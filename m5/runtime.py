import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m4.compose import Deployment as M4Deployment, ROOT

PROJECT = "drawing-step8-qa"
EVIDENCE = Path(os.environ.get("M5_EVIDENCE", "/opt/m5-evidence"))
ENV = Path("/etc/drawing-platform/m5.env")


class Deployment(M4Deployment):
    def __init__(self):
        super().__init__(ENV, PROJECT, EVIDENCE / "commands.jsonl")
        self.command += ["-f", str(ROOT / "m5/compose.yaml")]

    def data(self, action, **arguments):
        result = self.run("exec", "-T", "api", "python", "/m5/db.py", check=False,
                          body=json.dumps({"action": action, **arguments}).encode())
        payload = json.loads(result.stdout)
        if result.returncode:
            raise ValueError(payload.get("error", result.stderr.decode(errors="replace")))
        return payload["result"]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def deploy():
    deployment = Deployment()
    deployment.run("config", "--quiet")
    deployment.run("up", "-d", "--wait", "mysql")
    assert int(deployment.sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='drawings'").strip()) == 0
    deployment.run("exec", "-T", "mysql", "sh", "-ec",
                   'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --max-allowed-packet=256M drawings',
                   body=(Path("/opt/m5-bundle") / "history.sql").read_bytes())
    deployment.run("run", "--no-deps", "-T", "migrate")
    deployment.sql("USE drawings; UPDATE task_stage SET state='waiting',lease_until=NULL,error_text='M5 historical clone paused' WHERE state IN ('ready','running');")
    deployment.run("up", "-d", "--wait", "api", "web", "harness", "simulator", "simulator-backup",
                   "interruption-simulator", "cad-simulator")
    environment = {"hostname": platform.node(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
                   "machine_id": Path("/etc/machine-id").read_text().strip(),
                   "memory": Path("/proc/meminfo").read_text(), "disk": subprocess.check_output(["df", "-B1", "/"]).decode(),
                   "docker": subprocess.check_output(["docker", "version", "--format", "{{json .}} "]).decode(),
                   "compose": subprocess.check_output(["docker", "compose", "version", "--short"]).decode(),
                   "isolation": "separate Debian VM, engine, database volume and Compose networks; HTTPS only"}
    save(EVIDENCE / "environment.json", environment)
    save(EVIDENCE / "initial.json", deployment.data("inventory"))
    print("Independent Linux QA services ready; historical clone paused", flush=True)


def monitor():
    deployment = Deployment()
    with (EVIDENCE / "resources.jsonl").open("a", encoding="utf-8") as stream:
        while not (EVIDENCE / "stop-monitor").exists():
            started = time.monotonic()
            result = {"at": datetime.now(timezone.utc).isoformat(), "unix": time.time()}
            result["queue"] = deployment.data("stats")
            result["memory"] = Path("/proc/meminfo").read_text()
            result["loadavg"] = Path("/proc/loadavg").read_text().strip()
            stats = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}"], capture_output=True)
            result["docker_stats_exit"] = stats.returncode
            result["containers"] = [json.loads(line) for line in stats.stdout.decode().splitlines()]
            stream.write(json.dumps(result) + "\n")
            stream.flush()
            time.sleep(max(0, 3 - (time.monotonic() - started)))
    save(EVIDENCE / "final-inventory.json", deployment.data("inventory"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("deploy", "start", "monitor", "audit", "tests"))
    args = parser.parse_args()
    if args.action == "deploy":
        deploy()
    elif args.action == "start":
        Deployment().run("up", "-d", "--wait", "--scale", "worker=2", "worker")
    elif args.action == "monitor":
        monitor()
    elif args.action == "audit":
        save(EVIDENCE / "database.json", Deployment().data("inventory"))
    else:
        result = Deployment().run("run", "--no-deps", "-T", "tests", check=False)
        (EVIDENCE / "regression.log").write_bytes(result.stdout + result.stderr)
        print(result.stdout.decode(errors="replace"))
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
