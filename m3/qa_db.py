import base64
import json
import os
import sys
import uuid

sys.path.insert(0, "/app")

from sqlalchemy import event as sql_event, text
from sqlalchemy.exc import OperationalError

from app.db import engine, one, rows
from app.ingest import put_bytes
from app.m1_sample import encode_json
from app.m2_cad import execute
from app.m2_work import blob, enqueue, save_read
from app.queue import event

assert os.environ.get("M3_QA") == "1", "QA-only entry"


def seed(label, origin):
    with engine.begin() as conn:
        existing = one(conn, "SELECT id,task_id FROM m2_version WHERE version=:version", version=f"A0-M3-{label}")
        if existing:
            return dict(existing)
        batch = one(conn, "SELECT id FROM upload_batch WHERE request_key='m3-faults'")
        if not batch:
            batch_id = conn.execute(text("INSERT INTO upload_batch (project_id,user_id,request_key) VALUES (1,1,'m3-faults')")).lastrowid
            source = conn.execute(text("""INSERT INTO source_file (batch_id,client_file_key,filename,sha,selection_json,state)
                SELECT :batch,'m12','M3-M12.pdf',sha,selection_json,state FROM source_file WHERE id=5"""), {"batch": batch_id}).lastrowid
            file_task = conn.execute(text("INSERT INTO product_task (source_id,scope_key,state,budget_cny) VALUES (:source,'file','selection_ready',10)"),
                                     {"source": source}).lastrowid
            for name in ("pdf_extract", "m2_read"):
                previous = one(conn, "SELECT * FROM task_stage WHERE task_id=5 AND stage_name=:name", name=name)
                stage = enqueue(conn, file_task, name, previous["input_sha"])
                conn.execute(text("UPDATE task_stage SET output_sha=:output,state='done' WHERE id=:id"),
                             {"id": stage["id"], "output": previous["output_sha"]})
                event(conn, stage, "qa_checkpoint_reused", {"origin_task": 5, "sha256": previous["output_sha"].hex()})
        else:
            batch_id = batch["id"]
            source = one(conn, "SELECT id FROM source_file WHERE batch_id=:id", id=batch_id)["id"]
        original = one(conn, "SELECT * FROM m2_version WHERE task_id=:id", id=origin)
        specification = one(conn, "SELECT * FROM m2_product WHERE task_id=:id", id=origin)
        task = conn.execute(text("INSERT INTO product_task (source_id,scope_key,state,budget_cny) VALUES (:source,:scope,'cad_queued',10)"),
                            {"source": source, "scope": f"m3:{label}".encode()}).lastrowid
        conn.execute(text("INSERT INTO m2_product VALUES (:task,:part,:specification)"),
                     {"task": task, "part": original["part_number"], "specification": specification["specification"]})
        version_name = f"A0-M3-{label}"
        basis = json.loads(blob(conn, original["basis_sha"]))
        basis.update(version=version_name, geometry_revision="G1")
        basis_sha = put_bytes(conn, encode_json(basis))
        checklist = json.loads(blob(conn, original["checklist_sha"]))
        checklist.update(version=version_name, basis_sha256=basis_sha.hex())
        checks = put_bytes(conn, encode_json(checklist))
        version = conn.execute(text("""INSERT INTO m2_version (task_id,project_id,part_number,version,basis_sha,checklist_sha)
            VALUES (:task,1,:part,:version,:basis,:checks)"""),
                               {"task": task, "part": original["part_number"], "version": version_name, "basis": basis_sha, "checks": checks}).lastrowid
        stage = enqueue(conn, task, "m2_cad", basis_sha)
        event(conn, stage, "qa_basis_reused", {"origin_task": origin, "basis_sha256": basis_sha.hex()})
        return {"task_id": task, "id": version, "batch_id": batch_id, "stage_id": stage["id"]}


def audit():
    with engine.connect() as conn:
        tables = ("product_task", "task_stage", "stage_event", "m1_call", "m2_version", "m3_publication", "m3_revision")
        return {table: [dict(item) for item in rows(conn, f"SELECT * FROM {table}")] for table in tables}


def plate_seed():
    with engine.begin() as conn:
        existing = one(conn, "SELECT id,task_id FROM m2_version WHERE version='A0-M3-plate'")
        if existing:
            return dict(existing)
        basis = {"schema": "m3-fixture-v1", "part_number": "QA-PLATE-ASYMMETRIC", "version": "A0-M3-plate",
                 "geometry_revision": "G1", "datum": "plate-center +X right +Y up +Z front"}
        basis_sha = put_bytes(conn, encode_json(basis))
        checks = [{"name": "孔位有符号坐标", "kind": "drawing", "expected": [
            {"radius_mm": 1, "center_xy_mm": [-12, -6]}, {"radius_mm": 1.5, "center_xy_mm": [7, 4]},
            {"radius_mm": 2, "center_xy_mm": [3, -9]}]},
            {"name": "外形与实体", "kind": "drawing", "expected": [40, 30, 3, 1, True]},
            {"name": "身份与基准", "kind": "drawing", "expected": {"part_number": basis["part_number"], "unit": "mm", "datum": basis["datum"]}}]
        checklist = put_bytes(conn, encode_json({"schema": "m3-fixture-checklist", "basis_sha256": basis_sha.hex(),
                                                "version": basis["version"], "checks": checks}))
        batch = one(conn, "SELECT id FROM upload_batch WHERE request_key='m3-faults'")["id"]
        source = conn.execute(text("""INSERT INTO source_file (batch_id,client_file_key,filename,sha,selection_json,state)
            VALUES (:batch,'plate','M3-plate-fixture.json',:sha,'{\"workflow\":\"m2\"}','accepted')"""), {"batch": batch, "sha": basis_sha}).lastrowid
        task = conn.execute(text("INSERT INTO product_task (source_id,scope_key,state,budget_cny) VALUES (:source,'file','cad_queued',10)"),
                            {"source": source}).lastrowid
        version = conn.execute(text("""INSERT INTO m2_version (task_id,project_id,part_number,version,basis_sha,checklist_sha)
            VALUES (:task,1,:part,:version,:basis,:checks)"""),
                               {"task": task, "part": basis["part_number"], "version": basis["version"], "basis": basis_sha, "checks": checklist}).lastrowid
        stage = enqueue(conn, task, "m2_cad", basis_sha)
        return {"task_id": task, "id": version, "batch_id": batch, "stage_id": stage["id"]}


