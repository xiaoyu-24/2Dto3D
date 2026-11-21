import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import NameObject, NumberObject, RectangleObject
from sqlalchemy import text

from app import m1_read, m2_cad, m2_work
from app.auth import password_hash
from app.db import engine, one
from app.ingest import accept_file, create_batch, put_bytes
from app.m1_sample import PART, encode_json
from app.m2_evidence import pdf_rect, render, transform
from app.m2_sample import B040, FIVE_PIN, WLT, resolve_variant, sample_evidence
from app.main import app
from app.pdf import extract_pdf
from app.queue import claim
from app.worker import process

PART_TABLES = [
    (B040, "B040108M0-2XX2XX01", "B040108M0-2102AA01", "B040108M0-2112AA01"),
    (WLT, "WLT2F9-141XX0200", "WLT2F9-1415A0200", "WLT2F9-1415S0200"),
]
PACKAGING = "包装（同料号，卷盘/载带）"


def defer_jobs():
    with engine.begin() as conn:
        conn.execute(text("UPDATE task_stage SET ready_at=DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 1 DAY) WHERE state='ready'"))


def shared_read(client, account, m12, monkeypatch):
    defer_jobs()
    batch = create_batch(account["project"], account["id"], uuid.uuid4().hex)["id"]
    endpoint = f"/api/batches/{batch}/files"
    def upload():
        return client.post(endpoint, data={"client_file_key": "pdf", "workflow": "m2"}, files={"file": ("M12.pdf", m12)})

    first = upload().json()
    second = upload().json()
    assert first["task_id"] == second["task_id"] and second["reused"]
    regions = {"model": {"page": 1, "label": "料号表", "rect": [0.02, 0.49, 0.37, 0.83]}}
    output = {"summary": "一页包含四针和五针", "regions": regions, "products": [
        {"part_number": part, "drawing_no": "ENDE05", "revision": "A0", "family": "M12",
         "pin_count": pins, "total_length_mm": length, "regions": ["model"], "findings": []}
        for part, pins, length in ((PART, 4, "24.70"), (FIVE_PIN, 5, "26.70"))]}
    calls = []

    def harness(path, route=None, payload=None):
        if path == "/route":
            return {"route": {"route_id": "24" * 32}}
        calls.append(path)
        common = {"http_status": 200, "request_id": path, "finish_reason": "completed", "usage": {"input_tokens": 10, "output_tokens": 10}}
        if path == "/read":
            assert len(payload["images"]) == 1
            return {**common, "response": json.dumps(output)}
        return {**common, "passed": True}

    monkeypatch.setenv("M1_VISION_PROJECT_ID", str(account["project"]))
    monkeypatch.setattr(m2_work, "harness", harness)
    monkeypatch.setattr(m1_read, "harness", harness)
    assert process(claim("extract")) and process(claim("reading"))
    assert calls == ["/probe", "/read"]
    return batch, first["task_id"], calls


