import json
import os
import sys
import time
import uuid
from urllib.request import Request, urlopen

sys.path.insert(0, "/app")
from sqlalchemy import text
from app import m1_read, m3_models
from app.db import engine, one, rows
from app.m1_sample import encode_json
from app.m2_work import enqueue
from app.queue import event

assert os.environ.get("M3_QA") == "1"
os.environ["M1_HARNESS_URL"] = "http://simulator:8011"


def remote(path, payload=None):
    with urlopen(Request("http://simulator:8011" + path, data=encode_json(payload) if payload is not None else None,
                         headers={"Content-Type": "application/json"})) as response:
        return json.load(response)


def seed(name, budget=10):
    with engine.begin() as conn:
        project = conn.execute(text("INSERT INTO project(name) VALUES (:name)"), {"name": f"M3 isolated model {name}"}).lastrowid
        conn.execute(text("INSERT INTO project_member VALUES (:project,1,'operator')"), {"project": project})
        batch = conn.execute(text("INSERT INTO upload_batch (project_id,user_id,request_key) VALUES (:project,1,:key)"),
                             {"project": project, "key": uuid.uuid4().hex}).lastrowid
        source = conn.execute(text("""INSERT INTO source_file (batch_id,client_file_key,filename,sha,selection_json,state)
            SELECT :batch,'model','M3-model-matrix.pdf',sha,selection_json,state FROM source_file WHERE id=5"""), {"batch": batch}).lastrowid
        task = conn.execute(text("INSERT INTO product_task (source_id,scope_key,state,budget_cny) VALUES (:source,'file','reading',:budget)"),
                            {"source": source, "budget": budget}).lastrowid
        checkpoint = one(conn, "SELECT output_sha FROM task_stage WHERE task_id=5 AND stage_name='m2_read'")["output_sha"]
        stage = enqueue(conn, task, "m3_model_qa", checkpoint)
        conn.execute(text("""UPDATE task_stage SET state='running',owner='m3-model-qa',fence=1,attempts=1,output_sha=input_sha,
            lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 90 SECOND) WHERE id=:id"""), {"id": stage["id"]})
        return project, dict(one(conn, "SELECT * FROM task_stage WHERE id=:id", id=stage["id"]))


def main():
    matrix = {}
    for name, sequence, expected, budget in (
        ("429", {"primary:read": [{"status": 429, "retry_after": "1"}] * 2}, 200, 10),
        ("503", {"primary:read": [{"status": 503}] * 2}, 200, 10),
        ("credential", {"primary:read": [{"status": 401}]}, 200, 10),
        ("unqualified", {"primary:read": [{"status": 401}], "backup:probe": [{"passed": False}]}, "no_qualified_route", 10),
        ("deadline", {"primary:read": [{"status": 429, "retry_after": "600"}]}, 200, 10),
        ("pending", {"primary:read": [{"disconnect": True}]}, "reconciliation_pending", 10),
        ("budget", {}, "budget_unavailable", 1),
    ):
        remote("/configure", {"sequence": sequence})
        project, stage = seed(name, budget)
        route = m1_read.harness("/route")["route"]
        waits = []
        while True:
            try:
                result = m3_models.call(stage, project, route, "read", {"prompt": "Resume verified checkpoint", "images": []})
                break
            except m3_models.Deferred as failure:
                waits.append(failure.seconds)
                with engine.begin() as conn:
                    event(conn, stage, "qa_wait_observed", {"seconds": failure.seconds})
                time.sleep(failure.seconds + 0.05)
        assert result.get("http_status") == expected or result.get("error_type") == expected
        calls = remote("/stats")["calls"]
        if name == "429":
            primary = [item for item in calls if item["route"] == "primary" and item["phase"] == "read"]
            assert primary[1]["time"] - primary[0]["time"] >= 1
        if name == "budget":
            assert not calls
        if name == "pending":
            again = m3_models.call(stage, project, route, "read", {})
            assert again["error_type"] == expected and remote("/stats")["calls"] == calls
        with engine.begin() as conn:
            evidence = {"project_id": project, "task_id": stage["task_id"], "stage_id": stage["id"],
                        "checkpoint_sha256": stage["output_sha"].hex(), "result": result, "waits": waits, "http_calls": calls,
                        "calls": [dict(item) for item in rows(conn, "SELECT * FROM m1_call WHERE stage_id=:id", id=stage["id"])],
                        "events": [dict(item) for item in rows(conn, "SELECT * FROM stage_event WHERE stage_id=:id", id=stage["id"])]}
            conn.execute(text("UPDATE task_stage SET state=:state,lease_until=NULL WHERE id=:id"),
                         {"id": stage["id"], "state": "done" if expected == 200 else "waiting"})
            matrix[name] = evidence
    print(json.dumps(matrix, indent=2, default=lambda value: value.hex() if isinstance(value, bytes) else str(value)))


if __name__ == "__main__":
    main()
