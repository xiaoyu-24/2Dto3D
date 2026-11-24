import argparse
import hashlib
import json
from io import BytesIO

from pypdf import PdfReader, PdfWriter

from app.db import engine, one
from app.ingest import put_bytes
from app.m1_sample import encode_json
from app.m2_evidence import render
from app.m2_work import blob, finish_stage


def identity(source, checkpoint):
    writer = PdfWriter()
    for page in PdfReader(BytesIO(source)).pages:
        page.rotation = 0
        writer.add_page(page)
    canonical = BytesIO()
    writer.write(canonical)
    pages = [{**page, "rotation": 0} for page in checkpoint["pages"]]
    _, images = render(canonical.getvalue(), {**checkpoint, "pages": pages})
    return [{"media_box": page["media_box"], "crop_box": page["crop_box"], "user_unit": page["user_unit"],
             "page_sha256": hashlib.sha256(images[f"page-{page['number']}"]).hexdigest()} for page in pages]


def turn(rect, degrees):
    if degrees % 90:
        raise ValueError("页面旋转不是直角")
    left, top, right, bottom = rect
    for _ in range(degrees % 360 // 90):
        left, top, right, bottom = 1 - bottom, left, 1 - top, right
    return [left, top, right, bottom]


def load(conn, task):
    stage = one(conn, """SELECT q.*,s.id AS source_id,s.sha,b.project_id FROM task_stage q JOIN product_task t ON t.id=q.task_id
        JOIN source_file s ON t.source_id=s.id JOIN upload_batch b ON s.batch_id=b.id
        WHERE t.scope_key='file' AND q.task_id=:task AND q.stage_name='m2_read'""", task=task)
    if not stage or not stage["output_sha"]:
        raise ValueError("文件任务缺少原页检查点")
    evidence = json.loads(blob(conn, stage["output_sha"]))
    checkpoint = json.loads(blob(conn, stage["input_sha"]))
    if not stage["sha"].hex() == evidence["source_sha256"] == checkpoint["source_sha256"]:
        raise ValueError("原图检查点不匹配")
    return stage, evidence, checkpoint, blob(conn, stage["sha"])


def reuse(target, peer):
    if target == peer:
        raise ValueError("需要两个不同的原任务")
    with engine.connect() as conn:
        stage, evidence, checkpoint, source = load(conn, target)
        origin, previous, prior_checkpoint, prior_source = load(conn, peer)
        if origin["state"] != "done" or stage["project_id"] != origin["project_id"] or stage["state"] not in ("done", "failed"):
            raise ValueError("只支持同项目已完成读图和静止的目标阶段")
        if evidence.get("equivalent_read", {}).get("peer_output_sha256") == origin["output_sha"].hex():
            return "already_reused"
        receipt = one(conn, """SELECT stage_id FROM m1_call WHERE stage_id=:id AND phase='read' AND project_id=:project
            AND JSON_UNQUOTE(JSON_EXTRACT(result,'$.request_id'))=:request
            AND JSON_UNQUOTE(JSON_EXTRACT(result,'$.finish_reason'))='completed'
            AND JSON_EXTRACT(result,'$.http_status')=200""", id=origin["id"], project=origin["project_id"],
                      request=previous["model_run"]["request_id"])
        if not receipt:
            raise ValueError("来源阶段缺少对应的已完成读图回执")
    fingerprints = identity(source, checkpoint)
    if fingerprints != identity(prior_source, prior_checkpoint):
        raise ValueError("源和目标的页数、顺序、页面框、单位或正向整页指纹不同")
    rotations = [current["rotation"] - old["rotation"] for current, old in zip(evidence["pages"], previous["pages"], strict=True)]
    regions = {}
    for key, region in previous["regions"].items():
        index = region["page"] - 1
        width, height = (previous["pages"][index][axis] for axis in ("width", "height"))
        left, top, across, down = region["pixel_rect"]
        rect = [left / width, top / height, (left + across) / width, (top + down) / height]
        regions[key] = {"page": region["page"], "label": region["view"], "rect": turn(rect, rotations[index])}
    located, images = render(source, checkpoint, regions, frames=evidence["pages"])
    reading = previous["reading"]
    for region in reading["regions"].values():
        region["rect"] = turn(region["rect"], rotations[region["page"] - 1])
    provenance = {"peer_task": peer, "peer_stage": origin["id"], "peer_source_sha256": origin["sha"].hex(),
                  "peer_output_sha256": origin["output_sha"].hex(), "previous_sha256": stage["output_sha"].hex(),
                  "page_identity": fingerprints, "rotation_deltas": rotations}
    evidence.update(equivalent_read=provenance, products=previous["products"], reading=reading, regions=located["regions"],
                    model_run={**previous["model_run"], "source_stage_id": origin["id"]})
    with engine.begin() as conn:
        current = one(conn, "SELECT state,output_sha,fence FROM task_stage WHERE id=:id FOR UPDATE", id=stage["id"])
        if (current["output_sha"] != stage["output_sha"] or current["state"] != stage["state"] or current["fence"] != stage["fence"]
                or one(conn, "SELECT id FROM product_task WHERE source_id=:id AND scope_key<>'file' LIMIT 1", id=stage["source_id"])):
            raise ValueError("目标状态已变化或已有产品绑定该检查点")
        evidence["images"] = {frame["image_key"]: evidence["images"][frame["image_key"]] for frame in evidence["pages"]}
        evidence["images"].update({key: put_bytes(conn, value).hex() for key, value in images.items()})
        output = put_bytes(conn, encode_json(evidence))
        finish_stage(conn, stage, "done", "selection_ready", "equivalent_read_reused", {**provenance, "output_sha256": output.hex()}, output=output)
    return "reused"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("target_task", type=int)
    parser.add_argument("peer_task", type=int)
    args = parser.parse_args()
    print(reuse(args.target_task, args.peer_task))
