import json
import os
import sys
from urllib.request import Request, urlopen

sys.path.insert(0, "/app")
from sqlalchemy import text
from app import m1_read, m3_models
from app.db import engine, one, rows
from app.m1_sample import encode_json
from qa_models import seed

PRIMARY = "http://simulator:8011"
BACKUP = "http://simulator-backup:8011"


def remote(url, path, payload=None):
    with urlopen(Request(url + path, data=encode_json(payload) if payload is not None else None,
                         headers={"Content-Type": "application/json"}), timeout=5) as response:
        return json.load(response)


def main():
    assert os.environ.get("M3_QA") == "1"
    os.environ["M1_HARNESS_URL"] = PRIMARY
    os.environ["M3_BACKUP_HARNESS_URL"] = BACKUP
    audits = []
    for case, status, available in (("primary_503", 503, True), ("primary_empty", 200, False)):
        remote(PRIMARY, "/configure", {"route_status": status, "route_available": available, "single_route": True})
        remote(BACKUP, "/configure", {})
        project, stage = seed(f"discovery-{case}")
        discovered = m1_read.harness("/route")
        route = discovered["route"]
        assert route["harness_url"] == BACKUP and route["route_id"] == "b2" * 32
        result = m3_models.call(stage, project, route, "read", {"prompt": "Resume original shared checkpoint", "images": []})
        assert result["http_status"] == 200
        primary = remote(PRIMARY, "/stats")
        backup = remote(BACKUP, "/stats")
        assert len(primary["discoveries"]) == len(backup["discoveries"]) == 1
        assert primary["calls"] == []
        assert [item["phase"] for item in backup["calls"]] == ["probe", "read"]
        assert m3_models.call(stage, project, route, "read", {}) == result
        assert remote(BACKUP, "/stats") == backup
        with engine.begin() as conn:
            checkpoint = one(conn, "SELECT output_sha FROM task_stage WHERE id=:id", id=stage["id"])["output_sha"]
            assert checkpoint == stage["output_sha"]
            calls = [dict(item) for item in rows(conn, "SELECT * FROM m1_call WHERE stage_id=:id", id=stage["id"])]
            events = [dict(item) for item in rows(conn, "SELECT * FROM stage_event WHERE stage_id=:id", id=stage["id"])]
            assert all(item["route_sha"] == bytes.fromhex("b2" * 32) for item in calls)
            routed = json.loads(next(item["detail"] for item in events if item["kind"] == "model_route_result"))
            assert routed["alternate"] and routed["discovery_fallback"]
            conn.execute(text("UPDATE task_stage SET state='done',lease_until=NULL WHERE id=:id"), {"id": stage["id"]})
        audits.append({"case": case, "task_id": stage["task_id"], "stage_id": stage["id"], "fence": stage["fence"],
                       "checkpoint_sha256": checkpoint.hex(), "discovered": discovered, "primary": primary, "backup": backup,
                       "calls": calls, "events": events, "repeat_added_calls": 0})
    remote(PRIMARY, "/configure", {})
    remote(BACKUP, "/configure", {})
    print(json.dumps(audits, indent=2, default=lambda value: value.hex() if isinstance(value, bytes) else str(value)))


if __name__ == "__main__":
    main()
