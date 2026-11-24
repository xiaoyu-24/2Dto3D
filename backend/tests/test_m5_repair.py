import json

import pytest
from sqlalchemy import text

from app import m2_work, m3_models
from app.db import engine, one
from app.queue import claim
from app.worker import process
from test_m2 import shared_read
from test_m3 import model_stage, run_model, simulator


def test_page_identity_handles_rotation_but_rejects_changed_ink():
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    from app.m2_reuse import identity, turn
    from app.pdf import extract_pdf

    def document(rotation, mark):
        writer = PdfWriter()
        page = writer.add_blank_page(width=120, height=90)
        content = DecodedStreamObject()
        content.set_data(f"{mark} 0 8 8 re f".encode())
        page[NameObject("/Contents")] = writer._add_object(content)
        page.rotation = rotation
        body = BytesIO()
        writer.write(body)
        return body.getvalue()

    originals = [document(0, 10), document(90, 10), document(90, 12)]
    plain, rotated, changed = [identity(body, extract_pdf(body)) for body in originals]
    assert plain == rotated and rotated != changed
    rect = [0.125, 0.25, 0.75, 0.875]
    assert turn(rect, 90) == [0.125, 0.125, 0.75, 0.75] and turn(turn(rect, 90), -90) == rect


def test_printed_label_requires_independent_occurrence_product_page_and_confirmation(monkeypatch):
    from types import SimpleNamespace
    from app import m2_labels
    word = '<word xMin="30" yMin="40" xMax="45" yMax="100">'
    xml = f'<doc><page>{word}型号：QA-4X</word>{word}QA-4X</word>{word}型号：UNKNOWN</word></page></doc>'.encode()
    monkeypatch.setattr(m2_labels.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=xml))
    saved = {}
    def render(source, checkpoint, regions, frames):
        saved.update(regions)
        return {"regions": regions}, {key: b"crop" for key in regions}
    monkeypatch.setattr(m2_labels, "render", render)
    page = {"number": 1, "dpi": 144, "width": 200, "height": 400, "rotation": 90, "user_unit": 1,
            "text": "包装标签 型号：QA-4X"}
    evidence = {"source_sha256": "00" * 32, "products": [], "regions": {}, "pages": [page]}
    assert not m2_labels.printed_candidates(b"", {}, evidence)
    page["text"] += " 接线方式 规格型号"
    assert len(m2_labels.printed_candidates(b"", {}, evidence)) == 1
    product, = evidence["products"]
    assert product["part_number"] == "QA-4X" and product["questions"][0]["key"] == "printed-model"
    assert product["total_length_mm"] is None and not product["findings"]
    assert saved[product["regions"][0]]["rect"] == [0.3, 0.2, 0.45, 0.5]
    assert not m2_labels.printed_candidates(b"", {}, evidence)


def test_variable_length_requires_confirmation_and_valid_evidence():
    product = {"part_number": "QA-xxx", "drawing_no": None, "revision": None, "family": None,
               "regions": ["page"], "findings": [], "total_length_mm": "L (order 2000 or 15000 mm)",
               "questions": [{"key": "L value", "question": "Which configuration?", "regions": ["page"]}]}
    document = {"summary": "QA", "regions": {"page": {"page": 1, "label": "order table", "rect": [0, 0, 1, 1]}},
                "products": [product]}
    parsed = m2_work.parse_read(json.dumps(document), 1)["products"][0]
    assert parsed["drawing_no"] is None and parsed["total_length_mm"] is None
    assert [question["key"] for question in parsed["questions"]] == ["reading-1", "reading-2"]
    assert parsed["questions"][1]["regions"] == ["reading-1"]
    product["regions"] = ["missing"]
    with pytest.raises(ValueError, match="有效原页证据"):
        m2_work.parse_read(json.dumps(document), 1)
    product["part_number"] = None
    with pytest.raises(ValueError, match="part_number"):
        m2_work.parse_read(json.dumps(document), 1)


def test_completed_receipt_reused_offline_with_later_unknown(client, account, m12, monkeypatch):
    _, task_id, calls = shared_read(client, account, m12, monkeypatch)
    with engine.begin() as conn:
        stage = one(conn, "SELECT id FROM task_stage WHERE task_id=:task AND stage_name='m2_read'", task=task_id)
        conn.execute(text("""INSERT INTO m1_call (stage_id,project_id,route_sha,phase,attempt,reserved_cny)
            SELECT stage_id,project_id,route_sha,phase,2,reserved_cny FROM m1_call WHERE stage_id=:id AND phase='read'"""), stage)
        conn.execute(text("UPDATE task_stage SET state='ready' WHERE id=:id"), stage)
    monkeypatch.setattr(m2_work, "harness", lambda *args: pytest.fail("cached read attempted discovery"))
    assert process(claim("replay")) and calls == ["/probe", "/read"]
    with engine.connect() as conn:
        assert one(conn, "SELECT state FROM product_task WHERE id=:id", id=task_id)["state"] == "selection_ready"
        unknown = one(conn, "SELECT result,usage_ceiling_cny FROM m1_call WHERE stage_id=:id AND attempt=2", **stage)
        assert all(value is None for value in unknown.values())


def test_unsent_cooldown_defers_without_reservation(simulator, account, m12):
    stage = model_stage(account, m12)
    route = {**simulator("/route")["route"], "alternates": []}
    project = account["project"]
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO m3_route (project_id,route_sha,cooldown_until)
            VALUES (:project,UNHEX(:route),DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 30 SECOND))"""),
                     {"project": project, "route": route["route_id"]})
    with pytest.raises(m3_models.Deferred) as delayed:
        m3_models.call(stage, project, route, "read", {})
    assert 0 < delayed.value.seconds <= 30 and simulator("/stats")["calls"] == []
    with engine.begin() as conn:
        assert one(conn, "SELECT COUNT(*) AS n FROM m1_call WHERE stage_id=:id", id=stage["id"])["n"] == 0
        conn.execute(text("UPDATE m3_route SET cooldown_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 600 SECOND) WHERE project_id=:id"), {"id": project})
    assert m3_models.call(stage, project, route, "read", {})["error_type"] == "route_wait_exhausted"
    assert simulator("/stats")["calls"] == []
    with engine.begin() as conn:
        conn.execute(text("UPDATE m3_route SET cooldown_until=UTC_TIMESTAMP(6) WHERE project_id=:id"), {"id": project})
    assert run_model(stage, project, route)["http_status"] == 200
    assert [call["phase"] for call in simulator("/stats")["calls"]] == ["probe", "read"]
