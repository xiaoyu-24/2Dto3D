import json
import os
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

import pytest
from sqlalchemy import event as sql_event, text
from sqlalchemy.exc import OperationalError

from app import m1_read, m2_cad, m3_models
from app.db import engine, one, rows
from app.ingest import put_bytes
from app.m1_sample import encode_json
from app.m2_work import enqueue
from app.queue import claim
from test_worker import isolated_job


@pytest.fixture
def simulator(monkeypatch):
    url = os.environ.get("M3_SIM_URL")
    if not url:
        pytest.skip("isolated HTTP simulator required")
    monkeypatch.setenv("M1_HARNESS_URL", url)

    def request(path, payload=None):
        with urlopen(Request(url + path, data=encode_json(payload) if payload is not None else None,
                             headers={"Content-Type": "application/json"}), timeout=5) as response:
            return json.load(response)
    request("/configure", {})
    return request


def model_stage(account, m12):
    isolated_job(account, m12)
    return claim("m3-model-matrix")


def run_model(stage, project, route):
    while True:
        try:
            return m3_models.call(stage, project, route, "read", {"prompt": "checkpoint", "images": []})
        except m3_models.Deferred as waiting:
            assert waiting.seconds <= 2
            time.sleep(waiting.seconds + 0.05)


@pytest.mark.parametrize("status", [429, 503, 401, 403])
def test_http_fallback_backoff_cooldown_and_usage(status, simulator, account, m12):
    errors = [{"status": status, "retry_after": "1"}] * (2 if status in (429, 503) else 1)
    simulator("/configure", {"sequence": {"primary:read": errors}})
    stage = model_stage(account, m12)
    route = m1_read.harness("/route")["route"]
    result = run_model(stage, account["project"], route)
    assert result["http_status"] == 200
    calls = simulator("/stats")["calls"]
    reads = [item for item in calls if item["phase"] == "read"]
    assert [item["route"] for item in reads] == ["primary"] * len(errors) + ["backup"]
    if len(errors) == 2:
        assert reads[1]["time"] - reads[0]["time"] >= 1
    before = len(calls)
    assert run_model(stage, account["project"], route) == result
    assert len(simulator("/stats")["calls"]) == before
    with engine.connect() as conn:
        state = one(conn, "SELECT * FROM m3_route WHERE project_id=:project AND route_sha=:route",
                    project=account["project"], route=bytes.fromhex(route["route_id"]))
        assert bool(state["disabled"]) == (status in (401, 403))
        assert state["cooldown_until"]
        usage = rows(conn, "SELECT result,usage_ceiling_cny FROM m1_call WHERE stage_id=:stage", stage=stage["id"])
        assert all(item["usage_ceiling_cny"] is not None for item in usage)
        assert sum(json.loads(item["result"])["http_status"] == status for item in usage) == len(errors)
    next_stage = model_stage(account, m12)
    assert run_model(next_stage, account["project"], route)["http_status"] == 200
    assert all(item["route"] == "backup" for item in simulator("/stats")["calls"][before:])


def test_pending_request_is_not_repeated_or_failed_over(simulator, account, m12):
    simulator("/configure", {"sequence": {"primary:read": [{"disconnect": True}]}})
    stage = model_stage(account, m12)
    route = m1_read.harness("/route")["route"]
    for _ in range(2):
        assert run_model(stage, account["project"], route)["error_type"] == "reconciliation_pending"
    assert [(item["route"], item["phase"]) for item in simulator("/stats")["calls"]] == [
        ("primary", "probe"), ("primary", "read")]
    with engine.connect() as conn:
        saved = one(conn, "SELECT * FROM m1_call WHERE stage_id=:stage AND phase='read'", stage=stage["id"])
        assert saved["result"] is None and saved["usage_ceiling_cny"] is None and saved["reserved_cny"] > 0


def test_harness_unknown_receipt_keeps_reservation_and_does_not_repeat(simulator, account, m12, monkeypatch):
    original = m1_read.harness
    sent = []

    def uncertain(path, route=None, payload=None):
        if path == "/read":
            sent.append(route["route_id"])
            return {"sent": True, "http_status": None, "request_id": None, "usage": {}, "finish_reason": "error"}
        return original(path, route, payload)

    monkeypatch.setattr(m1_read, "harness", uncertain)
    stage = model_stage(account, m12)
    route = m1_read.harness("/route")["route"]
    for _ in range(2):
        assert run_model(stage, account["project"], route)["error_type"] == "reconciliation_pending"
    assert sent == [route["route_id"]]
    with engine.connect() as conn:
        saved = one(conn, "SELECT * FROM m1_call WHERE stage_id=:stage AND phase='read'", stage=stage["id"])
        assert json.loads(saved["result"])["error_type"] == "reconciliation_pending"
        assert saved["usage_ceiling_cny"] is None and saved["reserved_cny"] > 0


def test_unqualified_backup_and_budget_do_not_call_read(simulator, account, m12):
    simulator("/configure", {"sequence": {"primary:read": [{"status": 401}], "backup:probe": [{"passed": False}]}})
    stage = model_stage(account, m12)
    route = m1_read.harness("/route")["route"]
    assert run_model(stage, account["project"], route)["error_type"] == "no_qualified_route"
    assert not any(item["route"] == "backup" and item["phase"] == "read" for item in simulator("/stats")["calls"])
    another = model_stage(account, m12)
    with engine.begin() as conn:
        conn.execute(text("UPDATE project SET daily_budget_cny=0.001 WHERE id=:id"), {"id": account["project"]})
    before = len(simulator("/stats")["calls"])
    fresh_route = {**route["alternates"][0], "route_id": "c3" * 32}
    assert m3_models.call(another, account["project"], fresh_route, "read", {})["error_type"] == "budget_unavailable"
    assert len(simulator("/stats")["calls"]) == before


