import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/m5-step8"


def main(destination):
    records = []
    for path in sorted(OUT.rglob("*")):
        relative = path.relative_to(OUT)
        if not path.is_file() or path.suffix == ".env" or path.name in ("evidence-sha256.json", "id_rsa", "key.pem"):
            continue
        if relative.parts[0] == "vm" and path.name != "commands.jsonl" and not path.name.startswith("command-"):
            continue
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        records.append({"path": relative.as_posix(), "bytes": path.stat().st_size, "sha256": digest})
    result = {"sealed_at": datetime.now(timezone.utc).isoformat(), "evidence_files": records,
              "acceptance_report": {"path": "m5/README.md", "bytes": (ROOT / "m5/README.md").stat().st_size,
                                    "sha256": hashlib.sha256((ROOT / "m5/README.md").read_bytes()).hexdigest()},
              "excluded": ["deployment credentials", "private keys", "VM disks and cloud-init media", "this self-referential index"],
              "step8_passed": False}
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Sealed {len(records)} evidence hashes; deployment secrets and VM disks excluded")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT / "evidence-sha256.json")
    main(parser.parse_args().output)
