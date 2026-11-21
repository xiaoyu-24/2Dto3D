import json

from sqlalchemy import text

from app.db import engine, one
from app.ingest import put_bytes


def event(conn, stage, kind, detail):
    conn.execute(text("""INSERT INTO stage_event (stage_id,fence,kind,detail)
        VALUES (:id,:fence,:kind,:detail)"""),
        {"id": stage["id"], "fence": stage["fence"], "kind": kind, "detail": json.dumps(detail)})


def claim(owner: str):
    with engine.begin() as conn:
        found = one(conn, """SELECT * FROM task_stage WHERE stage_name='pdf_extract' AND
            ((state='ready' AND ready_at<=UTC_TIMESTAMP(6)) OR
             (state='running' AND lease_until<=UTC_TIMESTAMP(6)))
            ORDER BY ready_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""")
        if not found:
            return None
        stage = dict(found)
        recovered = stage["state"] == "running"
        stage.update(owner=owner.encode(), fence=stage["fence"] + 1)
        conn.execute(text("""UPDATE task_stage SET state='running',owner=:owner,fence=:fence,
            lease_until=DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 90 SECOND),attempts=attempts+1
            WHERE id=:id"""), stage)
        conn.execute(text("UPDATE product_task SET state='processing' WHERE id=:id"), {"id": stage["task_id"]})
        event(conn, stage, "reclaimed" if recovered else "started", {"owner": owner})
    return stage


def renew(stage):
    with engine.begin() as conn:
        return conn.execute(text("""UPDATE task_stage
            SET lease_until=DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 90 SECOND)
            WHERE id=:id AND state='running' AND owner=:owner AND fence=:fence
            AND input_sha=:input_sha AND lease_until>UTC_TIMESTAMP(6)"""), stage).rowcount == 1


def finish(stage, checkpoint=None, error=None):
    with engine.begin() as conn:
        current = one(conn, """SELECT id FROM task_stage WHERE id=:id AND state='running'
            AND owner=:owner AND fence=:fence AND input_sha=:input_sha
            AND lease_until>UTC_TIMESTAMP(6) FOR UPDATE""", **stage)
        if not current:
            return False
        output_sha = None
        if error is None:
            output_sha = put_bytes(conn, json.dumps(checkpoint, ensure_ascii=False).encode())
        conn.execute(text("""UPDATE task_stage SET state=:state,output_sha=:output,
            error_code=:code,error_text=:error,lease_until=NULL WHERE id=:id"""),
            {"id": stage["id"], "state": "done" if error is None else "failed", "output": output_sha,
             "code": None if error is None else "pdf_parse_failed", "error": error})
        conn.execute(text("UPDATE product_task SET state=:state WHERE id=:id"),
                     {"id": stage["task_id"], "state": "awaiting_read" if error is None else "failed"})
        event(conn, stage, "completed" if error is None else "failed",
              {"output_sha256": output_sha.hex(), "page_count": checkpoint["page_count"]}
              if error is None else {"error": error})
    return True
