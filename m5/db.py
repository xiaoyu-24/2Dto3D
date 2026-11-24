import json
import os
import sys
import uuid

sys.path[:0] = ["/app", "/qa"]
from sqlalchemy import text
from app.db import engine, one, rows
from app.ingest import accept_file, create_batch
from app.m2_work import enqueue
from app.queue import event
import qa_db

assert os.environ.get("M5_QA") == "1", "Independent M5 QA only"


def wire(value):
    return value.hex() if isinstance(value, bytes) else str(value)


def main(data):
    action = data["action"]
    if action == "m3":
        return qa_db.main(data["request"])
    if action == "claim_fixture":
        from app.m2_cad import stage_wire
        from app.queue import claim
        stage = claim("m5-qa-cad", cad=True, stage_id=data["stage_id"])
        return stage_wire(stage) if stage else None
    if action == "interruption_seed":
        with engine.connect() as conn:
            source = one(conn, "SELECT f.body FROM source_file s JOIN file_bytes f ON f.sha=s.sha WHERE s.id=5")["body"]
            previous = dict(one(conn, "SELECT * FROM task_stage WHERE task_id=5 AND stage_name='pdf_extract'"))
        with engine.begin() as conn:
            project = conn.execute(text("INSERT INTO project (name) VALUES ('M5 isolated interruption')")).lastrowid
            conn.execute(text("INSERT INTO project_member VALUES (:project,1,'operator')"), {"project": project})
        batch = create_batch(project, 1, "m5-interruption-" + uuid.uuid4().hex)["id"]
        receipt = accept_file(batch, 1, "interrupt", "M5-worker-interruption.pdf", source, "m2")
        with engine.begin() as conn:
            conn.execute(text("UPDATE task_stage SET state='done',output_sha=:sha WHERE id=:id"),
                         {"id": receipt["stage_id"], "sha": previous["output_sha"]})
            stage = enqueue(conn, receipt["task_id"], "m2_read", previous["output_sha"])
        return {**receipt, "stage_id": stage["id"], "project_id": project}
    if action == "retry":
        from app.m2_routes import retry_stages
        with engine.begin() as conn:
            return retry_stages(conn, task_id=data["task_id"])
    with engine.connect() as conn:
        if action == "stats":
            boundary = int(data.get("boundary", 0))
            queue = [dict(row) for row in rows(conn, """SELECT stage_name,state,COUNT(*) AS count FROM task_stage
                WHERE id>:boundary GROUP BY stage_name,state""", boundary=boundary)]
            return {"stages": queue, "max_event": one(conn, "SELECT COALESCE(MAX(id),0) AS value FROM stage_event")["value"]}
        if action == "inventory":
            tables = ("upload_batch", "source_file", "product_task", "task_stage", "stage_event", "m1_call", "m2_product",
                      "m2_confirmation", "m2_version", "m3_publication", "m3_route")
            result = {name: [dict(row) for row in rows(conn, f"SELECT * FROM {name} ORDER BY 1")] for name in tables}
            result["file_bytes"] = [dict(row) for row in rows(conn,
                "SELECT LOWER(HEX(sha)) AS sha,byte_count,LOWER(SHA2(body,256)) AS actual_sha FROM file_bytes ORDER BY sha")]
            assert all(row["sha"] == row["actual_sha"] for row in result["file_bytes"])
            return result
    raise ValueError("Unknown M5 QA action")


if __name__ == "__main__":
    try:
        print(json.dumps({"result": main(json.load(sys.stdin))}, default=wire))
    except Exception as error:
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}))
        raise SystemExit(1)
