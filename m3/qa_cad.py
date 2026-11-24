import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m2.cad_worker import Executor, ROOT
from playwright.sync_api import sync_playwright

PROJECT = "drawing-step6-qa"


def database(action, **arguments):
    command = ["docker", "compose", "-p", PROJECT, "exec", "-T", "api", "python", "/qa/qa_db.py"]
    result = subprocess.run(command, cwd=ROOT, input=json.dumps({"action": action, **arguments}).encode(), capture_output=True, timeout=150)
    payload = json.loads(result.stdout)
    if result.returncode:
        raise ValueError(payload.get("error", result.stderr.decode(errors="replace")))
    return payload["result"]


class FaultExecutor(Executor):
    def __init__(self, output, fault):
        super().__init__(PROJECT, output)
        self.fault = fault

    def mysql(self, action, stage=None, **arguments):
        if action == "step" and self.fault == "before-commit":
            return database("before_commit", stage=stage, **arguments)
        result = super().mysql(action, stage, **arguments)
        if action == "step" and self.fault == "lost-receipt":
            print(f"QA committed task {stage['task_id']}; terminating before receipt is saved", flush=True)
            os._exit(83)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "run", "audit", "recover"))
    parser.add_argument("--label")
    parser.add_argument("--origin", type=int, default=8)
    parser.add_argument("--fault", choices=("before-commit", "lost-receipt", "step-export"))
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "m3-step6" / "cad")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.action == "seed":
        print(json.dumps(database("seed", label=args.label, origin=args.origin)))
        return
    if args.action == "audit":
        print(json.dumps(database("audit"), indent=2))
        return
    executor = FaultExecutor(args.output, args.fault)
    if args.action == "recover":
        executor.recover_receipts()
        return
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                                                                                "--host-resolver-rules=MAP * ~NOTFOUND"])
        stage = executor.mysql("claim", owner="m3-qa-cad")
        assert stage, "No ready CAD stage"
        executor.process(stage, browser, "M12A04-03-301-M16" if args.fault == "step-export" else None, 99)
        browser.close()


if __name__ == "__main__":
    main()
