import json

from sqlalchemy import text

from app.db import engine, one
from app.ingest import put_bytes
from app.m1_sample import encode_json
from app.queue import event


def published_receipt(conn, stage, step_sha=None, *, require_fence=True):
    version = one(conn, "SELECT * FROM m2_version WHERE task_id=:task FOR UPDATE", task=stage["task_id"])
    if not version or version["state"] != "published" or version["basis_sha"] != stage["input_sha"]:
        return None
    receipt = one(conn, """SELECT * FROM m3_publication WHERE version_id=:version AND geometry_revision=:revision""",
                  version=version["id"], revision=version["geometry_revision"])
    if not receipt or receipt["stage_id"] != stage["id"] or require_fence and receipt["fence"] != stage["fence"]:
        return None
    if step_sha and receipt["step_sha"] != step_sha:
        if not require_fence:
            return None
        raise ValueError("发布回执与重送字节不符")
    if not require_fence:
        return {"state": "published", "reused": True, "version_id": version["id"], "geometry_revision": version["geometry_revision"],
                "stage_id": receipt["stage_id"], "fence": receipt["fence"],
                **{f"{kind}_sha256": receipt[f"{kind}_sha"].hex() for kind in ("geometry", "html", "step", "report")}}
    return {"state": "published", "report_sha256": receipt["report_sha"].hex(), "reused": True}


def next_revision(task_id):
    from app.m2_work import enqueue

    with engine.begin() as conn:
        stage = one(conn, "SELECT * FROM task_stage WHERE task_id=:task AND stage_name='m2_cad' ORDER BY id DESC LIMIT 1 FOR UPDATE", task=task_id)
        version = one(conn, "SELECT * FROM m2_version WHERE task_id=:task FOR UPDATE", task=task_id)
        if not version or version["state"] != "rejected" or not stage or stage["state"] != "waiting":
            raise ValueError("仅可为已拒绝的几何创建修订")
        archived = {key: value.hex() if isinstance(value, bytes) else value.isoformat() if hasattr(value, "isoformat") else value
                    for key, value in dict(version).items()}
        conn.execute(text("INSERT INTO m3_revision (version_id,geometry_revision,evidence) VALUES (:id,:revision,:evidence)"),
                     {"id": version["id"], "revision": version["geometry_revision"], "evidence": encode_json(archived).decode()})
        revision = f"G{int(version['geometry_revision'][1:]) + 1}"
        basis = json.loads(one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=version["basis_sha"])["body"])
        basis["geometry_revision"] = revision
        basis_sha = put_bytes(conn, encode_json(basis))
        checklist = json.loads(one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=version["checklist_sha"])["body"])
        checklist["basis_sha256"] = basis_sha.hex()
        checklist_sha = put_bytes(conn, encode_json(checklist))
        conn.execute(text("""UPDATE m2_version SET geometry_revision=:revision,basis_sha=:basis,checklist_sha=:checks,
            geometry_sha=NULL,snapshot_sha=NULL,html_sha=NULL,step_sha=NULL,report_sha=NULL,html_check=NULL,
            step_check=NULL,state='building',published_at=NULL WHERE id=:id"""),
                     {"id": version["id"], "revision": revision, "basis": basis_sha, "checks": checklist_sha})
        following = enqueue(conn, task_id, "m2_cad", basis_sha)
        conn.execute(text("UPDATE product_task SET state='cad_queued' WHERE id=:id"), {"id": task_id})
        event(conn, following, "geometry_revision_created", {"previous": version["geometry_revision"], "revision": revision,
                                                           "basis_sha256": basis_sha.hex(), "previous_stage": stage["id"]})
        return {"geometry_revision": revision, "basis_sha256": basis_sha.hex()}
