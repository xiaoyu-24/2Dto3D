import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m4.compose import Deployment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--project", default="drawing-m4")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    deployment = Deployment(args.env, args.project)
    ids = deployment.run("ps", "-a", "-q").stdout.decode().split()
    containers = json.loads(subprocess.check_output(["docker", "inspect", *ids]))
    inventory = {"platform": platform.platform(), "machine_id": Path("/etc/machine-id").read_text().strip(),
                 "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                 "memory": Path("/proc/meminfo").read_text().splitlines()[:3],
                 "cpu_count": __import__("os").cpu_count(),
                 "docker_id": subprocess.check_output(["docker", "info", "--format", "{{.ID}}"]).decode().strip(),
                 "containers": []}
    for item in containers:
        config, host = item["Config"], item["HostConfig"]
        entry = {"name": item["Name"], "image": item["Image"], "created": item["Created"], "state": item["State"],
                 "user": config["User"], "root_read_only": host["ReadonlyRootfs"], "network_mode": host["NetworkMode"],
                 "port_bindings": host["PortBindings"], "cap_drop": host["CapDrop"], "security_opt": host["SecurityOpt"],
                 "pids": host["PidsLimit"], "memory": host["Memory"], "mounts": [
                     {key: mount[key] for key in ("Type", "Source", "Destination", "RW")} for mount in item["Mounts"]]}
        inventory["containers"].append(entry)
        service = config["Labels"]["com.docker.compose.service"]
        if service in ("cad", "step-check", "html-check"):
            assert config["User"] == "65534:65534" and host["ReadonlyRootfs"] and host["NetworkMode"] == "none"
            assert host["CapDrop"] == ["ALL"] and "no-new-privileges:true" in host["SecurityOpt"]
            assert not any(mount["Destination"] == "/var/run/docker.sock" for mount in item["Mounts"])
        if service != "web":
            assert not host["PortBindings"]
    inventory["database"] = deployment.sql("SELECT @@version,@@max_allowed_packet,@@server_uuid,@@server_id,@@log_bin,@@binlog_format,@@sync_binlog,@@innodb_flush_log_at_trx_commit; SELECT version_num FROM drawings.alembic_version;").decode()
    args.output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Inventory and isolation verified: {len(containers)} containers")


if __name__ == "__main__":
    main()
