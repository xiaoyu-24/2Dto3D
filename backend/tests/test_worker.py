import hashlib
import json
import uuid

from sqlalchemy import text

from app.db import engine, one
from app.ingest import accept_file, create_batch
from app.queue import claim, finish, renew
from app.worker import process


def isolated_job(account, body):
    # Defer jobs created by earlier tests so the global queue tests claim this job only.
    with engine.begin() as conn:
        conn.execute(text("""UPDATE task_stage SET ready_at=DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 1 DAY)
            WHERE state='ready'"""))
    batch = create_batch(account["project"], account["id"], uuid.uuid4().hex)
    return accept_file(batch["id"], account["id"], "file", "M12.pdf", body)


def test_worker_parses_real_m12_and_saves_checkpoint_atomically(account, m12):
    assert len(m12) == 104504
    assert hashlib.sha256(m12).hexdigest() == "be06c0a72ba49f73275dcbe515893495b8baadfd1a1aa19579603e2be8a92d2c"
    receipt = isolated_job(account, m12)
    stage = claim("real-m12-worker")
    assert stage["id"] == receipt["stage_id"]
    assert process(stage) is True
    with engine.connect() as conn:
        saved = one(conn, """SELECT t.state,q.state AS stage_state,f.body FROM product_task t
            JOIN task_stage q ON q.task_id=t.id JOIN file_bytes f ON f.sha=q.output_sha
            WHERE t.id=:id""", id=receipt["task_id"])
        checkpoint = json.loads(saved["body"])
        assert saved["state"] == "awaiting_read" and saved["stage_state"] == "done"
        assert checkpoint["page_count"] == 1
        assert checkpoint["source_sha256"] == receipt["sha256"]
        assert checkpoint["pages"][0]["media_box"][2] > 0
        assert one(conn, "SELECT id FROM stage_event WHERE stage_id=:id AND kind='completed'", id=stage["id"])


def test_locked_stage_is_skipped_and_expired_owner_cannot_commit(account, m12):
    receipt = isolated_job(account, m12)
    with engine.begin() as locking_connection:
        one(locking_connection, "SELECT id FROM task_stage WHERE id=:id FOR UPDATE", id=receipt["stage_id"])
        assert claim("blocked-worker") is None
    first = claim("first-worker")
    assert first["id"] == receipt["stage_id"]
    assert renew(first) is True
    assert claim("second-worker") is None
    with engine.begin() as conn:
        conn.execute(text("""UPDATE task_stage SET lease_until=DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 1 SECOND)
            WHERE id=:id"""), {"id": first["id"]})
    assert renew(first) is False
    assert finish(first, checkpoint={"page_count": 1}) is False
    second = claim("second-worker")
    assert second["fence"] == first["fence"] + 1
    assert finish(first, checkpoint={"page_count": 1}) is False
    assert process(second) is True
    assert finish(second, checkpoint={"page_count": 1}) is False


def test_parser_failure_is_persisted_and_next_job_can_run(account, m12):
    receipt = isolated_job(account, b"%PDF-broken")
    assert process(claim("parser-worker")) is True
    with engine.connect() as conn:
        failure = one(conn, "SELECT state,error_code,output_sha FROM task_stage WHERE id=:id", id=receipt["stage_id"])
    assert failure["state"] == "failed"
    assert failure["error_code"] == "pdf_parse_failed"
    assert failure["output_sha"] is None
    next_receipt = isolated_job(account, m12)
    stage = claim("parser-worker")
    assert stage["id"] == next_receipt["stage_id"]
    assert process(stage) is True
