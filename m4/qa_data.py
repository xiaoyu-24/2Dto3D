import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/app")
from sqlalchemy import text
from app.auth import password_hash
from app.db import engine, one, rows
from app.ingest import accept_file, create_batch
from app.m1_sample import encode_json
from app.m2_cad import execute
from app.m2_work import enqueue

assert os.environ.get("M4_QA") == "1", "Independent QA only"


def wire(value):
    return value.hex() if isinstance(value, bytes) else str(value)


def audit():
    with engine.connect() as conn:
        tables = [row["TABLE_NAME"] for row in rows(conn, "SELECT TABLE_NAME FROM information_schema.tables WHERE table_schema=DATABASE()")]
        records = {}
        for name in tables:
            if name == "user_session":
                continue
            if name == "file_bytes":
                data = rows(conn, "SELECT LOWER(HEX(sha)) AS sha,byte_count,LOWER(SHA2(body,256)) AS actual FROM file_bytes ORDER BY sha")
                assert all(row["sha"] == row["actual"] for row in data)
            else:
                data = sorted((dict(row) for row in rows(conn, f"SELECT * FROM `{name}`")),
                              key=lambda row: json.dumps(row, sort_keys=True, default=wire))
            encoded = json.dumps([dict(row) for row in data], sort_keys=True, default=wire).encode()
            records[name] = {"rows": len(data), "sha256": hashlib.sha256(encoded).hexdigest()}
        states = rows(conn, "SELECT state,COUNT(*) AS count FROM task_stage GROUP BY state")
        confirmations = one(conn, "SELECT COUNT(*) AS count FROM m2_confirmation WHERE answer IS NULL")["count"]
        versions = rows(conn, "SELECT id,task_id,project_id,part_number,version,state,LOWER(HEX(step_sha)) AS step_sha FROM m2_version ORDER BY id")
        return {"tables": records, "stage_states": [dict(row) for row in states], "unanswered_confirmations": confirmations,
                "versions": [dict(row) for row in versions]}


