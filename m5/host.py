import argparse
import importlib.util
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from m4 import qa_vm

ASSETS = qa_vm.VM
OUT = ROOT / "output/m5-step8"
VM = OUT / "vm"
NAME = "drawing-step8-qa"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "configure", "start", "exec", "put", "get"))
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    if args.action == "configure":
        values = dict(line.split("=", 1) for line in (ROOT / "output/m4-step7/deploy.env").read_text(encoding="utf-8").splitlines()
                      if line.strip() and not line.startswith("#") and "=" in line)
        values.update(MYSQL_PASSWORD=secrets.token_urlsafe(24), MYSQL_ROOT_PASSWORD=secrets.token_urlsafe(24),
                      MYSQL_SERVER_ID="81", CAD_STATE="/var/lib/drawing-platform/m5-cad", M5_EVIDENCE="/opt/m5-evidence")
        (OUT / "deploy.env").write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
        print("Independent QA credentials prepared; values omitted")
        return
    if args.action == "prepare":
        spec = importlib.util.spec_from_file_location("m5_vm_prepare", ASSETS.parents[2] / "m4/qa_vm.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.VM = VM
        original_run = module.subprocess.run

        def create(command, **kwargs):
            command[0] = str(ASSETS / "qemu/qemu-img.exe")
            command[command.index("-b") + 1] = str(ASSETS / "debian.qcow2")
            return original_run(command, **kwargs)

        VM.mkdir(parents=True)
        module.subprocess.run = create
        try:
            module.prepare(NAME, 2229, 8449)
        finally:
            module.subprocess.run = original_run
        return
    folder = VM / NAME
    qa_vm.VM = VM
    if args.action == "start":
        config = json.loads((folder / "vm.json").read_bytes())
        subprocess.Popen([str(ASSETS / "qemu/qemu-system-x86_64.exe"), *config["arguments"]],
                         creationflags=subprocess.CREATE_NO_WINDOW, stdout=(folder / "qemu.log").open("ab"), stderr=subprocess.STDOUT)
        return
    sys.argv = [sys.argv[0], args.action, NAME, *args.arguments]
    qa_vm.main(Path(os.environ["M5_HOST_LOG_DIR"]) if os.environ.get("M5_HOST_LOG_DIR") else None)


if __name__ == "__main__":
    main()
