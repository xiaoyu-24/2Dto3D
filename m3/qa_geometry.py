import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m1.qa_browser import offline
from m2.cad_worker import CAD, ROOT, Executor, digest, packed, write_json
from m3.plate_browser import verify as verify_plate_html
from m3.qa_cad import PROJECT, database
from playwright.sync_api import sync_playwright


def m12(executor, stage, work, browser, wrong):
    inputs = executor.mysql("inputs", stage)
    for name in ("basis", "checklist"):
        (work / f"{name}.json").write_bytes(base64.b64decode(inputs[name]))
    chosen = executor.mysql("selection", stage, job_sha256=digest(CAD / "job.py"))
    snapshot = database("snapshot", task_id=9 if wrong else 8)
    manifest = json.loads(base64.b64decode(snapshot["geometry.json"]))
    basis = json.loads((work / "basis.json").read_bytes())
    write_json(work / "selection.json", chosen)
    manifest.update(part_number=basis["part_number"], version=basis["version"], geometry_revision=inputs["geometry_revision"],
                    basis_sha256=digest(work / "basis.json"), job_sha256=digest(CAD / "job.py"),
                    selection_sha256=digest(work / "selection.json"))
    frozen = work / "frozen"
    frozen.mkdir()
    for name, value in snapshot.items():
        (frozen / name).write_bytes(base64.b64decode(value))
    write_json(frozen / "geometry.json", manifest)
    (frozen / "selection.json").write_bytes((work / "selection.json").read_bytes())
    (frozen / "job.py").write_bytes((CAD / "job.py").read_bytes())
    executor.submit("frozen", stage, snapshot={path.name: packed(path) for path in frozen.iterdir()})
    executor.cad(work, "export.py", "/work/frozen", "-", "/work/model.html")
    offline(browser, work / "model.html", work, part_count=len(manifest["components"]))
    executor.submit("html", stage, body=packed(work / "model.html"), check=json.loads((work / "edge-offline.json").read_bytes()))
    executor.cad(work, "export.py", "/work/frozen", "/work/model.step")
    code = executor.cad(work, "verify.py", "/work/model.step", "/work/checklist.json", "/work/frozen/geometry.json",
                        "/work/step-check.json", allowed=(0, 1))
    assert code == (1 if wrong else 0)
    check = json.loads((work / "step-check.json").read_bytes())
    if wrong:
        failed = [item["name"] for item in check["checks"] if not item["passed"]]
        assert "端面视向与触点数" in failed and "本体轴向总长" in failed
    return executor.submit("step", stage, body=packed(work / "model.step"), check=check)


def plate(executor, stage, work, browser, wrong):
    inputs = executor.mysql("inputs", stage)
    for name in ("basis", "checklist"):
        (work / f"{name}.json").write_bytes(base64.b64decode(inputs[name]))
    command = ["docker", "compose", "-p", PROJECT, "-f", "compose.m0.yaml", "run", "--rm", "--no-deps", "-T",
               "-v", f"{CAD.as_posix()}:/job:ro", "-v", f"{(ROOT / 'm3').as_posix()}:/qa:ro",
               "-v", f"{work.as_posix()}:/work", "-w", "/job", "cad", "/opt/freecad/usr/bin/python", "/qa/plate.py"]
    for arguments in (["build", "/work", "mirror" if wrong else "nominal"], ["verify", "/work"]):
        result = subprocess.run(command + arguments, capture_output=True, cwd=ROOT, timeout=120)
        write_json(work / f"plate-{arguments[0]}.log.json", {"command": command + arguments, "exit": result.returncode,
                   "stdout": result.stdout.decode(errors="replace"), "stderr": result.stderr.decode(errors="replace")})
        assert result.returncode == (1 if wrong and arguments[0] == "verify" else 0), result.stderr.decode(errors="replace")
    database("plate_freeze", stage=stage, geometry=packed(work / "geometry.json"))
    check = verify_plate_html(browser, work)
    executor.submit("html", stage, body=packed(work / "model.html"), check=check)
    step_check = json.loads((work / "step-check.json").read_bytes())
    assert step_check["passed"] is not wrong
    return executor.submit("step", stage, body=packed(work / "model.step"), check=step_check)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("m12", "plate"))
    parser.add_argument("--correct", action="store_true")
    args = parser.parse_args()
    output = ROOT / "output" / "m3-step6" / "geometry"
    output.mkdir(parents=True, exist_ok=True)
    seeded = database("seed", label="wrong-pins", origin=8) if args.kind == "m12" else database("plate_seed")
    if args.correct:
        revision = database("revision", task_id=seeded["task_id"])
        assert revision["geometry_revision"] == "G2"
    executor = Executor(PROJECT, output)
    stage = executor.mysql("claim", owner="m3-independent-geometry")
    assert stage["task_id"] == seeded["task_id"], stage
    work = output / f"{args.kind}-{'G2' if args.correct else 'G1'}"
    work.mkdir()
    write_json(work / "stage.json", stage)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader",
                                                                                "--host-resolver-rules=MAP * ~NOTFOUND"])
        result = (m12 if args.kind == "m12" else plate)(executor, stage, work, browser, not args.correct)
        assert result["state"] == ("published" if args.correct else "rejected")
        write_json(work / "receipt.json", result)
        print(json.dumps({"kind": args.kind, "task": stage["task_id"], "fence": stage["fence"], **result}))
        browser.close()


if __name__ == "__main__":
    main()
