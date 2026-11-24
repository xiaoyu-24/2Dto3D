import base64
import hashlib
import json
import os
import re
import sys
from urllib.error import URLError

from sqlalchemy import text

from app.db import engine, one
from app.ingest import put_bytes
from app.m1_read import harness
from app.m3_models import Deferred, call as recorded_call, defer
from app.m1_sample import checked_blob, encode_json
from app.m2_versions import passed_check
from app.m2_work import blob, finish_stage
from app.m3_revisions import next_revision, published_receipt
from app.queue import claim, event, live_stage, renew

JOB = "m12-a04-m16-a0"
ISOLATION = {"uid": 65534, "interfaces": ["lo"], "root_read_only": True}


def fingerprint(body):
    return hashlib.sha256(body).hexdigest()


def packed(body):
    return base64.b64encode(body).decode()


def unpacked(value):
    return base64.b64decode(value, validate=True)


def stage_wire(stage):
    return {**{key: stage[key] for key in ("id", "task_id", "fence", "attempts")}, "owner": stage["owner"].decode(),
            "input_sha": stage["input_sha"].hex()}


def version_for(conn, stage):
    if not live_stage(conn, stage):
        raise ValueError("CAD 执行租约已失效")
    version = one(conn, "SELECT * FROM m2_version WHERE task_id=:task FOR UPDATE", task=stage["task_id"])
    if not version or version["basis_sha"] != stage["input_sha"] or version["state"] in ("published", "rejected"):
        raise ValueError("CAD 阶段与当前版本不符")
    return version


def inputs(stage):
    with engine.begin() as conn:
        version = version_for(conn, stage)
        result = {key: version[key] for key in ("part_number", "version", "geometry_revision")}
        result.update(basis=packed(blob(conn, version["basis_sha"])), checklist=packed(blob(conn, version["checklist_sha"])),
                      snapshot=json.loads(blob(conn, version["snapshot_sha"])) if version["snapshot_sha"] else None,
                      html_sha256=version["html_sha"].hex() if passed_check(version, "html") else None,
                      geometry_sha256=version["geometry_sha"].hex() if version["geometry_sha"] else None)
        if result["snapshot"]:
            event(conn, stage, "cad_checkpoint_reused", {key: result[key] for key in ("geometry_sha256", "html_sha256")})
        return result


def selection(stage, job_sha):
    with engine.begin() as conn:
        version = version_for(conn, stage)
        project_id = version["project_id"]
        basis = json.loads(blob(conn, version["basis_sha"]))
    try:
        route = harness("/route").get("route")
    except (URLError, TimeoutError, ValueError):
        route = None
    if not route or str(project_id) != os.environ.get("M1_VISION_PROJECT_ID", "1"):
        raise ValueError("当前项目的已获准 Harness 渠道不可用")
    prompt = (f"Return exactly one ID, without explanation or Markdown: {JOB} or no-match. "
              f"Drawing {basis['drawing_no']} {basis['revision']}, {basis['part_number']}, "
              f"{basis['pin_count']} contacts, {basis['total_length_mm']} mm. "
              f"{JOB} supports ENDE05 A0 M12 A-code M16, 4 contacts 24.70 mm or 5 contacts 26.70 mm. "
              "Match drawing, part family, contact count and length. Output the ID only.")
    payload = {"prompt": prompt, "images": []}

    def selected(audit):
        return (audit.get("finish_reason") == "completed" and audit.get("request_id")
                and (audit.get("response") or "").strip().strip('`"') == JOB)

    audit = recorded_call(stage, project_id, route, "cad_select", payload)
    if not selected(audit) and stage["attempts"] > 1 and (audit.get("response") or "").strip() != "no-match":
        audit = recorded_call(stage, project_id, route, "cad_select", payload, attempt=2)
    if not selected(audit):
        raise ValueError("Harness 未返回可用的同族作业选择或调用待对账")
    return {"job": JOB, "basis_sha256": stage["input_sha"].hex(), "job_sha256": job_sha, "model_run": audit}


