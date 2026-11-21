import hashlib

from fastapi import HTTPException
from sqlalchemy import text

from app.auth import require_member
from app.db import engine, one

MAX_PDF_BYTES = 32 * 1024 * 1024


def put_bytes(conn, body: bytes):
    sha = hashlib.sha256(body).digest()
    conn.execute(text("""INSERT INTO file_bytes (sha,byte_count,body) VALUES (:sha,:size,:body)
        ON DUPLICATE KEY UPDATE sha=file_bytes.sha"""), {"sha": sha, "size": len(body), "body": body})
    return sha


def create_batch(project_id: int, user_id: int, request_key: str):
    with engine.begin() as conn:
        require_member(conn, project_id, user_id, write=True)
        conn.execute(text("""INSERT INTO upload_batch (project_id,user_id,request_key)
            VALUES (:project,:user,:key) ON DUPLICATE KEY UPDATE id=upload_batch.id"""),
            {"project": project_id, "user": user_id, "key": request_key.encode()})
        batch = one(conn, """SELECT id,project_id FROM upload_batch
            WHERE project_id=:project AND request_key=:key""",
            project=project_id, key=request_key.encode())
    return dict(batch)


def accept_file(batch_id: int, user_id: int, client_file_key: str, filename: str, body: bytes):
    if len(body) > MAX_PDF_BYTES:
        raise HTTPException(413, "PDF 不能超过 32 MiB")
    if not body.startswith(b"%PDF-"):
        raise HTTPException(422, "请选择有效的 PDF 文件")
    sha = hashlib.sha256(body).digest()
    with engine.begin() as conn:
        batch = one(conn, "SELECT project_id FROM upload_batch WHERE id=:id", id=batch_id)
        if not batch:
            raise HTTPException(404, "批次不存在")
        require_member(conn, batch["project_id"], user_id, write=True)
        # Serialize only this batch. No source or queue row can escape this transaction.
        one(conn, "SELECT id FROM upload_batch WHERE id=:id FOR UPDATE", id=batch_id)
        source = one(conn, """SELECT id,sha FROM source_file
            WHERE batch_id=:batch AND client_file_key=:key""", batch=batch_id, key=client_file_key.encode())
        reused = source is not None
        if source:
            if source["sha"] != sha:
                raise HTTPException(409, "文件键已用于另一份内容，请为新文件使用新的文件键")
            source_id = source["id"]
        else:
            put_bytes(conn, body)
            source_id = conn.execute(text("""INSERT INTO source_file
                (batch_id,client_file_key,filename,sha,selection_json,state)
                VALUES (:batch,:key,:filename,:sha,'{}','accepted')"""),
                {"batch": batch_id, "key": client_file_key.encode(), "filename": filename, "sha": sha}).lastrowid
            task_id = conn.execute(text("""INSERT INTO product_task (source_id,scope_key,state,budget_cny)
                SELECT :source,'file','queued',p.version_budget_cny FROM project p WHERE p.id=:project"""),
                {"source": source_id, "project": batch["project_id"]}).lastrowid
            idem = hashlib.sha256(f"{task_id}:pdf_extract:{sha.hex()}:pypdf-v1".encode()).digest()
            stage_id = conn.execute(text("""INSERT INTO task_stage
                (task_id,stage_name,idem_key,input_sha,state)
                VALUES (:task,'pdf_extract',:idem,:sha,'ready')"""),
                {"task": task_id, "idem": idem, "sha": sha}).lastrowid
            conn.execute(text("""INSERT INTO stage_event (stage_id,fence,kind,detail)
                VALUES (:stage,0,'accepted',JSON_OBJECT('source_id',:source))"""),
                {"stage": stage_id, "source": source_id})
        task = one(conn, """SELECT t.id AS task_id,s.id AS stage_id FROM product_task t
            JOIN task_stage s ON s.task_id=t.id AND s.stage_name='pdf_extract'
            WHERE t.source_id=:source AND t.scope_key='file'""", source=source_id)
    # Leaving engine.begin() has committed; a failed commit never creates a receipt.
    return {"status": "accepted", "message": "已接收", "batch_id": batch_id,
            "source_id": source_id, **dict(task), "sha256": sha.hex(), "reused": reused}
