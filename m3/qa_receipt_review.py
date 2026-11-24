import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m2.cad_worker import Executor, ROOT, write_json
from m3.qa_cad import PROJECT, database


def restart_recovery(output):
    command = [sys.executable, "m3/qa_cad.py", "recover", "--output", str(output)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=90)
    write_json(output / "restart.json", {"command": command, "exit": result.returncode,
               "stdout": result.stdout.decode(errors="replace"), "stderr": result.stderr.decode(errors="replace")})
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return list(output.rglob("receipt.json"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "m3-review-fix" / "receipts")
    parser.add_argument("--evidence", type=Path, default=ROOT / "output" / "m3-receipt-review-f6ed050e")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = next(args.evidence.rglob("step.json"))
    original = evidence.read_bytes()
    saved = json.loads(original)
    assert saved["stage"]["task_id"] == 14 and saved["stage"]["fence"] == 2
    before = database("audit")
    actual = next(item for item in before["task_stage"] if item["id"] == saved["stage"]["id"])
    current = {key: actual[key] for key in ("id", "task_id", "input_sha", "fence", "attempts")}
    current["owner"] = bytes.fromhex(actual["owner"]).decode()
    assert current["fence"] == 3 and actual["state"] == "done"
    executor = Executor(PROJECT, args.output / "isolated")
    result = executor.submit("step", current, **saved["arguments"])
    assert result["reused"]
    protected = executor.pending(current, "step")
    payload_before = protected.read_bytes()
    try:
        executor.submit("step", saved["stage"], **saved["arguments"])
    except ValueError as error:
        assert "租约" in str(error)
    else:
        raise AssertionError("Late executor was allowed to publish")
    assert protected.read_bytes() == payload_before
    recovered = restart_recovery(executor.output)
    assert len(recovered) == 1
    receipt = json.loads(recovered[0].read_bytes())
    assert receipt["fence"] == 3 and receipt["report_sha256"] == result["report_sha256"]
    legacy = args.output / "legacy"
    target = legacy / evidence.relative_to(args.evidence)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(original)
    recovered_legacy = restart_recovery(legacy)
    assert len(recovered_legacy) == 1
    assert json.loads(recovered_legacy[0].read_bytes()) == receipt
    assert target.read_bytes() == evidence.read_bytes() == original
    after = database("audit")
    assert before == after
    write_json(args.output / "audit.json", {"task_id": 14, "stage_id": current["id"], "late_fence": 2, "published_fence": 3,
               "protected_pending_sha256": hashlib.sha256(payload_before).hexdigest(),
               "original_evidence_sha256": hashlib.sha256(original).hexdigest(), "receipt": receipt,
               "database_unchanged": True, "model_call_delta": 0, "publication_delta": 0, "stage_event_delta": 0})
    print("Task #14: fence 2 denied, fence 3 pending unchanged; fresh processes recover current and legacy receipts; database unchanged")


if __name__ == "__main__":
    main()