def seed(folder):
    folder.mkdir(parents=True, exist_ok=True)
    created = []
    with engine.begin() as conn:
        source = one(conn, """SELECT s.* FROM m2_version v JOIN product_task t ON t.id=v.task_id
            JOIN source_file s ON s.id=t.source_id WHERE v.id=4""")
        original = one(conn, "SELECT * FROM m2_version WHERE id=4 AND state='published'")
        assert original and source
        geometry = one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=original["geometry_sha"])["body"]
        checklist = one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=original["checklist_sha"])["body"]
        step = one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=original["step_sha"])["body"]
        for label in ("pending", "large"):
            project = conn.execute(text("INSERT INTO project (name) VALUES (:name)"), {"name": f"M4 {label} recovery fixture"}).lastrowid
            conn.execute(text("INSERT INTO project_member VALUES (:project,1,'admin')"), {"project": project})
            batch = conn.execute(text("INSERT INTO upload_batch (project_id,user_id,request_key) VALUES (:project,1,:key)"),
                                 {"project": project, "key": uuid.uuid4().hex}).lastrowid
            copied = conn.execute(text("""INSERT INTO source_file (batch_id,client_file_key,filename,sha,selection_json,state)
                VALUES (:batch,'m4','M4-reused-accepted-M12.pdf',:sha,:selection,'accepted')"""),
                                  {"batch": batch, "sha": source["sha"], "selection": source["selection_json"]}).lastrowid
            task = conn.execute(text("""INSERT INTO product_task (source_id,scope_key,state,budget_cny)
                VALUES (:source,'m4-recovery','partial',10)"""), {"source": copied}).lastrowid
            conn.execute(text("""INSERT INTO m2_product (task_id,part_number,specification)
                SELECT :task,part_number,specification FROM m2_product WHERE task_id=:origin"""),
                         {"task": task, "origin": original["task_id"]})
            version = conn.execute(text("""INSERT INTO m2_version
                (task_id,project_id,part_number,version,geometry_revision,basis_sha,checklist_sha,
                 geometry_sha,snapshot_sha,html_sha,html_check,state)
                SELECT :task,:project,part_number,version,geometry_revision,basis_sha,checklist_sha,
                       geometry_sha,snapshot_sha,html_sha,html_check,'partial' FROM m2_version WHERE id=4"""),
                                   {"task": task, "project": project}).lastrowid
            stage = enqueue(conn, task, "m2_cad", original["basis_sha"])
            conn.execute(text("UPDATE task_stage SET ready_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 DAY) WHERE id=:id"), {"id": stage["id"]})
            directory = folder / label
            directory.mkdir()
            (directory / "geometry.json").write_bytes(geometry)
            (directory / "checklist.json").write_bytes(checklist)
            body = step
            if label == "large":
                target = 64 * 1024 * 1024 - 4096
                filler = b"\n/* M4 capacity fixture: accepted geometry unchanged; padding is STEP whitespace. */\n"
                body = step.replace(b"END-ISO-10303-21;", b" " * (target - len(step) - len(filler)) + filler + b"END-ISO-10303-21;")
                assert len(body) == target
            (directory / "model.step").write_bytes(body)
            created.append({"label": label, "project": project, "task": task, "version": version, "stage_id": stage["id"],
                            "input_sha": original["basis_sha"].hex(), "step_bytes": len(body),
                            "step_sha256": hashlib.sha256(body).hexdigest()})
        outsider_password = secrets.token_urlsafe(24)
        outsider = conn.execute(text("INSERT INTO app_user (login,password_hash) VALUES ('m4-outsider',:hash)"),
                               {"hash": password_hash.hash(outsider_password)}).lastrowid
        outside_project = conn.execute(text("INSERT INTO project (name) VALUES ('M4 outsider')")).lastrowid
        conn.execute(text("INSERT INTO project_member VALUES (:project,:user,'reader')"), {"project": outside_project, "user": outsider})
        for role in ("reader", "operator"):
            user = conn.execute(text("INSERT INTO app_user (login,password_hash) VALUES (:login,:hash)"),
                                {"login": f"m4-{role}", "hash": password_hash.hash(secrets.token_urlsafe(24))}).lastrowid
            conn.execute(text("INSERT INTO project_member VALUES (1,:user,:role)"), {"user": user, "role": role})
    batch = create_batch(1, 1, "m4-queued-pdf")["id"]
    with engine.connect() as conn:
        pdf = one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=source["sha"])["body"]
    queued = accept_file(batch, 1, "pending", "M4-queued.pdf", pdf, "m1")
    (folder / "outsider.env").write_text(f"BOOTSTRAP_LOGIN=m4-outsider\nBOOTSTRAP_PASSWORD={outsider_password}\n", encoding="utf-8")
    result = {"items": created, "queued": queued, "outsider_project": outside_project,
              "fixture_provenance": "M3 #4 accepted bytes reused in new QA projects; original publications unchanged"}
    (folder / "fixtures.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def stage(folder, state):
    fixtures = json.loads((folder / "fixtures.json").read_bytes())
    for item in fixtures["items"]:
        directory = folder / item["label"]
        check = json.loads((directory / "step-check.json").read_bytes())
        body = (directory / "model.step").read_bytes()
        assert check["passed"] and check["step_sha256"] == hashlib.sha256(body).hexdigest()
        with engine.begin() as conn:
            conn.execute(text("UPDATE task_stage SET ready_at=UTC_TIMESTAMP(6) WHERE id=:id"), {"id": item["stage_id"]})
        claimed = execute({"action": "claim", "owner": f"m4-{item['label']}-staging"})
        assert claimed["id"] == item["stage_id"], claimed
        payload = {"stage": claimed, "arguments": {"body": base64.b64encode(body).decode(), "check": check}}
        pending = state / "pending" / f"{claimed['id']}-{claimed['input_sha']}" / str(claimed["fence"])
        pending.mkdir(parents=True)
        with (pending / "step.json").open("w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if item["label"] == "large":
            receipt = execute({"action": "step", **payload["arguments"], "stage": claimed})
            assert receipt["state"] == "published"
        else:
            execute({"action": "fail", "stage": claimed, "reason": "M4 QA accepted STEP persisted before database publication"})
            with engine.begin() as conn:
                conn.execute(text("UPDATE task_stage SET state='ready',ready_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 1 DAY) WHERE id=:id"), {"id": claimed["id"]})
    with engine.begin() as conn:
        conn.execute(text("UPDATE task_stage SET ready_at=UTC_TIMESTAMP(6) WHERE id=:id"), {"id": fixtures["items"][0]["stage_id"]})
    return fixtures


def prepare(folder):
    fixtures = json.loads((folder / "fixtures.json").read_bytes())
    with engine.connect() as conn:
        original = one(conn, "SELECT * FROM m2_version WHERE id=4")
        snapshot = json.loads(one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=original["snapshot_sha"])["body"])
        original_step = one(conn, "SELECT body FROM file_bytes WHERE sha=:sha", sha=original["step_sha"])["body"]
    for item in fixtures["items"]:
        directory = folder / item["label"]
        (directory / "panel.brep").write_bytes(base64.b64decode(snapshot["panel.brep"]))
        if item["label"] == "pending":
            body = original_step.replace(b"END-ISO-10303-21;", b"/* M4 independently accepted pending transfer */\nEND-ISO-10303-21;")
            (directory / "model.step").write_bytes(body)
            item.update(step_bytes=len(body), step_sha256=hashlib.sha256(body).hexdigest())
            with engine.connect() as conn:
                assert not one(conn, "SELECT sha FROM file_bytes WHERE sha=:sha", sha=hashlib.sha256(body).digest())
    (folder / "fixtures.json").write_text(json.dumps(fixtures, indent=2), encoding="utf-8")
    return fixtures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("audit", "versions", "seed", "prepare", "stage", "replay-marker", "resume-audit"))
    parser.add_argument("--folder", type=Path, default=Path("/qa-state"))
    parser.add_argument("--state", type=Path, default=Path("/state"))
    args = parser.parse_args()
    if args.action == "audit":
        result = audit()
    elif args.action == "versions":
        with engine.connect() as conn:
            result = [dict(row) for row in rows(conn, "SELECT * FROM m2_version ORDER BY id")]
    elif args.action == "seed":
        result = seed(args.folder)
    elif args.action == "stage":
        result = stage(args.folder, args.state)
    elif args.action == "prepare":
        result = prepare(args.folder)
    elif args.action == "replay-marker":
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO project (name) VALUES ('M4 binlog replay proof')"))
        result = {"marker_written_after_snapshot": True}
    else:
        fixtures = json.loads((args.folder / "fixtures.json").read_bytes())
        item = fixtures["items"][0]
        with engine.connect() as conn:
            receipt = one(conn, "SELECT * FROM m3_publication WHERE version_id=:id", id=item["version"])
            events = rows(conn, "SELECT kind,COUNT(*) AS count FROM stage_event WHERE stage_id=:id GROUP BY kind", id=item["stage_id"])
            calls = one(conn, "SELECT COUNT(*) AS count FROM m1_call")["count"]
            result = {"receipt": dict(receipt) if receipt else None, "events": [dict(row) for row in events], "model_calls": calls}
    print(json.dumps(result, ensure_ascii=True, indent=2, default=wire))


if __name__ == "__main__":
    main()
