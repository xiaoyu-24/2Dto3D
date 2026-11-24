import hashlib
import json
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m5.runtime import Deployment, EVIDENCE, ROOT, save


def main():
    deployment = Deployment()
    save(EVIDENCE / "database.json", deployment.data("inventory"))
    ids = deployment.run("ps", "-a", "-q").stdout.decode().split()
    containers = json.loads(subprocess.check_output(["docker", "inspect", *ids]))
    inventory = []
    for container in containers:
        config, host = container["Config"], container["HostConfig"]
        service = config["Labels"]["com.docker.compose.service"]
        inventory.append({"name": container["Name"], "service": service, "image": container["Image"],
                          "state": container["State"], "user": config["User"], "read_only": host["ReadonlyRootfs"],
                          "network_mode": host["NetworkMode"], "ports": host["PortBindings"], "cap_drop": host["CapDrop"],
                          "security_opt": host["SecurityOpt"], "memory": host["Memory"], "pids": host["PidsLimit"],
                          "mounts": [{key: mount[key] for key in ("Source", "Destination", "RW")} for mount in container["Mounts"]]})
        if service in ("cad", "step-check", "html-check"):
            assert config["User"] == "65534:65534" and host["ReadonlyRootfs"] and host["NetworkMode"] == "none"
            assert host["CapDrop"] == ["ALL"] and "no-new-privileges:true" in host["SecurityOpt"]
        if service != "web":
            assert not host["PortBindings"]
    save(EVIDENCE / "containers.json", inventory)
    images = json.loads(subprocess.check_output(["docker", "image", "inspect", *sorted({entry["image"] for entry in inventory})]))
    save(EVIDENCE / "images.json", [{"id": image["Id"], "tags": image["RepoTags"], "digests": image["RepoDigests"],
                                   "os": image["Os"], "architecture": image["Architecture"],
                                   "labels": image["Config"].get("Labels", {})} for image in images])
    code = "import hashlib,json;from pathlib import Path;print(json.dumps({str(p.relative_to('/app')):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app').rglob('*') if p.is_file() and p.suffix in ('.py','.ini','.txt')}))"
    backend = json.loads(deployment.run("exec", "-T", "api", "python", "-c", code).stdout)
    harness = deployment.run("exec", "-T", "harness", "sha256sum", "/opt/probe/m1_service.py").stdout.decode().split()[0]
    save(EVIDENCE / "runtime-code.json", {"at": datetime.now(timezone.utc).isoformat(), "backend": backend,
         "harness_sha256": harness, "host": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
             for folder in ("m5", "m4", "m3", "m2", "m1/cad") for path in (ROOT / folder).rglob("*.py")}})
    logs = deployment.run("logs", "--no-color", "--timestamps", "worker", "harness", "api", check=False)
    (EVIDENCE / "service.log").write_bytes(logs.stdout + logs.stderr)
    cad = Path(deployment.settings()["CAD_STATE"])
    with tarfile.open(EVIDENCE / "cad-evidence.tar", "w") as archive:
        for path in sorted(cad.rglob("*")):
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(cad)), recursive=False)
    print(f"Collected {len(inventory)} isolated containers and {len(images)} image fingerprints", flush=True)


if __name__ == "__main__":
    main()
