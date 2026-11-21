import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import m1_read
from app.db import engine, one
from app.ingest import put_bytes
from app.m1_sample import PART, SOURCE
from app.main import app
from app.queue import claim
from app.worker import process
from test_worker import isolated_job


def reading(total="24.70", end_labels=("配合端", "焊线端")):
    findings = [{"label": "轴向总长", "value": total, "kind": "drawing", "regions": ["section"]}]
    findings += [{"label": f"{label}视向", "value": f"从{label}沿轴观察。", "kind": "derived",
                  "regions": [region, "section"]} for label, region in zip(end_labels, ("mating", "terminal"))]
    return json.dumps({"part_number": PART, "drawing_no": "ENDE05", "revision": "A0", "pin_count": 4,
                       "total_length_mm": total, "findings": findings})


@pytest.mark.parametrize("length", ["26.70", "26.90"])
def test_rejects_other_pin_lengths_in_four_pin_response(length, client, account, m12, monkeypatch):
    root = f"/api/tasks/{isolated_job(account, m12)['task_id']}/m1"
    assert process(claim("m1-pdf"))

    def harness(path, *_):
        if path == "/route":
            return {"route": {"route_id": "42" * 32}}
        common = {"http_status": 200, "request_id": "test", "usage": {"input_tokens": 17, "output_tokens": 9}}
        return {**common, "passed": True} if path == "/probe" else {
            **common, "finish_reason": "completed", "response": reading(length),
        }

    monkeypatch.setenv("M1_VISION_PROJECT_ID", str(account["project"]))
    monkeypatch.setattr(m1_read, "harness", harness)
    assert client.post(root + "/start").status_code == 200
    assert process(claim("m1-mismatch"))
    rejected = client.get(root).json()
    assert rejected["state"] == "read_pending" and "型号错配" in rejected["error"]
    assert "reading" not in rejected["evidence"]
    assert client.get(root + "/frozen/basis").status_code == 404


@pytest.mark.parametrize("case", ["correct", "swapped", "contradictory", "missing"])
def test_end_views_control_frozen_downloads(case, client, account, m12, monkeypatch):
    root = f"/api/tasks/{isolated_job(account, m12)['task_id']}/m1"
    assert process(claim("m1-pdf"))
    output = json.loads(reading(end_labels=("焊线端", "配合端") if case == "swapped" else ("配合端", "焊线端")))
    if case == "correct":
        output["findings"][1]["regions"][0] = "end_a"
        output["findings"][2]["regions"][0] = "end_b"
    if case == "contradictory":
        output["findings"][1]["value"] = "从焊线端沿轴观察。"
    if case == "missing":
        output["findings"].pop()

    def harness(path, *_):
        if path == "/route":
            return {"route": {"route_id": "42" * 32}}
        common = {"http_status": 200, "request_id": path[1:] + "-test", "response_id": "test",
                  "actual_model": "test", "elapsed_ms": 100, "usage": {"input_tokens": 17, "output_tokens": 9}}
        return {**common, "passed": True} if path == "/probe" else {
            **common, "finish_reason": "completed", "response": json.dumps(output),
        }

    monkeypatch.setenv("M1_VISION_PROJECT_ID", str(account["project"]))
    monkeypatch.setattr(m1_read, "harness", harness)
    assert client.post(root + "/start").status_code == 200
    assert process(claim("m1-end-views"))
    result = client.get(root).json()
    correct = case == "correct"
    assert result["state"] == ("basis_frozen" if correct else "read_pending")
    basis = client.get(root + "/frozen/basis")
    checklist = client.get(root + "/frozen/checklist")
    assert basis.status_code == checklist.status_code == (200 if correct else 404)
    if correct:
        views = basis.json()["end_views"]
        assert (views["mating"]["region"], views["mating"]["look_along"]) == ("mating", "+Z")
        assert (views["terminal"]["region"], views["terminal"]["look_along"]) == ("terminal", "-Z")
        assert result["evidence"]["reading"]["end_views"] == views
    else:
        assert "端面视向" in result["error"] and result["frozen"] is None
    if case == "swapped":
        # Simulate an affected M1.2 freeze made before the validator existed.
        with engine.begin() as conn:
            legacy_output = put_bytes(conn, json.dumps({**result["evidence"], "reading": output,
                                                       "model_run": {"request_id": "read-test"}}).encode())
            legacy_basis = put_bytes(conn, json.dumps({"version": f"{PART}/A0/M1.2"}).encode())
            conn.execute(text("UPDATE task_stage SET state='done',output_sha=:sha WHERE id=:id"),
                         {"sha": legacy_output, "id": result["stage_id"]})
            conn.execute(text("""UPDATE product_task t JOIN task_stage q ON q.task_id=t.id
                SET t.state='basis_frozen' WHERE q.id=:id"""), {"id": result["stage_id"]})
            conn.execute(text("""INSERT INTO m1_basis (stage_id,answers,basis_sha,checklist_sha)
                VALUES (:id,'{}',:sha,:sha)"""), {"id": result["stage_id"], "sha": legacy_basis})
        assert client.get(root).json()["state"] == "basis_recheck"
        assert client.get(root + "/frozen/basis").status_code == 404
        assert client.get(root + "/frozen/checklist").status_code == 404
        assert client.post(root + "/start").json()["reused"] is True
        failed_recheck = client.get(root).json()
        assert failed_recheck["state"] == "read_pending" and "端面视向" in failed_recheck["error"]
        assert client.get(root + "/frozen/basis").status_code == 404
        assert client.get(root + "/frozen/checklist").status_code == 404


