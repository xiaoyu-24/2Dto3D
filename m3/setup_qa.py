import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = "drawing-step6-qa"


def command(project, *arguments, body=None):
    args = ["docker", "compose", "-p", project, *arguments]
    result = subprocess.run(args, cwd=ROOT, input=body, capture_output=True)
    print(json.dumps({"command": args, "exit": result.returncode}))
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-1000:])
    return result.stdout


def main():
    output = ROOT / "output" / "m3-step6"
    output.mkdir(parents=True, exist_ok=True)
    command(TARGET, "up", "-d", "--wait", "mysql")
    count = command(TARGET, "exec", "-T", "mysql", "sh", "-ec",
                    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=\'drawings\'"')
    if int(count.strip()):
        raise RuntimeError("QA database is not empty; retained without import")
    common = 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --single-transaction --no-tablespaces --skip-add-drop-table --hex-blob '
    data = command("drawing-step5-qa", "exec", "-T", "mysql", "sh", "-ec",
                   common + "--ignore-table=drawings.user_session drawings")
    sessions = command("drawing-step5-qa", "exec", "-T", "mysql", "sh", "-ec",
                       common + "--no-data drawings user_session")
    (output / "baseline.sql").write_bytes(data + sessions)
    command(TARGET, "exec", "-T", "mysql", "sh", "-ec",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot drawings', body=data + sessions)
    print(json.dumps({"baseline": "9fdc167", "bytes": len(data + sessions)}))


if __name__ == "__main__":
    main()
