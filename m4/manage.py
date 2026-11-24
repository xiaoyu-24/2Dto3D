import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m4.compose import Deployment, ROOT
from m4.snapshot import capture

MYSQL = "mysql:8.4@sha256:d36d39a64cd12a5c1cc9e6aa2bfb5f8d4c81a2f6586e0a04a9ae13939db02209"


def run(command, output, timeout=3600, cwd=ROOT):
    started = time.monotonic()
    with (output / "build.log").open("ab") as log:
        result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    with (output / "commands.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"command": command, "exit": result.returncode, "seconds": round(time.monotonic()-started, 3)}) + "\n")
    if result.returncode:
        raise RuntimeError(f"Command failed with {result.returncode}; see {output / 'build.log'}")


def build(output):
    if output.exists():
        raise ValueError("Build output must be new")
    capture(output)
    source_sha = json.loads((output / "source.json").read_bytes())["source_tar_sha256"]
    release = source_sha[:16]
    source = output / "source"
    source.mkdir()
    with tarfile.open(output / "source.tar") as archive:
        for member in archive.getmembers():
            if not (source / member.name).resolve().is_relative_to(source) or not member.isfile():
                raise ValueError("Unsafe source archive member")
        archive.extractall(source)
    run(["docker", "build", "--platform", "linux/amd64", "-t", "drawing-m4-cad-base:1.1.4", "m0/cad"], output, cwd=source)
    for service, dockerfile, context, target in (
        ("api", "m4/Dockerfile.api", ".", None), ("web", "m4/Dockerfile.web", ".", None),
        ("harness", "m0/harness/Dockerfile", "m0/harness", None),
        ("cad", "m4/Dockerfile.cad", ".", "cad"), ("step-check", "m4/Dockerfile.cad", ".", "step-check"),
        ("html-check", "m4/Dockerfile.html", ".", None),
        ("mysql-tools", "m4/Dockerfile.mysql-tools", ".", None),
    ):
        command = ["docker", "build", "--platform", "linux/amd64", "-t", f"drawing-m4-{service}:{release}",
                   "-f", dockerfile, "--build-arg", f"SOURCE_SHA={source_sha}",
                   "--label", f"org.opencontainers.image.revision={source_sha}"]
        run([*command, *(["--target", target] if target else []), context], output, cwd=source)
    names = [f"drawing-m4-{service}:{release}" for service in ("api", "web", "harness", "cad", "step-check", "html-check", "mysql-tools")]
    names.append(MYSQL)
    images = json.loads(subprocess.check_output(["docker", "image", "inspect", *names]))
    manifest = {"release": release, "source_sha256": source_sha, "images": [
        {"name": name, "id": image["Id"], "repo_digests": image.get("RepoDigests", []),
         "os": image["Os"], "architecture": image["Architecture"], "labels": image["Config"].get("Labels", {})}
        for name, image in zip(names, images, strict=True)]}
    assert all(image["os"] == "linux" and image["architecture"] == "amd64" for image in manifest["images"])
    (output / "images.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Built release {release}; {len(names)} locked images")


def pack(output):
    manifest = json.loads((output / "images.json").read_bytes())
    destination = output / "images.tar"
    if destination.exists():
        raise ValueError("Image archive already exists")
    run(["docker", "save", "-o", str(destination), *[image["name"] for image in manifest["images"]]], output)
    with destination.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "images.tar.sha256").write_text(f"{digest}  images.tar\n", encoding="utf-8")
    print(json.dumps({"image_archive_bytes": destination.stat().st_size, "sha256": digest}))


def verify(output):
    manifest = json.loads((output / "images.json").read_bytes())
    for expected in manifest["images"]:
        reference = expected["id"] if expected["name"].startswith("mysql:") else expected["name"]
        actual = json.loads(subprocess.check_output(["docker", "image", "inspect", reference]))[0]
        if actual["Id"] != expected["id"] or actual["Architecture"] != "amd64" or actual["Os"] != "linux":
            raise ValueError(f"Image mismatch: {expected['name']}")
    print("All image fingerprints verified")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("build", "pack", "verify", "init", "up", "migrate", "status"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env", type=Path)
    parser.add_argument("--project", default="drawing-m4")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.action in ("build", "pack", "verify"):
        {"build": build, "pack": pack, "verify": verify}[args.action](output)
        return
    if not args.env:
        parser.error("--env is required")
    verify(output)
    deployment = Deployment(args.env, args.project, output / "runtime-commands.jsonl")
    deployment.run("config", "--quiet")
    if args.action == "init":
        deployment.run("up", "-d", "--wait", "mysql")
        count = int(deployment.sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='drawings'").strip())
        if count:
            raise ValueError("Initialization requires an empty database")
        deployment.run("run", "--no-deps", "-T", "migrate")
        deployment.run("run", "--no-deps", "-T", "bootstrap")
    elif args.action == "migrate":
        deployment.run("run", "--no-deps", "-T", "migrate")
    elif args.action == "up":
        deployment.run("up", "-d", "--wait", "--wait-timeout", "360", "api", "worker", "web")
    print(deployment.run("ps", "--format", "json").stdout.decode())
    environment = {"platform": platform.platform(), "machine": platform.machine(),
                   "docker": subprocess.check_output(["docker", "version", "--format", "{{json .}}"]).decode(),
                   "compose": subprocess.check_output(["docker", "compose", "version", "--short"]).decode().strip()}
    (output / f"environment-{args.project}.json").write_text(json.dumps(environment, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
