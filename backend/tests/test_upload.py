import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from app.db import engine, one
from app.ingest import accept_file, create_batch
from app.main import app


def batch(client, account):
    response = client.post(f"/api/projects/{account['project']}/batches", json={"request_key": uuid.uuid4().hex})
    assert response.status_code == 200
    return response.json()["id"]


def upload(client, batch_id, body, key="m12", filename="M12.pdf"):
    return client.post(f"/api/batches/{batch_id}/files", data={"client_file_key": key},
                       files={"file": (filename, body, "application/pdf")})


def test_receipt_committed_and_retry_reuses_same_task(client, account, m12):
    batch_id = batch(client, account)
    first = upload(client, batch_id, m12)
    assert first.status_code == 202
    receipt = first.json()
    assert receipt["message"] == "已接收" and receipt["reused"] is False
    with engine.connect() as independent_reader:
        stored = one(independent_reader, """SELECT f.body,s.id,t.id AS task_id,q.id AS stage_id
            FROM source_file s JOIN file_bytes f ON f.sha=s.sha
            JOIN product_task t ON t.source_id=s.id JOIN task_stage q ON q.task_id=t.id
            WHERE s.id=:id""", id=receipt["source_id"])
        assert stored["body"] == m12
        assert stored["task_id"] == receipt["task_id"]
        assert stored["stage_id"] == receipt["stage_id"]
    retry = upload(client, batch_id, m12).json()
    assert retry["reused"] is True
    assert retry["task_id"] == receipt["task_id"]
    assert retry["stage_id"] == receipt["stage_id"]
    assert len(client.get(f"/api/batches/{batch_id}").json()["files"]) == 1


def test_parallel_retries_create_one_source_task_and_stage(account, m12):
    batch_id = create_batch(account["project"], account["id"], uuid.uuid4().hex)["id"]
    def send(_):
        return accept_file(batch_id, account["id"], "parallel", "M12.pdf", m12)
    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(send, range(6)))
    assert len({r["task_id"] for r in receipts}) == 1
    assert len({r["stage_id"] for r in receipts}) == 1
    assert sum(not r["reused"] for r in receipts) == 1


@pytest.mark.parametrize("failure_point", ["source_file", "product_task", "task_stage", "stage_event", "commit"])
def test_incomplete_transaction_has_no_receipt_or_orphans(client, account, m12, failure_point):
    batch_id = batch(client, account)
    body = m12 + f"\n% rollback-{uuid.uuid4()}\n".encode()
    sha = hashlib.sha256(body).digest()
    def fail_statement(conn, cursor, statement, parameters, context, executemany):
        if f"INSERT INTO {failure_point}" in statement:
            raise SQLAlchemyError("injected write failure")
    def fail_commit(conn):
        raise SQLAlchemyError("injected commit failure")
    listener = fail_commit if failure_point == "commit" else fail_statement
    hook = "commit" if failure_point == "commit" else "before_cursor_execute"
    event.listen(engine, hook, listener)
    try:
        response = upload(client, batch_id, body)
        assert response.status_code == 503
        assert "status" not in response.json()
    finally:
        event.remove(engine, hook, listener)
    with engine.connect() as conn:
        assert one(conn, "SELECT sha FROM file_bytes WHERE sha=:sha", sha=sha) is None
        assert one(conn, "SELECT id FROM source_file WHERE batch_id=:batch", batch=batch_id) is None
        assert one(conn, "SELECT id FROM task_stage WHERE input_sha=:sha", sha=sha) is None


def test_same_key_changed_bytes_conflicts_and_same_name_new_key_is_independent(client, account, m12):
    batch_id = batch(client, account)
    first = upload(client, batch_id, m12).json()
    changed = m12 + b"\n% revision-two\n"
    assert upload(client, batch_id, changed).status_code == 409
    second = upload(client, batch_id, changed, key="revision-two").json()
    assert second["task_id"] != first["task_id"]
    assert second["sha256"] != first["sha256"]


def test_batch_request_key_is_idempotent(client, account):
    route = f"/api/projects/{account['project']}/batches"
    data = {"request_key": uuid.uuid4().hex}
    assert client.post(route, json=data).json() == client.post(route, json=data).json()


def test_login_logout_membership_and_read_only_permissions(client, account, m12):
    batch_id = batch(client, account)
    task_id = upload(client, batch_id, m12).json()["task_id"]
    with engine.begin() as conn:
        other_project = conn.execute(text("INSERT INTO project (name) VALUES ('private')")).lastrowid
        other_user = conn.execute(text("""INSERT INTO app_user (login,password_hash)
            VALUES (:login,'unused')"""), {"login": uuid.uuid4().hex}).lastrowid
        conn.execute(text("INSERT INTO project_member VALUES (:p,:u,'operator')"), {"p": other_project, "u": other_user})
    other_batch = create_batch(other_project, other_user, uuid.uuid4().hex)["id"]
    other_task = accept_file(other_batch, other_user, "private", "M12.pdf", m12)["task_id"]
    assert client.get(f"/api/projects/{other_project}/batches").status_code == 404
    assert client.get(f"/api/batches/{other_batch}").status_code == 404
    assert client.get(f"/api/tasks/{other_task}").status_code == 404
    assert client.get(f"/api/tasks/{other_task}/events").status_code == 404
    assert upload(client, other_batch, m12).status_code == 404
    assert client.post(f"/api/projects/{other_project}/batches", json={"request_key": "denied"}).status_code == 404
    with engine.begin() as conn:
        conn.execute(text("UPDATE project_member SET role='reader' WHERE user_id=:id"), {"id": account["id"]})
    assert client.get(f"/api/tasks/{task_id}").status_code == 200
    assert upload(client, batch_id, m12).status_code == 403
    assert client.post(f"/api/projects/{account['project']}/batches", json={"request_key": "readonly"}).status_code == 403
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/projects").status_code == 401
    assert upload(client, batch_id, m12).status_code == 401
    assert client.post("/api/login", json={"login": account["login"], "password": "wrong"}).status_code == 401
    with TestClient(app) as cross_site:
        assert cross_site.post("/api/login", json={"login": account["login"], "password": account["password"]}).status_code == 403


def test_invalid_pdf_and_size_limit_do_not_queue(client, account):
    batch_id = batch(client, account)
    assert upload(client, batch_id, b"not a PDF").status_code == 422
    assert upload(client, batch_id, b"%PDF-" + b" " * (32 * 1024 * 1024)).status_code == 413
    assert client.get(f"/api/batches/{batch_id}").json()["files"] == []
