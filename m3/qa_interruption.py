import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m2.cad_worker import ROOT, write_json
from m3.qa_cad import PROJECT, database

COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", "compose.yaml", "-f", "compose.m3-qa.yaml"]
OUTPUT = ROOT / "output" / "m3-step6"


def run(*arguments, body=None):
    result = subprocess.run(list(arguments), cwd=ROOT, input=body, capture_output=True)
    print(json.dumps({"command": arguments, "exit": result.returncode}), flush=True)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout


def wait_for(stage_id, predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stage = database("stage", stage_id=stage_id)
        if predicate(stage):
            return stage
        time.sleep(1)
    raise AssertionError(f"Timed out waiting for stage {stage_id}")


def main():
    run(*COMPOSE, "stop", "worker")
    reading = database("reading")
    config = json.dumps({"reply": json.dumps(reading)})
    code = "import sys,urllib.request; urllib.request.urlopen(urllib.request.Request('http://simulator:8011/configure', data=sys.stdin.buffer.read(), headers={'Content-Type':'application/json'})).read()"
    run(*COMPOSE, "exec", "-T", "api", "python", "-c", code, body=config.encode())
    seeded = database("interruption_seed")
    run(*COMPOSE, "run", "-d", "--no-deps", "--name", f"{PROJECT}-interrupted",
        "-e", "M1_HARNESS_URL=http://simulator:8011", "worker", "python", "/qa/qa_worker.py")
    checkpoint = wait_for(seeded["stage_id"], lambda stage: stage["output_sha"] is not None, 40)
    renewed = wait_for(seeded["stage_id"], lambda stage: stage["lease_until"] > checkpoint["lease_until"], 35)
    run("docker", "kill", "--signal", "KILL", f"{PROJECT}-interrupted")
    print("Checkpoint and normal renewal observed; waiting for natural lease expiry", flush=True)
    run(*COMPOSE, "run", "-d", "--no-deps", "--name", f"{PROJECT}-replacement",
        "-e", "M1_HARNESS_URL=http://simulator:8011", "worker", "python", "-m", "app.worker")
    recovered = wait_for(seeded["stage_id"], lambda stage: stage["state"] == "done", 125)
    assert recovered["fence"] == checkpoint["fence"] + 1 and recovered["attempts"] == 2
    old = {"id": checkpoint["id"], "task_id": checkpoint["task_id"], "fence": checkpoint["fence"],
           "owner": bytes.fromhex(checkpoint["owner"]).decode(), "input_sha": checkpoint["input_sha"]}
    assert database("stale_read", stage=old) is False
    assert database("stage", stage_id=seeded["stage_id"])["output_sha"] == recovered["output_sha"]
    audit = database("audit")
    events = [item for item in audit["stage_event"] if item["stage_id"] == seeded["stage_id"]]
    assert sum(item["kind"] == "evidence_saved" for item in events) == 1
    assert sum(item["kind"] == "reclaimed" for item in events) == 1
    calls = [item for item in audit["m1_call"] if item["stage_id"] == seeded["stage_id"]]
    assert [item["phase"] for item in calls] == ["probe", "read"]
    write_json(OUTPUT / "worker-interruption.json", {"seed": seeded, "checkpoint": checkpoint, "renewed": renewed,
               "recovered": recovered, "events": events, "calls": calls, "stale_checkpoint_rejected": True})
    run("docker", "stop", f"{PROJECT}-replacement")
    run(*COMPOSE, "start", "worker")
    print("Worker interruption, renewal, natural expiry, checkpoint reuse and stale fence: PASS", flush=True)


if __name__ == "__main__":
    main()
