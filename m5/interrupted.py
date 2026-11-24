import os
import sys
import time
import uuid

sys.path.insert(0, "/app")
from sqlalchemy import text
from app import m2_work, worker
from app.db import engine, one
from app.queue import event

assert os.environ.get("M5_QA") == "1"
stage_id = int(sys.argv[1])
with engine.begin() as conn:
    saved = one(conn, "SELECT * FROM task_stage WHERE id=:id AND state='ready' FOR UPDATE", id=stage_id)
    assert saved and saved["stage_name"] == "m2_read"
    stage = {**saved, "owner": f"m5-interrupted:{uuid.uuid4()}".encode(), "fence": saved["fence"] + 1,
             "attempts": saved["attempts"] + 1}
    conn.execute(text("""UPDATE task_stage SET state='running',owner=:owner,fence=:fence,attempts=:attempts,
        lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 90 SECOND) WHERE id=:id"""), stage)
    conn.execute(text("UPDATE product_task SET state='reading' WHERE id=:id"), {"id": stage["task_id"]})
    event(conn, stage, "started", {"owner": stage["owner"].decode(), "qa_targeted_claim": True})
save_read = m2_work.save_read


def checkpoint_then_wait(stage, evidence, images, final=False):
    committed = save_read(stage, evidence, images, final)
    if committed and not final:
        print(f"QA persisted checkpoint stage={stage['id']} fence={stage['fence']}", flush=True)
        time.sleep(600)
    return committed


m2_work.save_read = checkpoint_then_wait
worker.process(stage)
