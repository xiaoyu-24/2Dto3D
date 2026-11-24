import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m4.executor import LinuxExecutor
from m5.runtime import Deployment, EVIDENCE, PROJECT, save


def remote(service, payload):
    command = "import sys,json,urllib.request; print(urllib.request.urlopen(urllib.request.Request('http://" + service + ":8011/configure',data=sys.stdin.buffer.read(),headers={'Content-Type':'application/json'})).read().decode())"
    Deployment().run("exec", "-T", "api", "python", "-c", command, body=json.dumps(payload).encode())


def wait_stage(stage_id, predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stage = Deployment().data("m3", request={"action": "stage", "stage_id": stage_id})
        if predicate(stage):
            return stage
        time.sleep(1)
    raise AssertionError(f"Timed out waiting for stage {stage_id}")


class FaultExecutor(LinuxExecutor):
    def __init__(self, fault=None):
        super().__init__(Deployment())
        self.fault = fault

    def mysql(self, action, stage=None, **arguments):
        if action == "selection":
            result = self.deployment.run("exec", "-T", "-e", "M1_HARNESS_URL=http://cad-simulator:8011", "worker",
                "python", "-m", "app.m2_cad", body=json.dumps({"action": action, "stage": stage, **arguments}).encode())
            return json.loads(result.stdout)["result"]
        if action == "step" and self.fault == "before-commit":
            return self.deployment.data("m3", request={"action": "before_commit", "stage": stage, **arguments})
        result = super().mysql(action, stage, **arguments)
        if action == "step" and self.fault == "lost-receipt":
            print("Publication committed; deliberate process exit before local receipt", flush=True)
            os._exit(83)
        return result


def prepare(label):
    deployment = Deployment()
    reading = deployment.data("m3", request={"action": "reading"})
    remote("interruption-simulator", {"reply": json.dumps(reading)})
    remote("cad-simulator", {"reply": "m12-a04-m16-a0"})
    interrupted = deployment.data("interruption_seed")
    seeded = {name: deployment.data("m3", request={"action": "seed", "label": f"m5-{label}-{name}", "origin": 8})
              for name in ("step-failure", "lost-receipt")}
    deployment.run("run", "-d", "--no-deps", "--name", f"{PROJECT}-interrupted-{label}", "-e",
                   "M1_HARNESS_URL=http://interruption-simulator:8011", "-e", f"M1_VISION_PROJECT_ID={interrupted['project_id']}",
                   "worker", "python", "/m5/interrupted.py", str(interrupted["stage_id"]))
    checkpoint = wait_stage(interrupted["stage_id"], lambda stage: stage["output_sha"] is not None, 60)
    save(EVIDENCE / "fault-prepared.json", {"interrupted": interrupted, "checkpoint": checkpoint, "cad": seeded,
                                          "prepared_unix": time.time(), "label": label})
    print("Paused real Worker after persisted checkpoint; CAD fault tasks queued", flush=True)


def cad(fault):
    executor = FaultExecutor(fault)
    executor.output.mkdir(parents=True, exist_ok=True)
    executor.recover_receipts()
    fixture = json.loads((EVIDENCE / "fault-prepared.json").read_bytes())["cad"]
    target = fixture["lost-receipt" if fault == "lost-receipt" else "step-failure"]
    stage = executor.deployment.data("claim_fixture", stage_id=target["stage_id"])
    assert stage and stage["task_id"] == target["task_id"], target
    executor.process(stage, None, "M12A04-03-301-M16" if fault == "step-export" else None, 99)


def child(fault, expected=0):
    started = time.monotonic()
    command = [sys.executable, str(Path(__file__)), "cad", "--fault", fault]
    result = subprocess.run(command, capture_output=True, timeout=600)
    (EVIDENCE / f"cad-{fault}-{time.time_ns()}.log").write_bytes(result.stdout + result.stderr)
    record = {"command": command, "exit": result.returncode, "seconds": time.monotonic() - started}
    save(EVIDENCE / f"cad-command-{fault}-{time.time_ns()}.json", record)
    assert result.returncode == expected, record
    return record


def task_audit(inventory, task_id):
    stages = {item["id"] for item in inventory["task_stage"] if item["task_id"] == task_id}
    versions = {item["id"] for item in inventory["m2_version"] if item["task_id"] == task_id}
    return {"task": [item for item in inventory["product_task"] if item["id"] == task_id],
            "stages": [item for item in inventory["task_stage"] if item["id"] in stages],
            "versions": [item for item in inventory["m2_version"] if item["id"] in versions],
            "events": [item for item in inventory["stage_event"] if item["stage_id"] in stages],
            "calls": [item for item in inventory["m1_call"] if item["stage_id"] in stages],
            "receipts": [item for item in inventory["m3_publication"] if item["version_id"] in versions]}


def run():
    deployment = Deployment()
    fixture = json.loads((EVIDENCE / "fault-prepared.json").read_bytes())
    started = time.time()
    renewed = wait_stage(fixture["interrupted"]["stage_id"],
                         lambda stage: stage["lease_until"] > fixture["checkpoint"]["lease_until"], 40)
    result = subprocess.run(["docker", "kill", "--signal", "KILL", f"{PROJECT}-interrupted-{fixture['label']}"], capture_output=True)
    assert result.returncode == 0
    progress_before = deployment.data("stats")
    matrix = deployment.run("exec", "-T", "api", "python", "/qa/qa_models.py")
    (EVIDENCE / "model-matrix.json").write_bytes(matrix.stdout)
    discovery = deployment.run("exec", "-T", "api", "python", "/qa/qa_discovery.py")
    (EVIDENCE / "discovery.json").write_bytes(discovery.stdout)
    operations = [child("step-export"), child("lost-receipt", 83)]
    before = deployment.data("inventory")
    partial = task_audit(before, fixture["cad"]["step-failure"]["task_id"])
    assert partial["task"][0]["state"] == "partial" and not partial["receipts"]
    assert any("QA 故障注入：STEP" in (stage["error_text"] or "") for stage in partial["stages"])
    restarted = subprocess.run([sys.executable, str(Path(__file__)), "recover"], capture_output=True)
    assert restarted.returncode == 0
    (EVIDENCE / "lost-receipt-recovery.log").write_bytes(restarted.stdout + restarted.stderr)
    after = deployment.data("inventory")
    lost_task = fixture["cad"]["lost-receipt"]["task_id"]
    assert task_audit(before, lost_task) == task_audit(after, lost_task)
    task = fixture["cad"]["step-failure"]["task_id"]
    assert deployment.data("retry", task_id=task) == {"retried": [task]}
    operations.append(child("before-commit"))
    failed = deployment.data("inventory")
    rolled_back = task_audit(failed, task)
    assert not rolled_back["receipts"] and rolled_back["task"][0]["state"] == "partial"
    assert any("QA transaction write failure" in (stage["error_text"] or "") for stage in rolled_back["stages"])
    assert deployment.data("retry", task_id=task) == {"retried": [task]}
    command_offset = len((EVIDENCE / "commands.jsonl").read_text().splitlines())
    operations.append(child("normal"))
    resume_commands = [json.loads(line) for line in (EVIDENCE / "commands.jsonl").read_text().splitlines()[command_offset:]]
    assert not any("run" in entry["command"] and any(service in entry["command"] for service in ("cad", "step-check", "html-check"))
                   for entry in resume_commands)
    final = deployment.data("inventory")
    assert task_audit(failed, task)["calls"] == task_audit(final, task)["calls"]
    recovered = wait_stage(fixture["interrupted"]["stage_id"], lambda stage: stage["state"] == "done", 140)
    checkpoint = fixture["checkpoint"]
    assert recovered["fence"] == checkpoint["fence"] + 1 and recovered["attempts"] == 2
    old = {key: checkpoint[key] for key in ("id", "task_id", "fence", "input_sha")}
    old["owner"] = bytes.fromhex(checkpoint["owner"]).decode()
    assert deployment.data("m3", request={"action": "stale_read", "stage": old}) is False
    current = deployment.data("inventory")
    events = [entry for entry in current["stage_event"] if entry["stage_id"] == recovered["id"]]
    assert sum(entry["kind"] == "evidence_saved" for entry in events) == 1
    assert sum(entry["kind"] == "reclaimed" for entry in events) == 1
    task_calls = [entry for entry in current["m1_call"] if entry["stage_id"] == recovered["id"]]
    assert [entry["phase"] for entry in task_calls] == ["probe", "read"]
    progress_after = deployment.data("stats")
    assert progress_after["max_event"] > progress_before["max_event"]
    for item in fixture["cad"].values():
        publication = [entry for entry in current["m3_publication"] if entry["version_id"] == item["id"]]
        assert len(publication) == 1
        assert sum(entry["stage_id"] == item["stage_id"] and entry["kind"] == "published" for entry in current["stage_event"]) == 1
    load_tasks = {entry["task_id"] for entry in json.loads((EVIDENCE / "upload.json").read_bytes())["receipts"]}
    load_stages = {stage["id"] for stage in current["task_stage"] if stage["task_id"] in load_tasks}
    during = [entry for entry in current["stage_event"] if entry["stage_id"] in load_stages
              and progress_before["max_event"] < entry["id"] <= progress_after["max_event"] and entry["kind"] == "shared_read_completed"]
    assert during, "No original load file finished during fault exercise"
    executor = FaultExecutor()
    failed_stage = fixture["cad"]["step-failure"]["stage_id"]
    pending = sorted((executor.output / "pending").glob(f"{failed_stage}-*/*/step.json"))
    assert len(pending) == 2
    old_step = json.loads(pending[0].read_bytes())
    protected_bytes = pending[1].read_bytes()
    try:
        executor.submit("step", old_step["stage"], **old_step["arguments"])
    except ValueError as error:
        assert "租约" in str(error)
    else:
        raise AssertionError("Old CAD fence was accepted")
    assert pending[1].read_bytes() == protected_bytes
    save(EVIDENCE / "faults.json", {"started_unix": started, "ended_unix": time.time(), "operations": operations,
         "renewed": renewed, "recovered": recovered, "events": events, "checkpoint_reused": True,
         "stale_read_rejected": True, "lost_receipt_database_unchanged": True,
         "database_retry_model_call_delta": 0, "database_retry_cad_build_export_verify_delta": 0,
         "late_cad_rejected": True, "current_pending_unchanged": True, "resume_commands": resume_commands,
         "progress_before": progress_before, "progress_after": progress_after,
         "cad": fixture["cad"], "worker_calls": task_calls, "load_completions_during_faults": during,
         "step_partial": partial["stages"], "database_rollback": rolled_back["stages"], "receipt_recovery_exit": restarted.returncode})
    print("M5 fault matrix and publication recovery passed", flush=True)


def finish():
    deployment = Deployment()
    fixture = json.loads((EVIDENCE / "fault-prepared.json").read_bytes())
    initial = json.loads((EVIDENCE / "fault-prepared-initial.json").read_bytes())
    task = fixture["cad"]["step-failure"]["task_id"]
    parked = int(fixture["cad"]["lost-receipt"]["stage_id"])
    deployment.sql(f"USE drawings; UPDATE task_stage SET state='waiting',error_text='M5 earlier lost-receipt case retained as authority' WHERE id={parked} AND state='ready';")
    stage = deployment.data("m3", request={"action": "stage", "stage_id": fixture["cad"]["step-failure"]["stage_id"]})
    if stage["state"] == "running":
        assert stage["lease_until"] < stage["now"]
        child("step-export")
    assert deployment.data("retry", task_id=task) == {"retried": [task]}
    operations = [child("before-commit")]
    before = deployment.data("inventory")
    assert deployment.data("retry", task_id=task) == {"retried": [task]}
    command_offset = len((EVIDENCE / "commands.jsonl").read_text().splitlines())
    operations.append(child("normal"))
    resume_commands = [json.loads(line) for line in (EVIDENCE / "commands.jsonl").read_text().splitlines()[command_offset:]]
    assert not any("run" in entry["command"] and any(service in entry["command"] for service in ("cad", "step-check", "html-check"))
                   for entry in resume_commands)
    after = deployment.data("inventory")
    assert task_audit(before, task)["calls"] == task_audit(after, task)["calls"]
    recovered = deployment.data("m3", request={"action": "stage", "stage_id": fixture["interrupted"]["stage_id"]})
    checkpoint = fixture["checkpoint"]
    assert recovered["state"] == "done" and recovered["fence"] == checkpoint["fence"] + 1 and recovered["attempts"] == 2
    old = {key: checkpoint[key] for key in ("id", "task_id", "fence", "input_sha")}
    old["owner"] = bytes.fromhex(checkpoint["owner"]).decode()
    assert deployment.data("m3", request={"action": "stale_read", "stage": old}) is False
    events = [entry for entry in after["stage_event"] if entry["stage_id"] == recovered["id"]]
    assert sum(entry["kind"] == "evidence_saved" for entry in events) == sum(entry["kind"] == "reclaimed" for entry in events) == 1
    worker_calls = [entry for entry in after["m1_call"] if entry["stage_id"] == recovered["id"]]
    assert [entry["phase"] for entry in worker_calls] == ["probe", "read"]
    lost_task = initial["cad"]["lost-receipt"]["task_id"]
    restart = subprocess.run([sys.executable, str(Path(__file__)), "recover"], capture_output=True)
    assert restart.returncode == 0
    (EVIDENCE / "final-receipt-recovery.log").write_bytes(restart.stdout + restart.stderr)
    final = deployment.data("inventory")
    assert task_audit(after, lost_task) == task_audit(final, lost_task)
    executor = FaultExecutor()
    pending = sorted((executor.output / "pending").glob(f"{fixture['cad']['step-failure']['stage_id']}-*/*/step.json"))
    assert len(pending) == 2
    old_step = json.loads(pending[0].read_bytes())
    protected = pending[1].read_bytes()
    try:
        executor.submit("step", old_step["stage"], **old_step["arguments"])
    except ValueError as error:
        assert "租约" in str(error)
    else:
        raise AssertionError("Old CAD fence accepted")
    assert pending[1].read_bytes() == protected
    cad_cases = {"step-failure": fixture["cad"]["step-failure"], "lost-receipt": initial["cad"]["lost-receipt"]}
    for item in cad_cases.values():
        assert len([entry for entry in final["m3_publication"] if entry["version_id"] == item["id"]]) == 1
        assert len([entry for entry in final["stage_event"] if entry["stage_id"] == item["stage_id"] and entry["kind"] == "published"]) == 1
    matrix = json.loads((EVIDENCE / "model-matrix.json").read_bytes())
    assert set(matrix) == {"429", "503", "credential", "unqualified", "deadline", "pending", "budget"}
    for name in ("429", "503", "credential"):
        assert matrix[name]["result"]["http_status"] == 200
        assert any(call["route"] == "backup" and call["phase"] == "read" for call in matrix[name]["http_calls"])
    began = min(entry["created_at"] for entry in events)
    ended = max(entry["created_at"] for entry in events)
    progressing = [entry for entry in final["stage_event"] if began <= entry["created_at"] <= ended
                   and entry["stage_id"] != recovered["id"] and entry["kind"] in ("completed", "shared_read_completed")]
    assert progressing
    save(EVIDENCE / "faults.json", {"cad": cad_cases, "worker_fixture": fixture["interrupted"], "recovered": recovered,
         "checkpoint": checkpoint, "events": events, "worker_calls": worker_calls, "operations": operations,
         "checkpoint_reused": True, "stale_read_rejected": True, "late_cad_rejected": True,
         "current_pending_unchanged": True, "lost_receipt_database_unchanged": True,
         "database_retry_model_call_delta": 0, "database_retry_cad_build_export_verify_delta": 0,
         "resume_commands": resume_commands, "other_completions_during_takeover": progressing,
         "load_rounds": "initial CAD publication fault and corrected Worker interruption both occurred while original load was active; final DB retry audited after load",
         "retained_failures": ["initial route timeout and Worker simulator configuration", "untargeted QA checkpoint pause",
                               "corrected-round offline Edge timeout before lost-receipt injection"]})
    print("Per-case M5 fault evidence verified; retained failed rounds remain disclosed", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "cad", "recover", "finish"))
    parser.add_argument("--fault", default="normal")
    parser.add_argument("--label", default="final")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.label)
    elif args.action == "run":
        run()
    elif args.action == "cad":
        cad(args.fault)
    elif args.action == "finish":
        finish()
    else:
        FaultExecutor().recover_receipts()


if __name__ == "__main__":
    main()