def frozen(stage, snapshot):
    with engine.begin() as conn:
        version = version_for(conn, stage)
        manifest_body = unpacked(snapshot["geometry.json"])
        manifest = json.loads(manifest_body)
        chosen = json.loads(unpacked(snapshot["selection.json"]))
        required = {"geometry.json", "selection.json", "job.py", *manifest["brep_sha256"]}
        if (set(snapshot) != required or manifest["environment"] != ISOLATION or manifest["variant"] != "nominal"
                or set(manifest["brep_sha256"]) != {"A.brep", "B.brep", "C.brep", "D.brep", "E.brep", "panel.brep"}
                or manifest["basis_sha256"] != version["basis_sha"].hex() or manifest["version"] != version["version"]
                or manifest["part_number"] != version["part_number"] or manifest["geometry_revision"] != version["geometry_revision"]
                or chosen["job"] != JOB or not chosen["model_run"].get("request_id")
                or chosen["basis_sha256"] != manifest["basis_sha256"] or chosen["job_sha256"] != manifest["job_sha256"]):
            raise ValueError("冻结几何和作业选择与当前版本不一致")
        expected = {**manifest["brep_sha256"], "selection.json": manifest["selection_sha256"], "job.py": manifest["job_sha256"]}
        for name, digest in expected.items():
            checked_blob(unpacked(snapshot[name]), bytes.fromhex(digest))
        sha = bytes.fromhex(fingerprint(manifest_body))
        if version["geometry_sha"] and version["geometry_sha"] != sha:
            raise ValueError("当前几何修订已有不同指纹")
        if not version["geometry_sha"]:
            snapshot_sha = put_bytes(conn, encode_json(snapshot))
            put_bytes(conn, manifest_body)
            conn.execute(text("UPDATE m2_version SET geometry_sha=:geo,snapshot_sha=:snapshot WHERE id=:id"),
                         {"geo": sha, "snapshot": snapshot_sha, "id": version["id"]})
            event(conn, stage, "geometry_frozen", {"geometry_sha256": sha.hex(), "snapshot_sha256": snapshot_sha.hex()})
        return sha.hex()


def validate_identity(version, body, kind):
    pattern = rb'<script id="scene" type="application/json">([^\n]+)</script>' if kind == "html" else rb"/\* M1 ([^\n]+) \*/"
    match = re.search(pattern, body if kind == "html" else body[:2048])
    if not match:
        raise ValueError("成品缺少可追溯几何标识")
    entry = json.loads(match[1])
    identity = entry["identity"] if kind == "html" else entry
    if (any(identity[key] != version[key] for key in ("part_number", "version", "geometry_revision"))
            or identity["geometry_sha256"] != version["geometry_sha"].hex() or identity["basis_sha256"] != version["basis_sha"].hex()
            or identity["nut_installed_pose"] is not None):
        raise ValueError("成品身份标识与冻结几何不一致")
    return entry


def store_html(stage, body, check):
    with engine.begin() as conn:
        version = version_for(conn, stage)
        scene = validate_identity(version, body, "html")
        geometry = json.loads(blob(conn, version["geometry_sha"]))
        if ([part["id"] for part in scene["parts"]] != [part["id"] for part in geometry["components"]]
                or check.get("passed") is not True or check.get("channel") != "msedge" or check.get("offline") is not True
                or check.get("scheme") != "file:" or check.get("remote_requests") != [] or check.get("page_errors") != []
                or check.get("geometry_sha256") != version["geometry_sha"].hex() or check.get("html_sha256") != fingerprint(body)):
            raise ValueError("HTML 离线验收不通过或指纹不符")
        candidate = bytes.fromhex(fingerprint(body))
        if version["html_sha"] and version["html_sha"] != candidate:
            raise ValueError("当前几何修订已有不同 HTML")
        if not version["html_sha"]:
            sha = put_bytes(conn, body)
            conn.execute(text("UPDATE m2_version SET html_sha=:sha,html_check=:check,state='partial' WHERE id=:id"),
                         {"id": version["id"], "sha": sha, "check": encode_json(check).decode()})
            conn.execute(text("UPDATE product_task SET state='partial' WHERE id=:id"), {"id": stage["task_id"]})
            event(conn, stage, "html_verified", {"geometry_sha256": check["geometry_sha256"], "html_sha256": sha.hex()})
        return candidate.hex()


def report(version, check):
    html = json.loads(version["html_check"])
    lines = [f"# 验收报告 · {version['part_number']} / {version['version']} / {version['geometry_revision']}", "", "结论为通过。", "",
             f"- 冻结几何 SHA-256 `{check['geometry_sha256']}`", f"- 建模依据 SHA-256 `{version['basis_sha'].hex()}`",
             f"- 独立清单 SHA-256 `{check['checklist_sha256']}`", f"- STEP SHA-256 `{check['step_sha256']}`",
             f"- HTML SHA-256 `{html['html_sha256']}`", f"- 独立回读 FreeCAD {check['freecad']} / OCCT {check['occt']}",
             f"- Edge {html['edge_version']} 从本地文件断网通过模型可见、旋转、缩放、视角及部件显隐。", "",
             "| 项目 | 来源 | 原图区域 | 独立回读 |", "|---|---|---|---|"]
    for measurement in check["checks"]:
        actual = encode_json(measurement["measured"]).decode()
        lines.append(f"| {measurement['name']} | {measurement['kind']} | {', '.join(measurement['evidence_regions'])} | `{actual}` |")
    lines += ["", "图纸未标的针心位置和装配细节按冻结试建依据验收。独立螺母位移用于分件展示。", ""]
    return "\n".join(lines).encode()


