import hashlib
import json
import re
import sys
from pathlib import Path

from sqlalchemy import text

from app.db import engine, one
from app.ingest import put_bytes
from app.m1_acceptance import VERSION
from app.m1_read import current_basis
from app.m1_report import acceptance_report
from app.m1_sample import SOURCE, checked_blob
from app.queue import event

GEOMETRY_REVISION = "G1"
ARTIFACTS = {"html": ("html", "text/html"), "step": ("step", "model/step"), "report": ("md", "text/markdown")}


def fingerprint(body):
    return {"sha256": hashlib.sha256(body).hexdigest(), "byte_count": len(body)}


def filename(kind):
    return f"{VERSION.replace('/', '-')}-{GEOMETRY_REVISION}.{ARTIFACTS[kind][0]}"


def load_delivery(conn, task_id, basis):
    if not basis:
        return None
    return one(conn, """SELECT d.*, html.byte_count AS html_bytes, step.byte_count AS step_bytes,
        report.byte_count AS report_bytes FROM m1_delivery d
        JOIN file_bytes html ON html.sha=d.html_sha JOIN file_bytes step ON step.sha=d.step_sha
        JOIN file_bytes report ON report.sha=d.report_sha
        WHERE d.task_id=:task AND d.basis_sha=:basis AND d.checklist_sha=:checklist""",
        task=task_id, basis=basis["basis_sha"], checklist=basis["checklist_sha"])


def delivery_metadata(delivery):
    return {"version": VERSION, "geometry_revision": GEOMETRY_REVISION, "geometry_sha256": delivery["geometry_sha"].hex(),
            "published_at": delivery["published_at"], "files": {kind: {
                "filename": filename(kind), "sha256": delivery[f"{kind}_sha"].hex(),
                "byte_count": delivery[f"{kind}_bytes"]} for kind in ARTIFACTS}}


def read_bundle(work, cad, checklist, basis_sha, checklist_sha):
    def retain(path, expected=None):
        body = path.read_bytes()
        if expected is not None:
            checked_blob(body, bytes.fromhex(expected))
        retained.append(body)
        return json.loads(body) if path.suffix == ".json" else None

    def require(condition, reason):
        if not condition:
            raise ValueError(reason)

    retained = []
    geometry_path = work / "nominal" / "geometry.json"
    geometry = retain(geometry_path)
    geometry_sha = fingerprint(retained[-1])["sha256"]
    require((geometry["schema"], geometry["version"], geometry["geometry_revision"], geometry["variant"],
             geometry["source_sha256"], geometry["basis_sha256"]) ==
            ("m1-geometry-v1", VERSION, GEOMETRY_REVISION, "nominal", SOURCE, basis_sha), "冻结几何与任务不一致")
    selection = retain(work / "selection.json", geometry["selection_sha256"])
    require(selection["job_sha256"] == geometry["job_sha256"] and selection["basis_sha256"] == basis_sha
            and selection["model_run"]["request_id"], "缺少本次 Harness 作业选择凭证")
    retain(cad / "job.py", geometry["job_sha256"])
    require(set(geometry["brep_sha256"]) == {f"{code}.brep" for code in "ABCDE"} | {"panel.brep"}, "部件快照清单不符")
    for name, digest in geometry["brep_sha256"].items():
        retain(geometry_path.parent / name, digest)

    files = {kind: (work / "export" / f"nominal.{kind}").read_bytes() for kind in ("step", "html")}
    artifacts = {kind: {"filename": filename(kind), **fingerprint(body)} for kind, body in files.items()}
    measurements = retain(work / "verify" / "nominal.json")
    edge = retain(work / "browser" / "edge-offline.json")
    for isolation in (geometry["environment"], measurements["environment"]):
        require(isolation == {"uid": 65534, "interfaces": ["lo"], "root_read_only": True}, "CAD 执行环境不符合隔离约定")
    required_checks = {item["name"] for item in checklist["checks"]}
    require(measurements["passed"] is True and measurements["geometry_sha256"] == geometry_sha
            and measurements["step_sha256"] == artifacts["step"]["sha256"] and measurements["checklist_sha256"] == checklist_sha
            and {item["name"] for item in measurements["checks"] if item["passed"] is True} == required_checks
            and len(measurements["checks"]) == len(required_checks), "STEP 验收与当前成品不一致")
    require(edge["passed"] is True and edge["offline"] is True and edge["channel"] == "msedge" and edge["scheme"] == "file:"
            and not edge["remote_requests"] and not edge["page_errors"] and edge["geometry_sha256"] == geometry_sha
            and edge["html_sha256"] == artifacts["html"]["sha256"], "离线 Edge 验收与当前成品不一致")
    header = json.loads(re.search(rb"/\* M1 ([^\n]+) \*/", files["step"][:2048])[1])
    scene = json.loads(re.search(rb'<script id="scene" type="application/json">([^\n]+)</script>', files["html"])[1])
    require(header == scene["identity"] and header["geometry_sha256"] == geometry_sha
            and header["nut_installed_pose"] is None
            and [part["id"] for part in scene["parts"]] == [part["id"] for part in geometry["components"]],
            "STEP、HTML 与冻结部件清单不一致")

    negatives = {}
    for variant, gate in (("swapped", "端面视向与触点数"), ("26.70", "本体轴向总长"), ("26.90", "本体轴向总长")):
        negative = retain(work / "verify" / f"{variant}.json")
        rejected = {item["name"]: item["measured"] for item in negative["checks"] if item["passed"] is False}
        measured = {item["name"]: item["measured"] for item in negative["checks"]}
        require(negative["passed"] is False and negative["checklist_sha256"] == checklist_sha and gate in rejected
                and negative["step_sha256"] == fingerprint((work / "export" / f"{variant}.step").read_bytes())["sha256"]
                and negative["step_sha256"] != artifacts["step"]["sha256"]
                and measured["端面视向与触点数"]["pin_count"] == 4
                and (variant == "swapped" or measured["本体轴向总长"]["length_mm"] == float(variant)),
                f"缺少可复验的负例 {variant}")
        negatives[variant] = {"step_sha256": negative["step_sha256"], "rejected_at": gate, "measured": rejected[gate]}

    files["report"] = acceptance_report(geometry, geometry_sha, checklist_sha, selection, measurements, edge, negatives, artifacts)
    return geometry_sha, files, retained