def main(data):
    action = data["action"]
    if action == "seed":
        return seed(data["label"], data["origin"])
    if action == "audit":
        return audit()
    if action == "permission_account":
        from app.auth import password_hash
        login, password = "m3-" + uuid.uuid4().hex, uuid.uuid4().hex
        with engine.begin() as conn:
            user = conn.execute(text("INSERT INTO app_user (login,password_hash) VALUES (:login,:hash)"),
                                {"login": login, "hash": password_hash.hash(password)}).lastrowid
            project = conn.execute(text("INSERT INTO project (name) VALUES ('M3 permission fixture')")).lastrowid
            conn.execute(text("INSERT INTO project_member VALUES (:project,:user,'operator')"), {"project": project, "user": user})
        return {"login": login, "password": password, "project": project}
    if action == "plate_seed":
        return plate_seed()
    if action == "plate_freeze":
        from app.m2_cad import version_for
        stage = data["stage"].copy()
        stage.update(owner=stage["owner"].encode(), input_sha=bytes.fromhex(stage["input_sha"]))
        with engine.begin() as conn:
            version = version_for(conn, stage)
            body = base64.b64decode(data["geometry"])
            geometry = json.loads(body)
            assert version["part_number"] == "QA-PLATE-ASYMMETRIC" and geometry["schema"] == "m3-plate-fixture"
            assert geometry["basis_sha256"] == version["basis_sha"].hex() and geometry["geometry_revision"] == version["geometry_revision"]
            sha = put_bytes(conn, body)
            conn.execute(text("UPDATE m2_version SET geometry_sha=:sha WHERE id=:id"), {"sha": sha, "id": version["id"]})
            event(conn, stage, "qa_fixture_frozen", {"geometry_sha256": sha.hex(), "datum": geometry["datum"]})
            return sha.hex()
    if action == "snapshot":
        with engine.connect() as conn:
            saved = one(conn, "SELECT snapshot_sha FROM m2_version WHERE task_id=:id", id=data["task_id"])
            return json.loads(blob(conn, saved["snapshot_sha"]))
    if action == "reading":
        with engine.connect() as conn:
            saved = one(conn, "SELECT output_sha FROM task_stage WHERE task_id=5 AND stage_name='m2_read'")
            return json.loads(blob(conn, saved["output_sha"]))["reading"]
    if action == "interruption_seed":
        with engine.begin() as conn:
            batch = conn.execute(text("INSERT INTO upload_batch (project_id,user_id,request_key) VALUES (1,1,'m3-interruption')")).lastrowid
            source = conn.execute(text("""INSERT INTO source_file (batch_id,client_file_key,filename,sha,selection_json,state)
                SELECT :batch,'interrupt','M3-worker-interruption.pdf',sha,selection_json,state FROM source_file WHERE id=5"""),
                                  {"batch": batch}).lastrowid
            task = conn.execute(text("INSERT INTO product_task (source_id,scope_key,state,budget_cny) VALUES (:source,'file','read_queued',10)"),
                                {"source": source}).lastrowid
            previous = one(conn, "SELECT * FROM task_stage WHERE task_id=5 AND stage_name='pdf_extract'")
            parsed = enqueue(conn, task, "pdf_extract", previous["input_sha"])
            conn.execute(text("UPDATE task_stage SET state='done',output_sha=:sha WHERE id=:id"),
                         {"sha": previous["output_sha"], "id": parsed["id"]})
            stage = enqueue(conn, task, "m2_read", previous["output_sha"])
            return {"task_id": task, "stage_id": stage["id"], "batch_id": batch}
    if action == "stage":
        with engine.connect() as conn:
            return dict(one(conn, "SELECT *,UTC_TIMESTAMP(6) AS now FROM task_stage WHERE id=:id", id=data["stage_id"]))
    if action == "before_commit":
        def fail(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO m3_publication"):
                raise OperationalError(statement, {}, RuntimeError("QA transaction write failure"))
        sql_event.listen(engine, "before_cursor_execute", fail)
        try:
            return execute({**data, "action": "step"})
        finally:
            sql_event.remove(engine, "before_cursor_execute", fail)
    if action == "stale_read":
        stage = data["stage"]
        stage.update(owner=stage["owner"].encode(), input_sha=bytes.fromhex(stage["input_sha"]))
        return save_read(stage, {"late": True}, {}, final=True)
    return execute(data)


if __name__ == "__main__":
    try:
        result = main(json.load(sys.stdin))
        print(json.dumps({"result": result}, ensure_ascii=True,
                         default=lambda value: value.hex() if isinstance(value, bytes) else str(value)))
    except Exception as error:
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}))
        sys.exit(1)
