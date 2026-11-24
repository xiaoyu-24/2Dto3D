import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m4.compose import Deployment, ROOT


def data(deployment, folder, action):
    state = deployment.settings()["CAD_STATE"]
    result = deployment.run("run", "--no-deps", "-T", "-e", "M4_QA=1", "-v", f"{ROOT / 'm4'}:/qa:ro",
                            "-v", f"{folder}:/qa-state", "-v", f"{state}:/state",
                            "api", "python", "/qa/qa_data.py", action)
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("history", "fixtures", "audit", "versions", "snapshot", "seal", "resume-audit"))
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--project", default="drawing-m4-history")
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--sql", type=Path)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    folder = args.folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o777)
    deployment = Deployment(args.env, args.project, folder / "commands.jsonl")
    state = Path(deployment.settings()["CAD_STATE"])
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o777)
    if args.action == "history":
        deployment.run("up", "-d", "--wait", "mysql")
        assert int(deployment.sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='drawings'").strip()) == 0
        deployment.run("exec", "-T", "mysql", "sh", "-ec",
                       'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --max-allowed-packet=256M drawings',
                       body=args.sql.read_bytes())
        deployment.run("run", "--no-deps", "-T", "migrate")
        result = data(deployment, folder, "audit")
    elif args.action == "fixtures":
        if not (folder / "fixtures.json").exists():
            data(deployment, folder, "seed")
        result = data(deployment, folder, "prepare")
        for item in result["items"]:
            work = folder / item["label"]
            output = work / "verify"
            output.mkdir(mode=0o777, exist_ok=True)
            os.chmod(output, 0o777)
            verified = deployment.run("run", "--no-deps", "-T", "-v", f"{work}:/work:ro", "-v", f"{output}:/out",
                                     "step-check", "/work/model.step", "/work/checklist.json", "/work/geometry.json",
                                     "/out/step-check.json", timeout=300)
            (work / "verify.log").write_bytes(verified.stdout + verified.stderr)
            (work / "step-check.json").write_bytes((output / "step-check.json").read_bytes())
        result = data(deployment, folder, "stage")
    elif args.action in ("snapshot", "seal"):
        if args.action == "seal":
            data(deployment, folder, "replay-marker")
        command = [sys.executable, str(ROOT / "m4/recovery.py"), args.action, "--env", str(args.env),
                   "--project", args.project, "--backup", str(args.backup)]
        subprocess.run(command, check=True)
        result = data(deployment, folder, "audit")
    else:
        result = data(deployment, folder, args.action)
    target = folder / f"{args.action}.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"action": args.action, "record": str(target)}))


if __name__ == "__main__":
    main()