def publish(task_id, work, cad):
    with engine.connect() as conn:
        stage = one(conn, """SELECT id FROM task_stage WHERE task_id=:task AND stage_name='vision_read'
            AND state='done'""", task=task_id)
        basis = current_basis(conn, stage["id"]) if stage else None
        if not basis:
            raise ValueError("任务尚无当前 M1.3 冻结依据")
        checklist_sha = basis["checklist_sha"]
        stored = one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=checklist_sha)
        checklist = json.loads(checked_blob(stored["body"], checklist_sha))
    geometry_sha, files, retained = read_bundle(work, cad, checklist, basis["basis_sha"].hex(), checklist_sha.hex())
    with engine.begin() as conn:
        stage = one(conn, "SELECT id,fence,state FROM task_stage WHERE id=:id FOR UPDATE", id=stage["id"])
        current = current_basis(conn, stage["id"])
        task = one(conn, """SELECT t.state, s.sha FROM product_task t JOIN source_file s ON s.id=t.source_id
            WHERE t.id=:task FOR UPDATE""", task=task_id)
        if (stage["state"] != "done" or not current or current["basis_sha"] != basis["basis_sha"]
                or current["checklist_sha"] != checklist_sha or task["sha"].hex() != SOURCE
                or task["state"] not in ("basis_frozen", "published")):
            raise ValueError("发布前任务或冻结依据已改变")
        params = {"task": task_id, "basis_sha": basis["basis_sha"], "checklist_sha": checklist_sha,
                  "geometry_sha": bytes.fromhex(geometry_sha),
                  **{f"{kind}_sha": hashlib.sha256(body).digest() for kind, body in files.items()}}
        saved = load_delivery(conn, task_id, current)
        if saved:
            if any(saved[key] != value for key, value in params.items() if key != "task"):
                raise ValueError("任务已有不同字节的首件发布记录")
            return delivery_metadata(saved)
        for body in [*retained, *files.values()]:
            put_bytes(conn, body)
        conn.execute(text("""INSERT INTO m1_delivery
            (task_id,basis_sha,checklist_sha,geometry_sha,step_sha,html_sha,report_sha)
            VALUES (:task,:basis_sha,:checklist_sha,:geometry_sha,:step_sha,:html_sha,:report_sha)"""), params)
        conn.execute(text("UPDATE product_task SET state='published' WHERE id=:task"), {"task": task_id})
        from app.m2_versions import register_first_delivery

        register_first_delivery(conn, task_id)
        saved = delivery_metadata(load_delivery(conn, task_id, current))
        event(conn, stage, "m1_published", {"geometry_sha256": geometry_sha, "files": saved["files"]})
    return saved


if __name__ == "__main__":
    task_id, work, cad = sys.argv[1:]
    print(json.dumps(publish(int(task_id), Path(work), Path(cad)), ensure_ascii=False, default=str))
