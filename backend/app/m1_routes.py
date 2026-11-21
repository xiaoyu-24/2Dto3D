import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import text

from app.auth import current_user, require_member
from app.db import engine, one
from app.m1_acceptance import VERSION
from app.m1_delivery import ARTIFACTS, delivery_metadata, filename, load_delivery
from app.m1_read import call_summaries, current_basis, persist_basis
from app.m1_sample import GENERATOR, SOURCE, annotate_reading, checked_blob
from app.queue import event

router = APIRouter(prefix="/api/tasks/{task_id}/m1")
User = Annotated[dict, Depends(current_user)]


def task_scope(conn, task_id, user_id, write=False):
    task = one(conn, """SELECT t.state, s.sha, b.project_id, s.selection_json FROM product_task t
        JOIN source_file s ON s.id=t.source_id JOIN upload_batch b ON b.id=s.batch_id WHERE t.id=:id""", id=task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    require_member(conn, task["project_id"], user_id, write=write)
    if task["sha"].hex() != SOURCE or json.loads(task["selection_json"]).get("workflow") == "m2":
        raise HTTPException(404, "本次 M1 读图仅用于指定的四针样本")
    return task


def reading_stage(conn, task_id, lock=False):
    return one(conn, """SELECT id, state, output_sha, fence, attempts, error_text FROM task_stage
        WHERE task_id=:task AND stage_name='vision_read'""" + (" FOR UPDATE" if lock else ""), task=task_id)


def blob(conn, sha):
    return checked_blob(one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=sha)["body"], sha)


def saved_evidence(conn, stage):
    if not stage or not stage["output_sha"]:
        return None
    evidence = json.loads(blob(conn, stage["output_sha"]))
    evidence.pop("questions", None)
    return annotate_reading(evidence) if evidence.get("reading") and "trials" not in evidence else evidence


@router.post("/start")
def start(task_id: int, user: User):
    with engine.begin() as conn:
        task_scope(conn, task_id, user["id"], write=True)
        extracted = one(conn, """SELECT output_sha FROM task_stage WHERE task_id=:task AND stage_name='pdf_extract'
            AND state='done' FOR UPDATE""", task=task_id)
        if not extracted or not extracted["output_sha"]:
            raise HTTPException(409, "原图尚未完成解析")
        stage = reading_stage(conn, task_id, lock=True)
        if stage and stage["state"] != "waiting":
            if stage["state"] == "done":
                try:
                    persist_basis(conn, stage, task_id, saved_evidence(conn, stage))
                except ValueError as error:
                    conn.execute(text("UPDATE task_stage SET state='waiting',error_text=:error WHERE id=:id"),
                                 {"id": stage["id"], "error": str(error)})
                    conn.execute(text("UPDATE product_task SET state='read_pending' WHERE id=:task"), {"task": task_id})
                    event(conn, stage, "read_pending", {"reason": str(error)})
            return {"stage_id": stage["id"], "reused": True}
        if stage:
            conn.execute(text("""UPDATE task_stage SET state='ready',ready_at=UTC_TIMESTAMP(6),error_text=NULL
                WHERE id=:id"""), {"id": stage["id"]})
            event(conn, stage, "read_retried", {"reused_page_evidence": bool(stage["output_sha"])})
        else:
            input_sha = extracted["output_sha"]
            idem = hashlib.sha256(f"{task_id}:vision_read:{SOURCE}:{input_sha.hex()}:{GENERATOR}".encode()).digest()
            stage_id = conn.execute(text("""INSERT INTO task_stage (task_id,stage_name,idem_key,input_sha,state)
                VALUES (:task,'vision_read',:idem,:input,'ready')"""),
                                    {"task": task_id, "idem": idem, "input": input_sha}).lastrowid
            stage = {"id": stage_id}
        conn.execute(text("UPDATE product_task SET state='read_queued' WHERE id=:task"), {"task": task_id})
    return {"stage_id": stage["id"], "reused": bool(stage.get("attempts"))}


@router.get("")
def detail(task_id: int, user: User):
    with engine.connect() as conn:
        task = task_scope(conn, task_id, user["id"])
        stage = reading_stage(conn, task_id)
        evidence = saved_evidence(conn, stage)
        basis = current_basis(conn, stage["id"]) if stage else None
        delivery = load_delivery(conn, task_id, basis) if stage and stage["state"] == "done" else None
        state = "basis_recheck" if task["state"] in ("basis_frozen", "published") and not basis else task["state"]
        return {"state": state, "stage_id": stage["id"] if stage else None,
                "error": stage["error_text"] if stage else None, "evidence": evidence,
                "calls": call_summaries(conn, stage["id"]) if stage else [],
                "frozen": {"version": VERSION, "basis_sha256": basis["basis_sha"].hex(),
                           "checklist_sha256": basis["checklist_sha"].hex()} if basis else None,
                "delivery": delivery_metadata(delivery) if delivery else None}


@router.get("/images/{image_key}")
def image(task_id: int, image_key: str, user: User):
    with engine.connect() as conn:
        task_scope(conn, task_id, user["id"])
        evidence = saved_evidence(conn, reading_stage(conn, task_id)) or {}
        sha = evidence.get("images", {}).get(image_key)
        if not sha:
            raise HTTPException(404, "页图不存在")
        return Response(blob(conn, bytes.fromhex(sha)), media_type="image/png", headers={"X-Content-SHA256": sha})


@router.get("/frozen/{kind}")
def frozen(task_id: int, kind: str, user: User):
    with engine.connect() as conn:
        task_scope(conn, task_id, user["id"])
        stage = reading_stage(conn, task_id)
        basis = current_basis(conn, stage["id"]) if stage else None
        sha = basis.get(f"{kind}_sha") if basis and kind in ("basis", "checklist") else None
        if not sha:
            raise HTTPException(404, "冻结文件尚未生成")
        return Response(blob(conn, sha), media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="{VERSION.replace("/", "-")}-{kind}.json"',
            "X-Content-SHA256": sha.hex(),
        })


@router.get("/delivery/{kind}")
def delivered_file(task_id: int, kind: str, user: User):
    with engine.connect() as conn:
        task_scope(conn, task_id, user["id"])
        stage = reading_stage(conn, task_id)
        basis = current_basis(conn, stage["id"]) if stage and stage["state"] == "done" else None
        delivery = load_delivery(conn, task_id, basis)
        if not delivery or kind not in ARTIFACTS:
            raise HTTPException(404, "交付文件尚未发布")
        sha = delivery[f"{kind}_sha"]
        return Response(blob(conn, sha), media_type=ARTIFACTS[kind][1], headers={
            "Content-Disposition": f'attachment; filename="{filename(kind)}"',
            "X-Content-SHA256": sha.hex(),
        })
