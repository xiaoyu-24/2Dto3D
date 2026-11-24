import argparse
import fcntl
import json
import os
import signal
import socket
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m2.cad_worker import Executor, write_json
from m4.compose import Deployment


class LinuxExecutor(Executor):
    def __init__(self, deployment):
        self.deployment = deployment
        super().__init__(deployment.project, Path(deployment.settings()["CAD_STATE"]))
        if self.deployment.log is None:
            self.deployment.log = self.output / "commands.jsonl"

    def mysql(self, action, stage=None, **arguments):
        result = self.deployment.run("exec", "-T", "worker", "python", "-m", "app.m2_cad",
                                    body=json.dumps({"action": action, "stage": stage, **arguments}).encode(), check=False)
        if not result.stdout.strip():
            raise ValueError(result.stderr.decode(errors="replace")[-500:])
        response = json.loads(result.stdout)
        if result.returncode:
            raise ValueError(response.get("error", "CAD transaction failed"))
        return response["result"]

    def isolated(self, work, service, arguments):
        output = work / ("run-" + uuid.uuid4().hex)
        output.mkdir(mode=0o777)
        os.chmod(output, 0o777)
        result = self.deployment.run("run", "--no-deps", "-T", "-v", f"{work}:/work:ro",
                                    "-v", f"{output}:/out", service, *arguments, check=False, timeout=300)
        write_json(work / f"{service}-{output.name}.log.json",
                   {"exit": result.returncode, "stdout": result.stdout.decode(errors="replace"),
                    "stderr": result.stderr.decode(errors="replace")})
        for path in output.iterdir():
            path.rename(work / path.name)
        return result.returncode

    def cad(self, work, *arguments, allowed=(0,)):
        script, *parameters = arguments
        if script == "job.py":
            parameters[2] = "/out/frozen"
        else:
            parameters[-1] = "/out/" + Path(parameters[-1]).name
        service = "step-check" if script == "verify.py" else "cad"
        command = parameters if service == "step-check" else [script, *parameters]
        code = self.isolated(work, service, command)
        if code not in allowed:
            raise ValueError(f"Isolated {script} exited {code}")
        return code

    def verify_html(self, browser, work, components):
        output = work / ("edge-" + uuid.uuid4().hex)
        output.mkdir(mode=0o777)
        os.chmod(output, 0o777)
        result = self.deployment.run("run", "--no-deps", "-T", "-v", f"{work / 'model.html'}:/input/model.html:ro",
                                    "-v", f"{output}:/out", "html-check", str(len(components)), check=False, timeout=210)
        write_json(work / "html-check.log.json", {"exit": result.returncode,
                   "stdout": result.stdout.decode(errors="replace"), "stderr": result.stderr.decode(errors="replace")})
        if result.returncode:
            raise ValueError("Independent offline Edge verification failed")
        (work / "edge-offline.json").write_bytes((output / "edge-offline.json").read_bytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--project", default="drawing-m4")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    worker = LinuxExecutor(Deployment(args.env, args.project))
    worker.output.mkdir(parents=True, exist_ok=True)
    with (worker.output / "executor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stopped = threading.Event()
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stopped.set())
        worker.recover_receipts()
        owner = f"cad:{socket.gethostname()}:{uuid.uuid4()}"
        while not stopped.is_set() and not args.recover:
            try:
                stage = worker.mysql("claim", owner=owner)
                if stage:
                    worker.process(stage, None, None, 0)
                (worker.output / "heartbeat").touch()
            except (ValueError, OSError) as error:
                if args.once:
                    raise
                print(f"CAD queue unavailable: {error}", flush=True)
            if args.once:
                break
            stopped.wait(2)


if __name__ == "__main__":
    main()
