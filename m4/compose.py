import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Deployment:
    def __init__(self, env, project="drawing-m4", log=None):
        self.env = Path(env).resolve()
        self.project = project
        self.log = Path(log) if log else None
        self.command = ["docker", "compose", "--env-file", str(self.env), "-p", project, "-f", str(ROOT / "m4/compose.yaml")]

    def run(self, *arguments, body=None, check=True, timeout=1800):
        started = time.monotonic()
        command = [*self.command, *arguments]
        result = subprocess.run(command, cwd=ROOT, input=body, capture_output=True, timeout=timeout)
        if self.log:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "command": command,
                                         "exit": result.returncode, "seconds": round(time.monotonic()-started, 3)}) + "\n")
        if check and result.returncode:
            detail = (result.stderr or result.stdout).decode(errors="replace")[-3000:]
            raise RuntimeError(f"Command exit {result.returncode}: {detail}")
        return result

    def settings(self):
        values = dict(line.split("=", 1) for line in self.env.read_text(encoding="utf-8-sig").splitlines()
                      if line.strip() and not line.startswith("#") and "=" in line)
        return {key: os.environ.get(key, value) for key, value in values.items()}

    def sql(self, statement):
        return self.run("exec", "-T", "mysql", "sh", "-ec",
                        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --max-allowed-packet=256M -N -B',
                        body=statement.encode()).stdout
