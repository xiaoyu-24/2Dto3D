import argparse
import base64
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAD = ROOT / "m1" / "cad"
sys.path.insert(0, str(ROOT))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def packed(path):
    return base64.b64encode(path.read_bytes()).decode()


class Executor:
    def __init__(self, project, output):
        self.project = project
        self.output = output.resolve()

    def mysql(self, action, stage=None, **arguments):
        request = {"action": action, "stage": stage, **arguments}
        response = subprocess.run(["docker", "compose", "-p", self.project, "exec", "-T", "worker", "python", "-m", "app.m2_cad"],
                                  cwd=ROOT, input=json.dumps(request).encode(), capture_output=True, timeout=150)
        if not response.stdout.strip():
            raise ValueError(response.stderr.decode(errors="replace")[-500:])
        payload = json.loads(response.stdout)
        if response.returncode:
            raise ValueError(payload.get("error", "CAD 检查点事务失败"))
        return payload["result"]

    def pending(self, stage, action):
        return self.output / "pending" / f"{stage['id']}-{stage['input_sha']}" / str(stage["fence"]) / f"{action}.json"

    def submit(self, action, stage, **arguments):
        path = self.pending(stage, action)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"stage": stage, "arguments": arguments}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return self.mysql(action, stage, **arguments)

    def restore(self, stage, action):
        directory = self.pending(stage, action).parent.parent
        candidates = []
        for path in [directory / f"{action}.json", *directory.glob(f"*/{action}.json")]:
            if not path.is_file():
                continue
            saved = json.loads(path.read_bytes())
            identity = saved["stage"]
            if (all(identity[key] == stage[key] for key in ("id", "task_id", "input_sha"))
                    and identity["fence"] <= stage["fence"]
                    and (path.parent == directory or path.parent.name == str(identity["fence"]))):
                candidates.append((identity["fence"], path.parent != directory, saved["arguments"]))
        return max(candidates, key=lambda item: item[:2])[2] if candidates else None

    def recover_receipts(self):
        directory = self.output / "pending"
        for path in [*directory.glob("*/step.json"), *directory.glob("*/*/step.json")]:
            try:
                saved = json.loads(path.read_bytes())
                body = base64.b64decode(saved["arguments"]["body"], validate=True)
                step_sha = hashlib.sha256(body).hexdigest()
                if saved["arguments"]["check"]["step_sha256"] != step_sha:
                    raise ValueError("暂存 STEP 指纹不符")
            except (ValueError, KeyError) as error:
                print(f"Skipped invalid pending STEP {path}: {error}", flush=True)
                continue
            receipt = self.mysql("receipt", saved["stage"], step_sha256=step_sha)
            if receipt:
                recovered = self.pending({**saved["stage"], "fence": receipt["fence"]}, "receipt")
                recovered.parent.mkdir(parents=True, exist_ok=True)
                write_json(recovered, receipt)
                print(f"Recovered publication receipt for task {saved['stage']['task_id']}", flush=True)

    def cad(self, work, *arguments, allowed=(0,)):
        command = ["docker", "compose", "-p", self.project, "-f", "compose.m0.yaml", "run", "--rm", "--no-deps", "-T",
                   "-v", f"{CAD.as_posix()}:/job:ro", "-v", f"{work.as_posix()}:/work", "-w", "/job", "cad",
                   "timeout", "240", "/opt/freecad/usr/bin/python", *arguments]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=260)
        log_name = f"{Path(arguments[0]).stem}-{'html' if arguments[-1].endswith('.html') else 'cad'}"
        write_json(work / f"{log_name}.log.json", {"command": command, "exit": result.returncode,
                   "stdout": result.stdout.decode(errors="replace"), "stderr": result.stderr.decode(errors="replace")})
        print(f"task {work.parent.name} {arguments[0]} exit={result.returncode}", flush=True)
        if result.returncode not in allowed:
            raise ValueError(f"隔离 CAD 作业 {arguments[0]} 退出 {result.returncode}")
        return result.returncode

    def verify_html(self, browser, work, components):
        from playwright.sync_api import Error as BrowserError
        from m1.qa_browser import offline

        try:
            offline(browser, work / "model.html", work, part_count=len(components))
        except BrowserError as error:
            raise ValueError(str(error)) from error

    def process(self, stage, browser, failure_part, failure_attempts):
        stopped = threading.Event()

        def heartbeat():
            while not stopped.wait(20):
                try:
                    if not self.mysql("renew", stage):
                        return
                except (ValueError, OSError, subprocess.SubprocessError):
                    return

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        work = self.output / str(stage["task_id"]) / f"{stage['fence']}-{uuid.uuid4().hex[:8]}"
        work.mkdir(parents=True)
        try:
            inputs = self.mysql("inputs", stage)
            pending_step = self.restore(stage, "step")
            if pending_step:
                result = self.submit("step", stage, **pending_step)
                write_json(work / "receipt.json", result)
                print(f"task {stage['task_id']} resumed persisted STEP: {result['state']}", flush=True)
                return
            for name in ("basis", "checklist"):
                (work / f"{name}.json").write_bytes(base64.b64decode(inputs[name], validate=True))
            if digest(work / "basis.json") != stage["input_sha"]:
                raise ValueError("建模输入字节不符")
            frozen = work / "frozen"
            snapshot = inputs["snapshot"]
            pending_frozen = self.restore(stage, "frozen")
            if not snapshot and pending_frozen:
                inputs["geometry_sha256"] = self.submit("frozen", stage, **pending_frozen)
                snapshot = pending_frozen["snapshot"]
            if snapshot:
                frozen.mkdir()
                for name, body in snapshot.items():
                    if Path(name).name != name or name in ("..", "."):
                        raise ValueError("快照文件名无效")
                    (frozen / name).write_bytes(base64.b64decode(body, validate=True))
                if digest(frozen / "geometry.json") != inputs["geometry_sha256"]:
                    raise ValueError("持久快照的几何指纹不符")
            else:
                chosen = self.mysql("selection", stage, job_sha256=digest(CAD / "job.py"))
                write_json(work / "selection.json", chosen)
                self.cad(work, "job.py", "/work/basis.json", "/work/selection.json", "/work/frozen", "nominal")
                manifest = json.loads((frozen / "geometry.json").read_bytes())
                snapshot = {name: packed(frozen / name) for name in ("geometry.json", *manifest["brep_sha256"])}
                snapshot.update({"job.py": packed(CAD / "job.py"), "selection.json": packed(work / "selection.json")})
                self.submit("frozen", stage, snapshot=snapshot)
            if not inputs["html_sha256"]:
                pending_html = self.restore(stage, "html")
                if pending_html:
                    self.submit("html", stage, **pending_html)
                else:
                    self.cad(work, "export.py", "/work/frozen", "-", "/work/model.html")
                    components = json.loads((frozen / "geometry.json").read_bytes())["components"]
                    self.verify_html(browser, work, components)
                    self.submit("html", stage, body=packed(work / "model.html"), check=json.loads((work / "edge-offline.json").read_bytes()))
            if inputs["part_number"] == failure_part and stage["attempts"] <= failure_attempts:
                raise ValueError("QA 故障注入：STEP 导出暂不可用（HTML 已独立验收）")
            self.cad(work, "export.py", "/work/frozen", "/work/model.step")
            code = self.cad(work, "verify.py", "/work/model.step", "/work/checklist.json", "/work/frozen/geometry.json",
                            "/work/step-check.json", allowed=(0, 1))
            if not (work / "step-check.json").is_file():
                raise ValueError(f"独立 STEP 回读未返回测量记录，退出码 {code}")
            result = self.submit("step", stage, body=packed(work / "model.step"), check=json.loads((work / "step-check.json").read_bytes()))
            write_json(work / "receipt.json", result)
            print(f"task {stage['task_id']} {inputs['version']} {result['state']}", flush=True)
        except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
            reason = str(error)[:1000] or type(error).__name__
            write_json(work / "failed.json", {"reason": reason})
            print(f"task {stage['task_id']} failed: {reason}", flush=True)
            try:
                self.mysql("fail", stage, reason=reason)
            except (ValueError, OSError, subprocess.SubprocessError) as failure:
                print(f"task {stage['task_id']} failure receipt unavailable: {failure}", flush=True)
        finally:
            stopped.set()
            thread.join()


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "m2-cad")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--qa-fail-step", help="QA 验收中注入 STEP 导出失败的料号")
    parser.add_argument("--qa-fail-attempts", type=int, default=1)
    args = parser.parse_args()
    if args.qa_fail_step and args.project not in ("drawing-step5-qa", "drawing-step6-qa"):
        parser.error("故障注入只允许使用独立的 Step 5／6 QA 项目")
    worker = Executor(args.project, args.output)
    owner = f"cad:{socket.gethostname()[:30]}:{uuid.uuid4()}"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True, args=["--use-angle=swiftshader",
            "--enable-unsafe-swiftshader", "--host-resolver-rules=MAP * ~NOTFOUND"])
        worker.recover_receipts()
        while True:
            try:
                stage = worker.mysql("claim", owner=owner)
                if stage:
                    worker.process(stage, browser, args.qa_fail_step, args.qa_fail_attempts)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                if args.once:
                    raise
                print(f"CAD 队列暂不可用：{error}", flush=True)
            if args.once:
                break
            time.sleep(2)
        browser.close()


if __name__ == "__main__":
    main()
