import argparse
import io
import json
import subprocess
import time
import uuid
from pathlib import Path

import paramiko
import pycdlib

ROOT = Path(__file__).resolve().parents[1]
VM = ROOT / "output/m4-step7/vm"


def prepare(name, port, https):
    folder = VM / name
    folder.mkdir()
    key = paramiko.RSAKey.generate(3072)
    key.write_private_key_file(str(folder / "id_rsa"))
    cloud = f"""#cloud-config
hostname: {name}
ssh_pwauth: false
disable_root: false
users:
  - name: m4qa
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - {key.get_name()} {key.get_base64()}
write_files:
  - path: /etc/ssh/sshd_config.d/80-m4.conf
    content: |
      PasswordAuthentication no
      PermitRootLogin no
runcmd:
  - [sh, -c, "echo m4-cloud-ready > /home/m4qa/cloud-ready"]
"""
    disk = folder / "disk.qcow2"
    subprocess.run([str(VM / "qemu/qemu-img.exe"), "create", "-f", "qcow2", "-F", "qcow2",
                    "-b", str(VM / "debian.qcow2"), str(disk), "40G"], check=True)
    identity = str(uuid.uuid4())
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09", vol_ident="CIDATA")
    for filename, body in (("user-data", cloud), ("meta-data", f"instance-id: {identity}\nlocal-hostname: {name}\n")):
        data = body.encode()
        iso.add_fp(io.BytesIO(data), len(data), iso_path="/" + filename.upper().replace("-", "_") + ";1",
                   rr_name=filename, joliet_path="/" + filename)
    iso.write(str(folder / "seed.iso"))
    iso.close()
    arguments = ["-name", name, "-uuid", identity, "-machine", "q35", "-accel", "whpx,kernel-irqchip=off",
                 "-cpu", "max", "-smp", "2", "-m", "3072", "-display", "none", "-serial", f"file:{folder / 'serial.log'}",
                 "-drive", f"file={disk},format=qcow2,if=virtio",
                 "-drive", f"file={folder / 'seed.iso'},format=raw,if=virtio,readonly=on",
                 "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22,hostfwd=tcp:127.0.0.1:{https}-:443",
                 "-device", "virtio-net-pci,netdev=net0"]
    (folder / "vm.json").write_text(json.dumps({"name": name, "uuid": identity, "ssh": port, "https": https,
                                               "arguments": arguments}, indent=2), encoding="utf-8")
    return folder


def connect(name):
    folder = VM / name
    config = json.loads((folder / "vm.json").read_bytes())
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    known = folder / "known_hosts"
    if known.exists():
        client.load_host_keys(str(known))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect("127.0.0.1", port=config["ssh"], username="m4qa", key_filename=str(folder / "id_rsa"),
                   timeout=20, banner_timeout=30)
    client.save_host_keys(str(known))
    return client


def main(log_folder=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "exec", "put", "get"))
    parser.add_argument("name")
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    if args.mode == "prepare":
        print(prepare(args.name, *map(int, args.arguments)))
        return
    client = connect(args.name)
    try:
        if args.mode == "exec":
            started = time.monotonic()
            stdin, stdout, stderr = client.exec_command(args.arguments[0], timeout=1800)
            result = stdout.read().decode(errors="replace")
            error = stderr.read().decode(errors="replace")
            code = stdout.channel.recv_exit_status()
            folder = log_folder or VM / args.name
            stamp = str(time.time_ns())
            (folder / f"command-{stamp}.log").write_text(result + error, encoding="utf-8")
            with (folder / "commands.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps({"command": args.arguments[0], "exit": code,
                    "seconds": round(time.monotonic() - started, 3), "output": f"command-{stamp}.log"}) + "\n")
            print(result, end="")
            print(error, end="")
            print(f"\nSSH exit={code}")
            raise SystemExit(code)
        with client.open_sftp() as sftp:
            if args.mode == "put":
                sftp.put(args.arguments[0], args.arguments[1])
            else:
                sftp.get(args.arguments[0], args.arguments[1])
    finally:
        client.close()


if __name__ == "__main__":
    main()
