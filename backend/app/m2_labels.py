import hashlib
import json
import re
import subprocess
from xml.etree import ElementTree

from sqlalchemy import text

from app.db import engine, one
from app.ingest import put_bytes
from app.m1_sample import checked_blob, encode_json
from app.m2_evidence import render
from app.m2_sample import eligible_products
from app.queue import event

LABEL = re.compile(r"型号[：:]([A-Za-z0-9][A-Za-z0-9./_-]{2,159})")


def printed_candidates(source, checkpoint, evidence):
    existing = {item["part_number"] for item in evidence["products"]}
    if not any(LABEL.search(frame.get("text", "")) for frame in evidence["pages"]):
        return {}
    try:
        xml = subprocess.run(["pdftotext", "-cropbox", "-bbox-layout", "-", "-"], input=source, capture_output=True,
                             timeout=30, check=True).stdout
        pages = ElementTree.fromstring(xml).findall(".//{*}page")
    except (subprocess.SubprocessError, ElementTree.ParseError):
        return {}
    if len(pages) != len(evidence["pages"]):
        return {}
    regions, products = {}, []
    for page, frame in zip(pages, evidence["pages"]):
        if "接线方式" not in frame["text"] or "规格型号" not in frame["text"]:
            continue
        words = list(page.iterfind(".//{*}word"))
        spellings = {word.text for word in words}
        for word in words:
            match = LABEL.fullmatch(word.text or "")
            if not match or match[1] in existing or len(existing) >= 16 or match[1] not in spellings or frame["user_unit"] != 1:
                continue
            part = match[1]
            key = "printed-" + hashlib.sha256(part.encode()).hexdigest()[:16]
            product = {"part_number": part, "drawing_no": None, "revision": None, "family": None, "pin_count": None,
                       "total_length_mm": None, "regions": [key], "findings": [], "questions": [{"key": "printed-model",
                       "question": f"请对照原页型号栏确认 {part} 适用于本产品，并核对订货配置及尺寸依据。", "regions": [key]}]}
            if not eligible_products(evidence["source_sha256"], [product]):
                continue
            scale = frame["dpi"] / 72
            rect = [float(word.attrib[field]) * scale / frame[axis] for field, axis in
                    (("xMin", "width"), ("yMin", "height"), ("xMax", "width"), ("yMax", "height"))]
            if not (0 <= rect[0] < rect[2] <= 1 and 0 <= rect[1] < rect[3] <= 1):
                continue
            regions[key] = {"page": frame["number"], "label": f"原图型号栏 {part}", "rect": rect}
            products.append(product)
            existing.add(part)
    if not products:
        return {}
    located, images = render(source, checkpoint, regions, frames=evidence["pages"])
    evidence["regions"].update(located["regions"])
    evidence["products"].extend(products)
    return images


def supplement(task_id):
    with engine.begin() as conn:
        stage = one(conn, """SELECT q.* FROM task_stage q JOIN product_task t ON t.id=q.task_id
            WHERE t.id=:task AND t.scope_key='file' AND q.stage_name='m2_read' AND q.state='done' FOR UPDATE""", task=task_id)
        if not stage:
            return None
        row = one(conn, """SELECT s.sha,f.body,cp.body AS checkpoint,e.body AS evidence FROM product_task t
            JOIN source_file s ON s.id=t.source_id JOIN file_bytes f ON f.sha=s.sha
            JOIN file_bytes cp ON cp.sha=:input JOIN file_bytes e ON e.sha=:output WHERE t.id=:task""",
                  task=task_id, input=stage["input_sha"], output=stage["output_sha"])
        evidence = json.loads(checked_blob(row["evidence"], stage["output_sha"]))
        checkpoint = json.loads(checked_blob(row["checkpoint"], stage["input_sha"]))
        if evidence["source_sha256"] != row["sha"].hex() or checkpoint["source_sha256"] != row["sha"].hex():
            raise ValueError("图纸与已保存证据不匹配")
        crops = printed_candidates(checked_blob(row["body"], row["sha"]), checkpoint, evidence)
        if not crops:
            return []
        evidence["images"].update({key: put_bytes(conn, image).hex() for key, image in crops.items()})
        output = put_bytes(conn, encode_json(evidence))
        conn.execute(text("UPDATE task_stage SET output_sha=:output WHERE id=:id"), {"id": stage["id"], "output": output})
        parts = [item["part_number"] for item in evidence["products"] if any(key in crops for key in item["regions"])]
        event(conn, stage, "printed_candidates_saved", {"previous_sha256": stage["output_sha"].hex(),
              "output_sha256": output.hex(), "part_numbers": parts})
        return parts


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("task_ids", nargs="+", type=int)
    print(json.dumps({task: supplement(task) for task in parser.parse_args().task_ids}))