def test_one_shared_read_concurrent_derivation_and_single_confirmation(client, account, m12, monkeypatch):
    batch, file, calls = shared_read(client, account, m12, monkeypatch)
    endpoint = f"/api/tasks/{file}/products"
    assert client.post(endpoint, json={"part_numbers": ["unknown"]}).status_code == 422
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: client.post(endpoint, json={"part_numbers": [PART, FIVE_PIN]}), range(4)))
    assert all(result.status_code == 200 for result in results)
    receipt_sets = [result.json()["products"] for result in results]
    assert sum(not item["reused"] for receipt in receipt_sets for item in receipt) == 2
    part_ids = {item["part_number"]: item["task_id"] for item in receipt_sets[0]}
    summary = client.get(f"/api/batches/{batch}").json()
    assert len(summary["files"]) == 1 and len(summary["files"][0]["products"]) == 2
    four, five = part_ids[PART], part_ids[FIVE_PIN]
    assert process(claim("prepare-four"))
    four_detail = client.get(f"/api/tasks/{four}/m2").json()
    five_detail = client.get(f"/api/tasks/{five}/m2").json()
    assert four_detail["state"] == "cad_queued" and five_detail["state"] == "needs_confirmation"
    assert four_detail["shared_read_sha256"] == five_detail["shared_read_sha256"]
    assert four_detail["specification"]["total_length_mm"] == "24.70"
    assert five_detail["specification"]["total_length_mm"] == "26.70"
    assert four_detail["specification"]["regions"] != five_detail["specification"]["regions"]
    assert len(five_detail["confirmations"]) == 1
    confirmation = five_detail["confirmations"][0]
    endpoint = f"/api/confirmations/{confirmation['id']}/answer"
    assert client.post(endpoint, json={"answer": "交换端面"}).status_code == 422
    answer = {"answer": "采用 A-code 5pin 栏"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        submitted = list(pool.map(lambda _: client.post(endpoint, json=answer), range(4)))
    assert all(result.status_code == 200 for result in submitted)
    assert sum(result.json()["resumed"] for result in submitted) == 1
    assert client.post(endpoint, json={"answer": "重新确认"}).status_code == 409
    assert process(claim("prepare-five")) and claim("no-extra") is None
    saved = client.get(f"/api/tasks/{five}/m2").json()
    assert saved["state"] == "cad_queued" and saved["confirmations"][0]["operator"] == account["login"]
    assert saved["confirmations"][0]["answer"] == {"value": answer["answer"]}
    assert saved["confirmations"][0]["answered_at"].endswith("Z")
    assert saved["confirmations"][0]["evidence"] == confirmation["evidence"]
    events = client.get(f"/api/tasks/{five}/events").json()
    assert [event["kind"] for event in events].count("confirmation_resumed") == 1
    assert calls == ["/probe", "/read"]


@pytest.mark.parametrize(("rotation", "expected"), [(0, [18, 67, 43, 82]), (90, [18, 22, 33, 47]),
                                                    (180, [77, 22, 102, 37]), (270, [87, 57, 102, 82])])
def test_pdf_rotation_with_nonzero_crop(rotation, expected):
    frame = {"pdf_to_pixels": transform({"crop_box": [8, 12, 112, 92], "user_unit": 1, "rotation": rotation}, 72)}
    assert pdf_rect(frame, [10, 10, 25, 15]) == expected


def test_user_unit_retained_and_matches_render_scale_for_rotated_crop():
    document = PdfWriter()
    page = document.add_blank_page(126, 118)
    page.cropbox = RectangleObject([8, 12, 112, 92])
    page[NameObject("/UserUnit")] = NumberObject(2)
    page.rotate(270)
    buffer = BytesIO()
    document.write(buffer)
    source = buffer.getvalue()
    evidence, _ = render(source, extract_pdf(source))
    frame = evidence["pages"][0]
    assert (frame["user_unit"], frame["width"], frame["height"]) == (2, 200, 260)
    assert pdf_rect(frame, [0, 0, frame["width"], frame["height"]]) == frame["crop_box"]


@pytest.mark.parametrize(("source", "valid", "invalid", "pins"), [
    (B040, "B040108M0-2102AA01", "B040108M0-2112AA01", 10),
    (WLT, "WLT2F9-1415A0200", "WLT2F9-1415S0200", 14),
])
def test_explicit_part_selection_obeys_original_code_table(source, valid, invalid, pins):
    reviewed, regions = sample_evidence(source, {"products": [], "regions": {}})
    products = {item["part_number"]: item for item in reviewed}
    selected = resolve_variant(source, valid, products)
    assert selected["part_number"] == valid and selected["pin_count"] == pins
    assert selected["questions"][0]["expected"] == f"确认 {valid}"
    assert {regions[key]["page"] for key in selected["questions"][0]["regions"]} == {1, 2}
    assert resolve_variant(source, invalid, products) is None


def contaminated_reading(template, invalid):
    regions = {"model": {"page": 1, "label": "料号表", "rect": [0.1, 0.2, 0.5, 0.4]},
               "package": {"page": 2, "label": "卷盘包装", "rect": [0.1, 0.2, 0.5, 0.4]}}
    parts = [template, PACKAGING, f"{template}（60针示例配置）", invalid, PART]
    return {"summary": "第一页为产品，第二页为同料号包装", "regions": regions, "products": [
        {"part_number": part, "drawing_no": "ENDE05", "revision": "A0", "family": "connector",
         "pin_count": None, "total_length_mm": None, "regions": ["model", "package"], "findings": [],
         "questions": [{"key": "configuration", "question": "请核对原图配置", "regions": ["model", "package"]}]}
        for part in parts]}


@pytest.mark.parametrize(("source", "template", "valid", "invalid"), PART_TABLES)
def test_new_candidates_exclude_packaging_and_non_part_labels(source, template, valid, invalid):
    reading = contaminated_reading(template, invalid)
    reading["products"].append({**reading["products"][0], "part_number": valid})
    original = encode_json(reading)
    products, regions = sample_evidence(source, reading)
    assert {item["part_number"] for item in products} == {template, valid}
    assert regions["package"]["page"] == 2 and regions["verified-package"]["page"] == 2
    assert encode_json(reading) == original


def save_legacy_candidates(account, source, template, invalid):
    batch = create_batch(account["project"], account["id"], uuid.uuid4().hex)["id"]
    receipt = accept_file(batch, account["id"], "legacy", f"{template}.pdf", b"%PDF-legacy-reading", "m2")
    reading = contaminated_reading(template, invalid)
    with engine.begin() as conn:
        image = put_bytes(conn, b"saved-page-crop").hex()
        evidence = {"source_sha256": source, "reading": reading, "products": reading["products"],
                    "images": {key: image for key in reading["regions"]}, "regions": reading["regions"]}
        output = put_bytes(conn, encode_json(evidence))
        stage = m2_work.enqueue(conn, receipt["task_id"], "m2_read", output)
        conn.execute(text("UPDATE task_stage SET state='done',output_sha=:sha WHERE id=:id"),
                     {"sha": output, "id": stage["id"]})
        conn.execute(text("UPDATE product_task SET state='selection_ready' WHERE id=:id"), {"id": receipt["task_id"]})
    return receipt["task_id"], output, evidence


@pytest.mark.parametrize(("source", "template", "valid", "invalid"), PART_TABLES)
def test_legacy_candidates_filtered_without_rewriting_shared_evidence(client, account, source, template, valid, invalid):
    task_id, output, saved = save_legacy_candidates(account, source, template, invalid)
    detail = client.get(f"/api/tasks/{task_id}/m2").json()
    assert [item["part_number"] for item in detail["evidence"]["products"]] == [template]
    assert detail["shared_read_sha256"] == output.hex()
    assert detail["evidence"]["reading"] == saved["reading"]
    assert detail["evidence"]["regions"] == saved["regions"]
    assert detail["evidence"]["images"] == saved["images"]
    with engine.connect() as conn:
        assert json.loads(m2_work.blob(conn, output)) == saved


@pytest.mark.parametrize(("source", "template", "valid", "invalid"), PART_TABLES)
def test_derive_rejects_saved_non_parts_atomically_and_reuses_valid_parts(client, account, source, template, valid, invalid):
    task_id, output, saved = save_legacy_candidates(account, source, template, invalid)
    endpoint = f"/api/tasks/{task_id}/products"

    def counts():
        with engine.connect() as conn:
            return {table: one(conn, f"SELECT COUNT(*) AS count FROM {table}")["count"]
                    for table in ("product_task", "m2_product", "task_stage", "stage_event", "m2_confirmation", "m2_version", "m1_call", "file_bytes")}

    before = counts()
    for part in (PACKAGING, f"{template}（60针示例配置）", invalid, PART):
        for selected in ([part], [valid, part]):
            response = client.post(endpoint, json={"part_numbers": selected})
            assert response.status_code == 422
            assert counts() == before
    accepted = client.post(endpoint, json={"part_numbers": [template, valid]})
    assert accepted.status_code == 200
    products = accepted.json()["products"]
    assert [item["part_number"] for item in products] == [template, valid]
    assert all(not item["reused"] for item in products)
    created = counts()
    assert created["product_task"] == before["product_task"] + 2
    assert created["m1_call"] == before["m1_call"]
    assert client.post(endpoint, json={"part_numbers": [template, valid]}).json() == {
        "products": [{**item, "reused": True} for item in products]}
    assert counts() == created
    with engine.connect() as conn:
        assert json.loads(m2_work.blob(conn, output)) == saved


def seed_version(project, user, label, *, partial=False):
    batch = create_batch(project, user, uuid.uuid4().hex)["id"]
    receipt = accept_file(batch, user, "file", f"{label}.pdf", f"%PDF-{label}".encode())
    with engine.begin() as conn:
        payloads = {kind: f"{label}:{kind}".encode() for kind in ("geo", "basis", "checks", "html", "step", "report")}
        sha = {key: put_bytes(conn, body) for key, body in payloads.items()}
        html = encode_json({"passed": True, "geometry_sha256": sha["geo"].hex(), "html_sha256": sha["html"].hex()}).decode()
        step = encode_json({"passed": True, "geometry_sha256": sha["geo"].hex(), "step_sha256": sha["step"].hex(),
                            "report_sha256": sha["report"].hex()}).decode()
        state = "partial" if partial else "published"
        version = conn.execute(text("""INSERT INTO m2_version (task_id,project_id,part_number,version,basis_sha,checklist_sha,
            geometry_sha,html_sha,step_sha,report_sha,html_check,step_check,state) VALUES
            (:task,:project,:part,:version,:basis,:checks,:geo,:html,:step,:report,:html_check,:step_check,:state)"""),
                               {**sha, "task": receipt["task_id"], "project": project, "part": label, "version": "A0-M2",
                                "html_check": html, "step_check": step if not partial else None, "state": state}).lastrowid
    return receipt, version, payloads


def test_history_immutable_checks_zip_and_cross_project(client, account):
    own, version, own_files = seed_version(account["project"], account["id"], "own", partial=True)
    _, complete, complete_files = seed_version(account["project"], account["id"], "complete")
    with engine.begin() as conn:
        outsider = conn.execute(text("INSERT INTO app_user (login,password_hash) VALUES (:login,:hash)"),
                                {"login": uuid.uuid4().hex, "hash": password_hash.hash("qa-only-passphrase")}).lastrowid
        project = conn.execute(text("INSERT INTO project(name) VALUES ('other')")).lastrowid
        conn.execute(text("INSERT INTO project_member VALUES (:project,:user,'operator')"), {"project": project, "user": outsider})
    _, foreign, _ = seed_version(project, outsider, "outside")
    path = f"/api/projects/{account['project']}"
    search = client.get(f"{path}/versions", params={"batch_id": own["batch_id"], "part_number": "own", "version": "A0-M2"}).json()
    assert [item["id"] for item in search] == [version]
    assert search[0]["state"] == "partial" and set(search[0]["files"]) == {"html"}
    assert client.get(f"/api/artifacts/{version}-html/download").content == own_files["html"]
    assert client.get(f"/api/artifacts/{version}-step/download").status_code == 404
    assert client.get(f"/api/artifacts/{foreign}-html/download").status_code == 404
    assert client.post(f"{path}/zip", json={"version_ids": [version, foreign]}).status_code == 404
    assert client.post(f"/api/projects/{project}/zip", json={"version_ids": [foreign]}).status_code == 404
    with TestClient(app, headers={"X-Requested-With": "drawing-web"}) as anonymous:
        assert anonymous.get(f"/api/artifacts/{version}-html/download").status_code == 401
        assert anonymous.post(f"{path}/zip", json={"version_ids": [version]}).status_code == 401
    zipped = client.post(f"{path}/zip", json={"version_ids": [version, complete, complete]})
    assert zipped.status_code == 200
    with ZipFile(BytesIO(zipped.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))["versions"]
        for item, expected in zip(manifest, (own_files, complete_files), strict=True):
            for kind, proof in item["files"].items():
                actual = archive.read(proof["path"])
                assert actual == expected[kind]
                assert len(actual) == proof["byte_count"] and hashlib.sha256(actual).hexdigest() == proof["sha256"]
        assert len(archive.namelist()) == 5
    with engine.begin() as conn:
        conn.execute(text("UPDATE m2_version SET report_sha=html_sha WHERE id=:id"), {"id": complete})
    assert client.get(f"/api/versions/{complete}").json()["state"] == "rejected"
    assert client.get(f"/api/artifacts/{complete}-html/download").status_code == 404
    assert client.post(f"{path}/zip", json={"version_ids": [complete]}).status_code == 409


def test_retry_isolated_and_stale_cad_worker_cannot_write(client, account, m12):
    defer_jobs()
    batch = create_batch(account["project"], account["id"], uuid.uuid4().hex)["id"]
    failed = accept_file(batch, account["id"], "bad", "failed.pdf", b"%PDF-broken", "m2")
    done = accept_file(batch, account["id"], "good", "good.pdf", m12)
    assert process(claim("fail")) and process(claim("success"))
    good = client.get(f"/api/tasks/{done['task_id']}/events").json()
    endpoint = f"/api/batches/{batch}/retry-failed"
    assert client.post(endpoint).json()["retried"] == [failed["task_id"]]
    assert client.post(endpoint).json()["retried"] == []
    assert client.get(f"/api/tasks/{done['task_id']}/events").json() == good
    assert process(claim("repeat-fail"))
    assert client.post(f"/api/tasks/{failed['task_id']}/retry").json()["retried"] == [failed["task_id"]]

    receipt, version, _ = seed_version(account["project"], account["id"], "stale", partial=True)
    with engine.begin() as conn:
        basis = one(conn, "SELECT basis_sha FROM m2_version WHERE id=:id", id=version)["basis_sha"]
        stage = m2_work.enqueue(conn, receipt["task_id"], "m2_cad", basis)
    first = claim("first-cad", cad=True)
    assert first["id"] == stage["id"]
    with engine.begin() as conn:
        conn.execute(text("UPDATE task_stage SET lease_until=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 SECOND) WHERE id=:id"), {"id": stage["id"]})
    second = claim("second-cad", cad=True)
    assert second["fence"] == first["fence"] + 1
    with pytest.raises(ValueError, match="租约"):
        m2_cad.fail(first, "stale")
    assert m2_cad.fail(second, "isolated export failure")
    assert client.get(f"/api/versions/{version}").json()["state"] == "partial"
