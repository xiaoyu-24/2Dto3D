import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth import current_user, require_member
from app.db import engine, one, rows
from app.m1_read import call_summaries
from app.m1_sample import encode_json
from app.m2_sample import eligible_products, resolve_variant, template_input
from app.m2_work import blob, confirmations, enqueue
from app.queue import event

router = APIRouter(prefix="/api")
User = Annotated[dict, Depends(current_user)]


def task_scope(conn, task_id, user_id, write=False):
    task = one(conn, """SELECT t.id,t.source_id,t.state,b.project_id,file_task.id AS file_task_id,p.specification
        FROM product_task t JOIN source_file s ON s.id=t.source_id JOIN upload_batch b ON b.id=s.batch_id
        JOIN product_task file_task ON file_task.source_id=s.id AND file_task.scope_key='file'
        LEFT JOIN m2_product p ON p.task_id=t.id WHERE t.id=:id""", id=task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    require_member(conn, task["project_id"], user_id, write=write)
    return task


def read_stage(conn, file_task, lock=False):
    return one(conn, "SELECT * FROM task_stage WHERE task_id=:task AND stage_name='m2_read'" + (" FOR UPDATE" if lock else ""), task=file_task)


def evidence_for(conn, stage):
    evidence = json.loads(blob(conn, stage["output_sha"])) if stage and stage["output_sha"] else None
    if evidence and "products" in evidence:
        evidence["products"] = eligible_products(evidence["source_sha256"], evidence["products"])
    return evidence


class SelectedParts(BaseModel):
    part_numbers: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(min_length=1, max_length=16)


class Answer(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


@router.get("/tasks/{task_id}/m2")
def detail(task_id: int, user: User):
    with engine.connect() as conn:
        task = task_scope(conn, task_id, user["id"])
        stage = read_stage(conn, task["file_task_id"])
        evidence = evidence_for(conn, stage)
        if evidence and evidence.get("products"):
            for product in evidence["products"]:
                choice = template_input(evidence["source_sha256"], product["part_number"])
                if choice:
                    product["part_example"] = choice
                    product["total_length_mm"] = None
        timeline = rows(conn, """SELECT stage_name,state,attempts,error_text,LOWER(HEX(input_sha)) AS input_sha256,
            LOWER(HEX(output_sha)) AS output_sha256 FROM task_stage WHERE task_id=:task ORDER BY id""", task=task_id)
        version = one(conn, "SELECT id,version,state,geometry_revision FROM m2_version WHERE task_id=:task", task=task_id)
        return {"state": task["state"], "file_task_id": task["file_task_id"], "evidence": evidence,
                "shared_read_sha256": stage["output_sha"].hex() if stage and stage["output_sha"] else None,
                "specification": json.loads(task["specification"]) if task["specification"] else None,
                "confirmations": confirmations(conn, task_id), "stages": timeline, "version": version,
                "calls": call_summaries(conn, stage["id"]) if stage else [],
                "cad_calls": [call for cad in rows(conn, "SELECT id FROM task_stage WHERE task_id=:task AND stage_name='m2_cad'", task=task_id)
                              for call in call_summaries(conn, cad["id"])]}


@router.get("/tasks/{task_id}/m2/images/{key}")
def image(task_id: int, key: str, user: User):
    with engine.connect() as conn:
        task = task_scope(conn, task_id, user["id"])
        evidence = evidence_for(conn, read_stage(conn, task["file_task_id"])) or {}
        sha = evidence.get("images", {}).get(key)
        if not sha:
            raise HTTPException(404, "页图不存在")
        return Response(blob(conn, bytes.fromhex(sha)), media_type="image/png", headers={"X-Content-SHA256": sha})


@router.post("/tasks/{task_id}/products")
def derive(task_id: int, data: SelectedParts, user: User):
    with engine.begin() as conn:
        task = task_scope(conn, task_id, user["id"], write=True)
        if task_id != task["file_task_id"]:
            raise HTTPException(409, "请从原图文件选择料号")
        stage = read_stage(conn, task_id, lock=True)
        if not stage or stage["state"] != "done":
            raise HTTPException(409, "请等待原图联合读图完成")
        evidence = evidence_for(conn, stage)
        available = {item["part_number"]: item for item in evidence["products"]}
        for part in data.part_numbers:
            if part not in available:
                resolved = resolve_variant(evidence["source_sha256"], part, available)
                if not resolved:
                    raise HTTPException(422, "所选料号不符合本图的型号和配置表")
                available[part] = resolved
        derived = []
        for part in dict.fromkeys(data.part_numbers):
            scope = b"m2:" + hashlib.sha256(part.encode()).hexdigest().encode()
            previous = one(conn, "SELECT id FROM product_task WHERE source_id=:source AND scope_key=:scope", source=task["source_id"], scope=scope)
            if previous:
                derived.append({"task_id": previous["id"], "part_number": part, "reused": True})
                continue
            product = available[part]
            questions = product["questions"]
            params = {"source": task["source_id"], "project": task["project_id"], "scope": scope,
                      "state": "needs_confirmation" if questions else "queued"}
            child = conn.execute(text("""INSERT INTO product_task (source_id,scope_key,state,budget_cny)
                SELECT :source,:scope,:state,version_budget_cny FROM project WHERE id=:project"""), params).lastrowid
            conn.execute(text("INSERT INTO m2_product VALUES (:id,:part,:specification)"),
                         {"id": child, "part": part, "specification": encode_json(product).decode()})
            next_stage = enqueue(conn, child, "m2_prepare", stage["output_sha"])
            if questions:
                conn.execute(text("UPDATE task_stage SET state='waiting' WHERE id=:id"), {"id": next_stage["id"]})
            for question in questions:
                references = [{"key": key, **evidence["regions"][key], "image_sha256": evidence["images"][key]}
                              for key in question["regions"]]
                conn.execute(text("""INSERT INTO m2_confirmation (task_id,question_key,question,evidence)
                    VALUES (:task,:key,:question,:evidence)"""), {"task": child, "key": question["key"],
                             "question": question["question"], "evidence": encode_json(references).decode()})
            event(conn, next_stage, "derived", {"part_number": part, "shared_read_sha256": stage["output_sha"].hex(),
                                               "source_stage_id": stage["id"], "question_count": len(questions)})
            derived.append({"task_id": child, "part_number": part, "reused": False})
    return {"products": derived}


@router.post("/confirmations/{confirmation_id}/answer")
def answer(confirmation_id: int, data: Answer, user: User):
    value = data.answer.strip()
    if not value:
        raise HTTPException(422, "请填写确认结论")
    with engine.begin() as conn:
        target = one(conn, "SELECT task_id FROM m2_confirmation WHERE id=:id", id=confirmation_id)
        if not target:
            raise HTTPException(404, "确认问题不存在")
        task_id = target["task_id"]
        task = task_scope(conn, task_id, user["id"], write=True)
        stage = one(conn, "SELECT * FROM task_stage WHERE task_id=:task AND stage_name='m2_prepare' FOR UPDATE", task=task_id)
        saved = one(conn, "SELECT * FROM m2_confirmation WHERE id=:id FOR UPDATE", id=confirmation_id)
        if saved["answer"] is not None:
            if json.loads(saved["answer"])["value"] != value:
                raise HTTPException(409, "该问题已确认，不能覆盖原记录")
            return {"task_id": task_id, "reused": True, "resumed": False}
        specification = json.loads(task["specification"])
        expected = next(item.get("expected") for item in specification["questions"] if item["key"] == saved["question_key"])
        if expected and value != expected:
            raise HTTPException(422, "答案与本型号可验收的原图依据不符，任务继续等待确认")
        conn.execute(text("""UPDATE m2_confirmation SET answer=:answer,answered_by=:user,answered_at=UTC_TIMESTAMP(6)
            WHERE id=:id"""), {"id": confirmation_id, "user": user["id"], "answer": encode_json({"value": value}).decode()})
        event(conn, stage, "confirmation_answered", {"confirmation_id": confirmation_id, "operator_id": user["id"]})
        waiting = one(conn, "SELECT id FROM m2_confirmation WHERE task_id=:task AND answer IS NULL LIMIT 1", task=task_id)
        resumed = waiting is None
        if resumed:
            conn.execute(text("UPDATE task_stage SET state='ready',ready_at=UTC_TIMESTAMP(6) WHERE id=:id"), {"id": stage["id"]})
            conn.execute(text("UPDATE product_task SET state='queued' WHERE id=:id"), {"id": task_id})
            event(conn, stage, "confirmation_resumed", {"shared_read_sha256": stage["input_sha"].hex()})
    return {"task_id": task_id, "reused": False, "resumed": resumed}


def retry_stages(conn, *, task_id=None, batch_id=None):
    where = "q.task_id=:task" if task_id is not None else "s.batch_id=:batch"
    failed = rows(conn, f"""SELECT q.*,v.geometry_sha,v.html_sha FROM task_stage q JOIN product_task t ON t.id=q.task_id
        JOIN source_file s ON s.id=t.source_id LEFT JOIN m2_version v ON v.task_id=t.id
        WHERE {where} AND q.state='failed' ORDER BY q.id FOR UPDATE OF q""", task=task_id, batch=batch_id)
    for stage in failed:
        conn.execute(text("UPDATE task_stage SET state='ready',ready_at=UTC_TIMESTAMP(6),error_code=NULL,error_text=NULL WHERE id=:id"), {"id": stage["id"]})
        conn.execute(text("UPDATE product_task SET state=:state WHERE id=:id"),
                     {"id": stage["task_id"], "state": "partial" if stage["html_sha"] else "queued"})
        event(conn, stage, "retried", {"input_sha256": stage["input_sha"].hex(),
                                       "checkpoint_sha256": stage["output_sha"].hex() if stage["output_sha"] else None,
                                       "geometry_sha256": stage["geometry_sha"].hex() if stage["geometry_sha"] else None,
                                       "html_sha256": stage["html_sha"].hex() if stage["html_sha"] else None})
    return {"retried": [stage["task_id"] for stage in failed]}


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: int, user: User):
    with engine.begin() as conn:
        task_scope(conn, task_id, user["id"], write=True)
        return retry_stages(conn, task_id=task_id)


@router.post("/batches/{batch_id}/retry-failed")
def retry_batch(batch_id: int, user: User):
    with engine.begin() as conn:
        batch = one(conn, "SELECT project_id FROM upload_batch WHERE id=:id", id=batch_id)
        if not batch:
            raise HTTPException(404, "批次不存在")
        require_member(conn, batch["project_id"], user["id"], write=True)
        return retry_stages(conn, batch_id=batch_id)
