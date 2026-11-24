import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*arguments):
    return subprocess.check_output(["git", *arguments], cwd=ROOT)


def capture(output):
    output.mkdir(parents=True, exist_ok=True)
    names = sorted(set(git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")) - {""})
    files = []
    archive = output / "source.tar"
    with tarfile.open(archive, "w") as bundle:
        for name in names:
            path = ROOT / name
            if not path.is_file():
                continue
            body = path.read_bytes()
            files.append({"path": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(body), 0o644, 0
            bundle.addfile(entry, io.BytesIO(body))
    manifest = {"captured_at": datetime.now(timezone.utc).isoformat(), "head": git("rev-parse", "HEAD").decode().strip(),
                "status": git("status", "--short", "--branch").decode(), "files": files,
                "source_tar_sha256": hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()}
    (output / "source.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "tracked.patch").write_bytes(git("diff", "--binary", "HEAD"))
    print(json.dumps({"files": len(files), "source_tar_sha256": manifest["source_tar_sha256"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    capture(parser.parse_args().output)