def test_half_open_is_single_and_long_retry_does_not_block_backup(simulator, account, m12):
    simulator("/configure", {"sequence": {"primary:read": [{"status": 429, "retry_after": "600"}]}})
    stage = model_stage(account, m12)
    route = m1_read.harness("/route")["route"]
    assert run_model(stage, account["project"], route)["http_status"] == 200
    assert len([item for item in simulator("/stats")["calls"] if item["route"] == "primary" and item["phase"] == "read"]) == 1
    other = model_stage(account, m12)
    with engine.begin() as conn:
        conn.execute(text("UPDATE m3_route SET cooldown_until=DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 1 SECOND) WHERE project_id=:id"),
                     {"id": account["project"]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda current: m3_models.route_state(current, account["project"], route), [stage, other]))
    assert sum(result is not None for result in outcomes) == 1


def test_http_date_retry_after_and_bounded_jitter():
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=12), usegmt=True)
    assert 10 <= m3_models.retry_delay(future) <= 12
    assert m3_models.retry_delay("0") == 0
    assert 1 <= m3_models.retry_delay(None) <= 1.5


def test_failed_primary_capability_probe_uses_qualified_backup(simulator, account, m12):
    simulator("/configure", {"sequence": {"primary:probe": [{"passed": False}]}})
    stage = model_stage(account, m12)
    route = m1_read.harness("/route")["route"]
    proof = m3_models.call(stage, account["project"], route, "probe", {})
    assert proof["passed"] is True
    assert run_model(stage, account["project"], route)["http_status"] == 200
    calls = simulator("/stats")["calls"]
    assert [(item["route"], item["phase"]) for item in calls] == [
        ("primary", "probe"), ("backup", "probe"), ("backup", "read")]


def publication_fixture(account, m12):
    receipt = isolated_job(account, m12)
    with engine.begin() as conn:
        conn.execute(text("UPDATE task_stage SET state='done' WHERE id=:id"), {"id": receipt["stage_id"]})
        basis = put_bytes(conn, encode_json({"geometry_revision": "G1"}))
        checks = put_bytes(conn, encode_json({"checks": [{"name": "dimension", "expected": 24.7}]}))
        geometry = put_bytes(conn, b"unit geometry")
        html = put_bytes(conn, b"unit html")
        identity = {"part_number": "unit-part", "version": "A0-M3", "geometry_revision": "G1",
                    "geometry_sha256": geometry.hex(), "basis_sha256": basis.hex(), "nut_installed_pose": None}
        body = b"/* M1 " + encode_json(identity) + b" */\nunit step"
        check = {"passed": True, "geometry_sha256": geometry.hex(), "step_sha256": m2_cad.fingerprint(body),
                 "checklist_sha256": checks.hex(), "environment": m2_cad.ISOLATION, "freecad": "unit", "occt": "unit",
                 "checks": [{"name": "dimension", "expected": 24.7, "measured": 24.7, "passed": True,
                             "kind": "drawing", "evidence_regions": ["test"]}]}
        html_check = encode_json({"passed": True, "geometry_sha256": geometry.hex(), "html_sha256": html.hex(), "edge_version": "unit"}).decode()
        version = conn.execute(text("""INSERT INTO m2_version
            (task_id,project_id,part_number,version,basis_sha,checklist_sha,geometry_sha,html_sha,html_check,state)
            VALUES (:task,:project,'unit-part','A0-M3',:basis,:checks,:geo,:html,:html_check,'partial')"""),
                               {"task": receipt["task_id"], "project": account["project"], "basis": basis, "checks": checks,
                                "geo": geometry, "html": html, "html_check": html_check}).lastrowid
        enqueue(conn, receipt["task_id"], "m2_cad", basis)
    return claim("unit-publisher", cad=True), version, body, check


def test_publication_rollback_lost_receipt_and_stale_fence(account, m12):
    stage, version, body, check = publication_fixture(account, m12)

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO m3_publication"):
            raise OperationalError(statement, {}, RuntimeError("QA before commit"))

    sql_event.listen(engine, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(OperationalError):
            m2_cad.store_step(stage, body, check)
    finally:
        sql_event.remove(engine, "before_cursor_execute", fail_insert)
    with engine.connect() as conn:
        assert one(conn, "SELECT state FROM m2_version WHERE id=:id", id=version)["state"] == "partial"
        assert one(conn, "SELECT COUNT(*) AS count FROM m3_publication WHERE version_id=:id", id=version)["count"] == 0
    result = m2_cad.store_step(stage, body, check)
    assert result["state"] == "published"
    assert m2_cad.store_step(stage, body, check) == {**result, "reused": True}
    with engine.connect() as conn:
        assert one(conn, "SELECT COUNT(*) AS count FROM m3_publication WHERE version_id=:id", id=version)["count"] == 1
        assert one(conn, "SELECT COUNT(*) AS count FROM stage_event WHERE stage_id=:id AND kind='published'", id=stage["id"])["count"] == 1
    stale = {**stage, "fence": stage["fence"] - 1}
    for action in (lambda: m2_cad.store_step(stale, body, check), lambda: m2_cad.store_html(stale, b"", {}),
                   lambda: m2_cad.frozen(stale, {})):
        with pytest.raises(ValueError, match="租约"):
            action()