def test_read_back_retry_permissions_and_frozen_files(client, account, m12, monkeypatch):
    root = f"/api/tasks/{isolated_job(account, m12)['task_id']}/m1"
    source_stage = claim("m1-pdf")
    assert process(source_stage)
    assert client.get(root + "/frozen/basis").status_code == 404

    monkeypatch.setattr(m1_read, "harness", lambda *_: {})
    first = client.post(root + "/start").json()
    assert client.post(root + "/start").json() == {"stage_id": first["stage_id"], "reused": True}
    assert process(claim("m1-unconfigured"))
    pending = client.get(root).json()
    assert pending["state"] == "read_pending" and pending["calls"] == []
    evidence = pending["evidence"]
    assert evidence["source_sha256"] == SOURCE and len(evidence["images"]) == 10
    region = evidence["regions"]["section"]
    assert region["page"] == 1 and region["source_sha256"] == SOURCE
    assert len(region["pdf_rect"]) == len(region["pixel_rect"]) == 4
    image = client.get(root + "/images/section")
    assert hashlib.sha256(image.content).hexdigest() == evidence["images"]["section"] == image.headers["x-content-sha256"]

    def fail_render(*_):
        pytest.fail("重试应从 MySQL 复用原图页区")

    calls = []

    def harness(path, route=None, payload=None):
        if path == "/route":
            return {"route": {"route_id": "24" * 32}}
        calls.append(path)
        common = {"http_status": 200, "request_id": path[1:] + "-test", "response_id": "test-response",
                  "actual_model": "test-vision", "elapsed_ms": 100, "usage": {"input_tokens": 17, "output_tokens": 9}}
        return {**common, "passed": True} if path == "/probe" else {
            **common, "finish_reason": "completed", "response": reading(),
        }

    with engine.begin() as conn:
        conn.execute(text("UPDATE project_member SET role='reader' WHERE project_id=:p AND user_id=:u"),
                     {"p": account["project"], "u": account["id"]})
    assert client.get(root + "/images/section").status_code == 200
    assert client.post(root + "/start").status_code == 403
    with TestClient(app) as stranger:
        assert stranger.get(root + "/images/section").status_code == 401
    with engine.begin() as conn:
        conn.execute(text("UPDATE project_member SET role='operator' WHERE project_id=:p AND user_id=:u"),
                     {"p": account["project"], "u": account["id"]})

    monkeypatch.setenv("M1_VISION_PROJECT_ID", str(account["project"]))
    monkeypatch.setattr(m1_read, "harness", harness)
    monkeypatch.setattr(m1_read, "render_evidence", fail_render)
    assert client.post(root + "/start").json() == {"stage_id": first["stage_id"], "reused": True}
    assert process(claim("m1-resumed"))
    completed = client.get(root).json()
    assert completed["state"] == "basis_frozen" and calls == ["/probe", "/read"]
    assert completed["evidence"]["images"] == evidence["images"]
    assert completed["evidence"]["model_run"]["request_id"] == "read-test"
    assert client.post(root + "/start").json() == {"stage_id": first["stage_id"], "reused": True}
    assert claim("m1-repeat") is None and calls == ["/probe", "/read"]

    frozen = completed["frozen"]
    assert client.get(root).json()["frozen"] == frozen
    for kind in ("basis", "checklist"):
        response = client.get(root + f"/frozen/{kind}")
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == frozen[f"{kind}_sha256"]
        assert response.json()["version"] == frozen["version"]
    basis = client.get(root + "/frozen/basis").json()
    checks = client.get(root + "/frozen/checklist").json()
    assert basis["part_number"] == PART and "reading" not in basis and "confirmations" not in basis
    assert set(basis["trials"]) == {"key", "pins", "oring", "assembly"}
    assert all(trial["kind"] == "trial" and trial["regions"] for trial in basis["trials"].values())
    assert basis["scope"]["nut"] == {"D": 1, "installed_pose": None}
    assert checks["basis_sha256"] == frozen["basis_sha256"]
    assert len([check for check in checks["checks"] if check["kind"] == "trial"]) == 4
    total = next(check for check in checks["checks"] if check["name"] == "本体轴向总长")
    assert total["expected"] == {"nominal_mm": "24.70", "general_tolerance_mm": "±0.25",
                                 "reject_mm": ["26.70", "26.90"]}
    assert total["evidence"][0]["image_sha256"] == evidence["images"]["section"]
    with engine.connect() as conn:
        assert one(conn, "SELECT COUNT(*) AS n FROM m1_call WHERE stage_id=:id", id=first["stage_id"])["n"] == 2
        assert one(conn, """SELECT COUNT(*) AS n FROM stage_event WHERE stage_id=:id AND kind='basis_frozen'""",
                   id=first["stage_id"])["n"] == 1