def store_step(stage, body, check):
    with engine.begin() as conn:
        one(conn, "SELECT id FROM task_stage WHERE id=:id FOR UPDATE", id=stage["id"])
        previous = published_receipt(conn, stage, bytes.fromhex(fingerprint(body)))
        if previous:
            return previous
        version = version_for(conn, stage)
        validate_identity(version, body, "step")
        checklist = json.loads(blob(conn, version["checklist_sha"]))
        specifications = {item["name"]: item["expected"] for item in checklist["checks"]}
        measured = {item["name"]: item["expected"] for item in check["checks"]}
        if (check["geometry_sha256"] != version["geometry_sha"].hex() or check["step_sha256"] != fingerprint(body)
                or check["checklist_sha256"] != version["checklist_sha"].hex() or check["environment"] != ISOLATION
                or specifications != measured or len(check["checks"]) != len(specifications)):
            raise ValueError("STEP 实测结果未绑定当前几何和独立清单")
        step = put_bytes(conn, body)
        if check["passed"] is not True or any(item["passed"] is not True for item in check["checks"]):
            conn.execute(text("UPDATE m2_version SET state='rejected',step_sha=:sha,step_check=:check WHERE id=:id"),
                         {"id": version["id"], "sha": step, "check": encode_json(check).decode()})
            finish_stage(conn, stage, "waiting", "geometry_rejected", "geometry_rejected", {"step_sha256": step.hex()},
                         reason="独立测量未通过，当前几何的下载已撤回")
            return {"state": "rejected"}
        if not passed_check(version, "html"):
            raise ValueError("当前几何缺少通过的 HTML 验收")
        report_sha = put_bytes(conn, report(version, check))
        verification = encode_json({**check, "report_sha256": report_sha.hex()}).decode()
        conn.execute(text("""UPDATE m2_version SET step_sha=:step,step_check=:check,report_sha=:report,state='published',
            published_at=UTC_TIMESTAMP(6) WHERE id=:id"""),
                     {"id": version["id"], "step": step, "check": verification, "report": report_sha})
        conn.execute(text("""INSERT INTO m3_publication
            (version_id,geometry_revision,stage_id,fence,geometry_sha,html_sha,step_sha,report_sha)
            VALUES (:version,:revision,:stage,:fence,:geo,:html,:step,:report)"""),
                     {"version": version["id"], "revision": version["geometry_revision"], "stage": stage["id"],
                      "fence": stage["fence"], "geo": version["geometry_sha"], "html": version["html_sha"],
                      "step": step, "report": report_sha})
        finish_stage(conn, stage, "done", "published", "published", {"geometry_sha256": check["geometry_sha256"],
                     "step_sha256": step.hex(), "html_sha256": version["html_sha"].hex(), "report_sha256": report_sha.hex()}, output=report_sha)
        return {"state": "published", "report_sha256": report_sha.hex()}


def fail(stage, reason):
    with engine.begin() as conn:
        version = version_for(conn, stage)
        state = "partial" if passed_check(version, "html") else "failed"
        conn.execute(text("UPDATE m2_version SET state=:state WHERE id=:id"), {"state": state, "id": version["id"]})
        finish_stage(conn, stage, "failed", state, "cad_failed", {"reason": reason, "retained_html": state == "partial"}, reason=reason)
    return True


def execute(data):
    action = data["action"]
    if action == "revision":
        return next_revision(data["task_id"])
    if action == "claim":
        stage = claim(data["owner"], cad=True)
        return stage_wire(stage) if stage else None
    stage = data["stage"].copy()
    stage.update(owner=stage["owner"].encode(), input_sha=bytes.fromhex(stage["input_sha"]))
    if action == "receipt":
        with engine.begin() as conn:
            return published_receipt(conn, stage, bytes.fromhex(data["step_sha256"]), require_fence=False)
    if action == "renew":
        return renew(stage)
    if action == "inputs":
        return inputs(stage)
    if action == "selection":
        return selection(stage, data["job_sha256"])
    if action == "frozen":
        return frozen(stage, data["snapshot"])
    if action == "html":
        return store_html(stage, unpacked(data["body"]), data["check"])
    if action == "step":
        return store_step(stage, unpacked(data["body"]), data["check"])
    if action == "fail":
        return fail(stage, data["reason"])
    raise ValueError("未知 CAD 动作")


if __name__ == "__main__":
    try:
        data = json.load(sys.stdin)
        print(json.dumps({"result": execute(data)}, ensure_ascii=False))
    except Deferred as failure:
        stage = data["stage"].copy()
        stage.update(owner=stage["owner"].encode(), input_sha=bytes.fromhex(stage["input_sha"]))
        defer(stage, failure)
        print(json.dumps({"error": failure.reason}, ensure_ascii=False))
        sys.exit(1)
    except ValueError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        sys.exit(1)
