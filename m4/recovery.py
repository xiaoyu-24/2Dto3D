import argparse
import fcntl
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m4.compose import Deployment


def fingerprint(path):
    with path.open("rb") as stream:
        return {"bytes": path.stat().st_size, "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}


def position(deployment):
    fields = deployment.sql("SHOW BINARY LOG STATUS").decode().split()
    return {"file": fields[0], "position": int(fields[1])}


def quiet(deployment):
    body = deployment.run("ps", "--format", "json").stdout.decode().strip()
    running = json.loads(body) if body.startswith("[") else [json.loads(line) for line in body.splitlines()]
    if any(item["Service"] in ("api", "worker", "web") and item["State"] == "running" for item in running):
        raise ValueError("Stop web, api and worker before the coordinated backup/restore")


def snapshot(deployment, folder):
    folder.mkdir()
    quiet(deployment)
    result = deployment.run("exec", "-T", "mysql", "sh", "-ec",
        'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -uroot --max-allowed-packet=256M --single-transaction '
        '--source-data=2 --set-gtid-purged=OFF --hex-blob --no-tablespaces --skip-add-drop-table '
        '--routines --events --triggers --databases drawings')
    dump = folder / "database.sql"
    dump.write_bytes(result.stdout)
    match = re.search(rb"CHANGE REPLICATION SOURCE TO SOURCE_LOG_FILE='([^']+)', SOURCE_LOG_POS=(\d+)", result.stdout)
    if not match:
        raise ValueError("Snapshot has no binlog position")
    meta = {"created_at": datetime.now(timezone.utc).isoformat(), "project": deployment.project,
            "snapshot": {"file": match[1].decode(), "position": int(match[2])},
            "database.sql": fingerprint(dump), "sealed": False}
    container = deployment.run("ps", "-a", "-q", "mysql").stdout.decode().strip()
    mounts = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]["Mounts"]
    volume = next(mount["Name"] for mount in mounts if mount["Destination"] == "/var/lib/mysql")
    deployment.run("stop", "-t", "120", "mysql")
    try:
        deployment.run("run", "--no-deps", "-T", "--user", "0:0", "-v", f"{volume}:/var/lib/mysql:ro",
                       "-v", f"{folder}:/backup", "--entrypoint", "tar", "mysql-tools",
                       "-cf", "/backup/mysql-data.tar", "--exclude=./auto.cnf", "-C", "/var/lib/mysql", ".")
        meta["mysql-data.tar"] = fingerprint(folder / "mysql-data.tar")
        meta["snapshot_method"] = "clean-shutdown physical InnoDB snapshot; logical SQL retained for portability only"
    finally:
        deployment.run("start", "--wait", "mysql")
    (folder / "backup.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def seal(deployment, folder, state):
    quiet(deployment)
    meta = json.loads((folder / "backup.json").read_bytes())
    if meta["sealed"]:
        raise ValueError("Backup is already sealed")
    meta["target"] = position(deployment)
    archive = folder / "cad-pending.tar"
    with tarfile.open(archive, "w") as stream:
        if (state / "pending").exists():
            stream.add(state / "pending", arcname="pending", recursive=True)
    meta["cad-pending.tar"] = fingerprint(archive)
    deployment.sql("FLUSH BINARY LOGS")
    logs = [line.split()[0] for line in deployment.sql("SHOW BINARY LOGS").decode().splitlines()]
    start, end = logs.index(meta["snapshot"]["file"]), logs.index(meta["target"]["file"])
    meta["binlogs"] = []
    for name in logs[start:end + 1]:
        if not re.fullmatch(r"binlog\.\d+", name):
            raise ValueError("Unexpected binlog name")
        path = folder / name
        deployment.run("cp", f"mysql:/var/lib/mysql/{name}", str(path))
        meta["binlogs"].append({"name": name, **fingerprint(path)})
    meta["sealed"] = True
    meta["sealed_at"] = datetime.now(timezone.utc).isoformat()
    (folder / "backup.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps({"snapshot": meta["snapshot"], "target": meta["target"], "binlogs": len(meta["binlogs"])}))


def restore(deployment, folder, state):
    started = time.monotonic()
    meta = json.loads((folder / "backup.json").read_bytes())
    if not meta["sealed"]:
        raise ValueError("Refusing an unsealed backup")
    for name, expected in [("database.sql", meta["database.sql"]), ("mysql-data.tar", meta["mysql-data.tar"]),
                           ("cad-pending.tar", meta["cad-pending.tar"]),
                           *((entry["name"], entry) for entry in meta["binlogs"])]:
        actual = fingerprint(folder / name)
        if any(actual[key] != expected[key] for key in actual):
            raise ValueError(f"Backup checksum mismatch: {name}")
    with tarfile.open(folder / "cad-pending.tar") as archive:
        for member in archive.getmembers():
            target = (state / member.name).resolve()
            if not target.is_relative_to(state / "pending") or not (member.isfile() or member.isdir()):
                raise ValueError("Unsafe path or link in CAD backup")
    quiet(deployment)
    if (state / "pending").exists():
        raise ValueError("Restore requires no existing pending directory")
    deployment.run("run", "--no-deps", "-T", "mysql-tools", "--version")
    deployment.run("create", "mysql")
    container = deployment.run("ps", "-a", "-q", "mysql").stdout.decode().strip()
    mounts = json.loads(subprocess.check_output(["docker", "inspect", container]))[0]["Mounts"]
    volume = next(mount["Name"] for mount in mounts if mount["Destination"] == "/var/lib/mysql")
    deployment.run("run", "--no-deps", "-T", "--user", "0:0", "-v", f"{volume}:/var/lib/mysql",
                   "-v", f"{folder}:/backup:ro", "--entrypoint", "sh", "mysql-tools", "-ec",
                   'test -z "$(ls -A /var/lib/mysql)" || { echo "Restore requires an empty MySQL volume" >&2; exit 1; }; '
                   'tar -xf /backup/mysql-data.tar -C /var/lib/mysql')
    deployment.run("up", "-d", "--wait", "mysql")
    for entry in meta["binlogs"]:
        name = entry["name"]
        options = ["--verify-binlog-checksum", "--disable-log-bin"]
        if name == meta["snapshot"]["file"]:
            options.append(f"--start-position={meta['snapshot']['position']}")
        if name == meta["target"]["file"]:
            options.append(f"--stop-position={meta['target']['position']}")
        replay = deployment.run("run", "--no-deps", "-T", "--user", "0:0", "-v", f"{folder}:/backup:ro",
                                "mysql-tools", *options, f"/backup/{name}")
        deployment.run("exec", "-T", "mysql", "sh", "-ec",
                       'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --max-allowed-packet=256M',
                       body=replay.stdout)
    with tarfile.open(folder / "cad-pending.tar") as archive:
        archive.extractall(state)
    deployment.run("run", "--no-deps", "-T", "migrate")
    record = {"restored_at": datetime.now(timezone.utc).isoformat(), "seconds": round(time.monotonic()-started, 3),
              "snapshot": meta["snapshot"], "target": meta["target"], "project": deployment.project,
              "temporary_task_directories_restored": False}
    (folder / f"restore-{deployment.project}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("snapshot", "seal", "restore"))
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--project", default="drawing-m4")
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    deployment = Deployment(args.env, args.project, args.backup.parent / "recovery-commands.jsonl")
    state = Path(deployment.settings()["CAD_STATE"]).resolve()
    state.mkdir(parents=True, exist_ok=True)
    with (state / "executor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "snapshot":
            snapshot(deployment, args.backup.resolve())
        elif args.action == "seal":
            seal(deployment, args.backup.resolve(), state)
        else:
            restore(deployment, args.backup.resolve(), state)


if __name__ == "__main__":
    main()
