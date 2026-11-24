import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, "/app")
from sqlalchemy import text
from app.db import engine, one
from app.ingest import put_bytes
from app.m1_sample import encode_json
from app.m2_evidence import render
from app.m2_reuse import identity, load, turn
from app.m2_sample import eligible_products
from app.m2_work import blob
from app.queue import event


def validate_receipt(call, review, task, project, expected_part):
    if not call:
        raise ValueError("来源标题回执不存在")
    if review.get("task") != task or review.get("exact_match") is not True:
        raise ValueError("来源标题回执未通过任务与精确匹配检查")
    if review.get("transcribed") != expected_part or not review.get("request_id"):
        raise ValueError("来源标题回执与冻结目标料号不一致")
    if call["project_id"] != project or call["phase"] != "read":
        raise ValueError("来源模型回执项目或阶段不匹配")
    result = json.loads(call["result"] or "{}")
    usage = call["usage_ceiling_cny"]
    if (result.get("sent") is not True or result.get("actual_model") != "deepseek-flash"
            or result.get("http_status") != 200 or result.get("finish_reason") != "completed"
            or result.get("error_type") is not None or result.get("request_id") != review["request_id"]
            or result.get("response") != expected_part or not result.get("usage")
            or usage is None or float(usage) <= 0):
        raise ValueError("来源标题调用缺少成功且已计量的精确模型回执")
    return {"request_id": result["request_id"], "model": result["actual_model"],
            "http_status": result["http_status"], "usage_ceiling_cny": str(usage)}


def validate_page_identity(target, peer):
    if not target or len(target) != len(peer) or target != peer:
        raise ValueError("来源和目标的页数、顺序、页面框、单位或归一整页像素不同")
    return target


def validate_page_crop(crop, expected_sha):
    if hashlib.sha256(crop).hexdigest() != expected_sha:
        raise ValueError("归一原页裁片 SHA-256 与冻结裁片不匹配")
    return crop


def title_review(conn, task):
    stage = one(conn, """SELECT * FROM task_stage WHERE task_id=:task AND stage_name='m5_title_review'
        AND state='done' ORDER BY id DESC LIMIT 1""", task=task)
    if not stage or not stage["output_sha"]:
        raise ValueError("来源任务缺少已完成的标题栏复核阶段")
    return stage, json.loads(blob(conn, stage["output_sha"]))


def reuse_title(target, peer, expected_part, target_sha, peer_sha, crop_sha, crop_path):
    if target == peer or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./_-]{2,159}", expected_part):
        raise ValueError("任务或料号参数无效")
    with engine.connect() as conn:
        target_stage, target_evidence, target_checkpoint, target_source = load(conn, target)
        peer_stage, peer_evidence, peer_checkpoint, peer_source = load(conn, peer)
        target_task = one(conn, "SELECT state FROM product_task WHERE id=:task AND scope_key='file'", task=target)
        peer_review_stage, review = title_review(conn, peer)
        if review.get("stage") != peer_review_stage["id"]:
            raise ValueError("来源标题回执阶段编号不匹配")
        receipt_row = one(conn, """SELECT * FROM m1_call WHERE stage_id=:stage AND project_id=:project
            AND phase='read' AND JSON_UNQUOTE(JSON_EXTRACT(result,'$.request_id'))=:request""",
                          stage=peer_review_stage["id"], project=peer_stage["project_id"], request=review.get("request_id"))
        target_review_stage = one(conn, """SELECT id,output_sha FROM task_stage WHERE task_id=:task
            AND stage_name='m5_title_review' AND state='done' ORDER BY id DESC LIMIT 1""", task=target)
    if (not target_task or target_task["state"] != "selection_ready" or target_stage["state"] != "done"
            or peer_stage["state"] != "done" or target_stage["project_id"] != peer_stage["project_id"]):
        raise ValueError("只支持同项目已完成读图的负载文件任务")
    if target_stage["sha"] == peer_stage["sha"]:
        raise ValueError("目标与来源必须是不同原图")
    if target_stage["sha"].hex() != target_sha or peer_stage["sha"].hex() != peer_sha:
        raise ValueError("原图 SHA-256 与冻结输入不匹配")
    receipt = validate_receipt(receipt_row, review, peer, peer_stage["project_id"], expected_part)
    target_identity = identity(target_source, target_checkpoint)
    peer_identity = identity(peer_source, peer_checkpoint)
    page_identity = validate_page_identity(target_identity, peer_identity)

    page_number = int(review["region"]["page"])
    if not (1 <= page_number <= len(peer_evidence["pages"]) == len(target_evidence["pages"])):
        raise ValueError("标题栏页码不在对应原图范围内")
    peer_frame = peer_evidence["pages"][page_number - 1]
    target_frame = target_evidence["pages"][page_number - 1]
    delta = target_frame["rotation"] - peer_frame["rotation"]
    region_key = f"reused-title-peer-{peer}-page-{page_number}"
    region = {**review["region"], "rect": turn(review["region"]["rect"], delta)}
    peer_key = f"title-review-{page_number}"
    _, peer_images = render(peer_source, peer_checkpoint, {peer_key: review["region"]}, frames=peer_evidence["pages"])
    peer_crop_sha = hashlib.sha256(peer_images[peer_key]).hexdigest()
    if peer_crop_sha != review.get("crop_sha256"):
        raise ValueError("來源標題回執裁圖與原圖不一致")
    located, _ = render(target_source, target_checkpoint, {region_key: region}, frames=target_evidence["pages"])
    crop_bytes = validate_page_crop(Path(crop_path).read_bytes(), crop_sha)
    product = {"part_number": expected_part, "drawing_no": None, "revision": None, "family": None,
               "pin_count": None, "total_length_mm": None, "regions": [region_key], "findings": [],
               "questions": [{"key": "title-reuse-review", "regions": [region_key],
                              "question": "该料号仅由同像原图的已计量标题栏回执支持；请核对订购配置、尺寸和适用范围。"}]}
    if product not in eligible_products(target_stage["sha"].hex(), [product]):
        raise ValueError("标题栏料号不符合该原图的候选约束")
    provenance = {"target_task": target, "target_source_sha256": target_stage["sha"].hex(),
                  "target_stage_id": target_stage["id"], "target_previous_sha256": target_stage["output_sha"].hex(),
                  "target_review_stage_id": target_review_stage["id"] if target_review_stage else None,
                  "target_review_output_sha256": target_review_stage["output_sha"].hex() if target_review_stage and target_review_stage["output_sha"] else None,
                  "peer_task": peer, "peer_source_sha256": peer_stage["sha"].hex(),
                  "peer_stage_id": peer_stage["id"], "peer_output_sha256": peer_stage["output_sha"].hex(),
                  "peer_review_stage_id": peer_review_stage["id"], "page_identity": page_identity,
                  "peer_page_rotation_degrees": peer_frame["rotation"],
                  "target_page_rotation_degrees": target_frame["rotation"],
                  "matched_rotation_delta_degrees": delta, "part_number": expected_part, "crop_sha256": crop_sha,
                  "crop_method": "target_source_canonical_page_crop_144_dpi",
                  "receipt": receipt}

    with engine.begin() as conn:
        current = one(conn, "SELECT state,output_sha,fence FROM task_stage WHERE id=:id FOR UPDATE", id=target_stage["id"])
        if (current["state"] != "done" or current["fence"] != target_stage["fence"]
                or current["output_sha"] != target_stage["output_sha"]):
            raise ValueError("目标读图阶段在复核期间发生变化")
        existing = [item for item in target_evidence.get("title_reuses", [])
                    if item.get("peer_task") == peer and item.get("part_number") == expected_part]
        if existing:
            return {"status": "already_reused", **provenance, "output_sha256": current["output_sha"].hex()}
        if any(item["part_number"] == expected_part for item in target_evidence["products"]):
            raise ValueError("目标读图已含同名候选，但没有本次来源回执记录")
        scope = b"m2:" + hashlib.sha256(expected_part.encode()).hexdigest().encode()
        child = one(conn, "SELECT id FROM product_task WHERE source_id=:source AND scope_key=:scope",
                    source=target_stage["source_id"], scope=scope)
        if child:
            raise ValueError("目标料号已派生产品任务，拒绝覆盖既有状态")
        target_evidence["regions"].update(located["regions"])
        target_evidence.setdefault("images", {})[region_key] = put_bytes(conn, crop_bytes).hex()
        target_evidence["products"].append(product)
        target_evidence.setdefault("title_reuses", []).append(provenance)
        output = put_bytes(conn, encode_json(target_evidence))
        conn.execute(text("UPDATE task_stage SET output_sha=:output WHERE id=:id"),
                     {"output": output, "id": target_stage["id"]})
        event(conn, target_stage, "focused_title_reused", {**provenance, "output_sha256": output.hex()})
    return {"status": "reused", **provenance, "output_sha256": output.hex()}


if __name__ == "__main__":
    assert os.environ.get("M5_QA") == "1"
    if len(sys.argv) != 8:
        raise SystemExit("usage: reuse_title.py TARGET_TASK PEER_TASK EXPECTED_PART TARGET_SHA PEER_SHA CROP_SHA CROP_PATH")
    print(json.dumps(reuse_title(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], *sys.argv[4:]), ensure_ascii=False))
