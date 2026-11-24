import base64
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import text

from app import m1_read, m2_cad, m3_models
from app.db import engine, one, rows
from test_m3 import model_stage, publication_fixture, run_model

sys.path.insert(0, "/workspace")
from m2.cad_worker import Executor


@contextmanager
def discovery_service(name, *, status=200, route_value=True, passed=True):
    calls = []
    route = {"route_id": ("a1" if name == "primary" else "b2") * 32, "name": name, "model": "qa-independent"}

    class Service(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, payload, code=200):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def do_GET(self):
            calls.append({"path": self.path})
            self.respond({"route": route if route_value else None}, status)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"path": self.path, "route_id": self.headers.get("X-M1-Route"), "payload": payload})
            if self.headers.get("X-M1-Route") != route["route_id"]:
                self.respond({}, 403)
                return
            self.respond({"http_status": 200, "request_id": f"{name}-{len(calls)}", "passed": passed,
                          "finish_reason": "completed", "response": "checkpoint read", "actual_model": "qa-independent",
                          "usage": {"input_tokens": 10, "output_tokens": 5}})

    with ThreadingHTTPServer(("127.0.0.1", 0), Service) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", calls, route
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.parametrize(("status", "route_value"), [(503, True), (200, False)])
def test_primary_discovery_failure_reaches_independent_backup(status, route_value, monkeypatch, account, m12):
    with discovery_service("primary", status=status, route_value=route_value) as primary, discovery_service("backup") as backup:
        monkeypatch.setenv("M1_HARNESS_URL", primary[0])
        monkeypatch.setenv("M3_BACKUP_HARNESS_URL", backup[0])
        result = m1_read.harness("/route")
        assert [item["path"] for item in backup[1]] == ["/route"]
        route = result["route"]
        assert route["route_id"] == backup[2]["route_id"] and route["harness_url"] == backup[0]
        stage = model_stage(account, m12)
        assert run_model(stage, account["project"], route)["http_status"] == 200
        assert [item["path"] for item in primary[1]] == ["/route"]
        assert [item["path"] for item in backup[1]] == ["/route", "/probe", "/read"]
        assert backup[1][-1]["payload"]["prompt"] == "checkpoint"
        with engine.connect() as conn:
            recorded = rows(conn, "SELECT detail FROM stage_event WHERE stage_id=:id AND kind='model_route_result'", id=stage["id"])
            detail = json.loads(recorded[-1]["detail"])
            assert detail["alternate"] is True
            assert detail["discovery_fallback"] == ("http_503" if status == 503 else "empty_route")
        assert run_model(stage, account["project"], route)["http_status"] == 200
        assert len(backup[1]) == 3


@pytest.mark.parametrize(("backup_status", "configured"), [(200, True), (503, True), (200, False)])
def test_healthy_primary_keeps_own_endpoint(backup_status, configured, monkeypatch, account, m12):
    with discovery_service("primary") as primary, discovery_service("backup", status=backup_status) as backup:
        monkeypatch.setenv("M1_HARNESS_URL", primary[0])
        monkeypatch.setenv("M3_BACKUP_HARNESS_URL", backup[0] if configured else "")
        route = m1_read.harness("/route")["route"]
        assert route["route_id"] == primary[2]["route_id"] and route["harness_url"] == primary[0]
        assert bool(route.get("alternates")) == (configured and backup_status == 200)
        if configured and backup_status == 200:
            assert route["alternates"][0]["harness_url"] == backup[0]
        stage = model_stage(account, m12)
        assert run_model(stage, account["project"], route)["http_status"] == 200
        assert [item["path"] for item in primary[1]] == ["/route", "/probe", "/read"]
        assert [item["path"] for item in backup[1]] == (["/route"] if configured else [])


def test_primary_transport_unavailable_still_discovers_backup(monkeypatch):
    with discovery_service("primary") as primary:
        primary_url = primary[0]
    with discovery_service("backup") as backup:
        monkeypatch.setenv("M1_HARNESS_URL", primary_url)
        monkeypatch.setenv("M3_BACKUP_HARNESS_URL", backup[0])
        assert m1_read.harness("/route")["route"]["harness_url"] == backup[0]
        assert [item["path"] for item in backup[1]] == ["/route"]


@pytest.mark.parametrize("configured", [True, False])
def test_no_available_discovery_returns_no_route(configured, monkeypatch):
    with discovery_service("primary", status=503) as primary, discovery_service("backup", route_value=False) as backup:
        monkeypatch.setenv("M1_HARNESS_URL", primary[0])
        monkeypatch.setenv("M3_BACKUP_HARNESS_URL", backup[0] if configured else "")
        assert m1_read.harness("/route")["route"] is None
        assert [item["path"] for item in backup[1]] == (["/route"] if configured else [])


@pytest.mark.parametrize("blocked", ["capability", "budget"])
def test_discovered_backup_still_requires_capability_and_budget(blocked, monkeypatch, account, m12):
    with discovery_service("primary", status=503) as primary, discovery_service("backup", passed=blocked != "capability") as backup:
        monkeypatch.setenv("M1_HARNESS_URL", primary[0])
        monkeypatch.setenv("M3_BACKUP_HARNESS_URL", backup[0])
        route = m1_read.harness("/route")["route"]
        stage = model_stage(account, m12)
        if blocked == "budget":
            with engine.begin() as conn:
                conn.execute(text("UPDATE product_task SET budget_cny=1 WHERE id=:id"), {"id": stage["task_id"]})
        result = run_model(stage, account["project"], route)
        assert result["error_type"] == ("no_qualified_route" if blocked == "capability" else "budget_unavailable")
        assert [item["path"] for item in backup[1]] == (["/route", "/probe"] if blocked == "capability" else ["/route"])


class DatabaseExecutor(Executor):
    def mysql(self, action, stage=None, **arguments):
        return m2_cad.execute({"action": action, "stage": stage, **arguments})


@pytest.mark.parametrize("action", ["frozen", "html", "step"])
def test_late_fence_cannot_replace_newer_pending(action, tmp_path):
    class RejectingExecutor(Executor):
        def mysql(self, *args, **kwargs):
            raise ValueError("租约已失效")

    executor = RejectingExecutor("test", tmp_path)
    stage = {"id": 21, "task_id": 14, "input_sha": "42" * 32, "fence": 3, "owner": "new", "attempts": 3}
    stale = {**stage, "fence": 2, "owner": "old", "attempts": 2}
    with pytest.raises(ValueError):
        executor.submit(action, stage, body="current", check={})
    path = executor.pending(stage, action)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        executor.submit(action, stale, body="late", check={})
    assert path.read_bytes() == original
    assert executor.restore(stage, action) == {"body": "current", "check": {}}
    assert executor.restore({**stage, "fence": 4}, action) == {"body": "current", "check": {}}


def test_concurrent_fences_keep_distinct_payloads_and_latest_restore(tmp_path):
    class OfflineExecutor(Executor):
        def mysql(self, *args, **kwargs):
            return None

    executor = OfflineExecutor("test", tmp_path)
    stage = {"id": 21, "task_id": 14, "input_sha": "42" * 32, "owner": "qa", "attempts": 1}
    barrier = threading.Barrier(4)

    def submit(fence):
        barrier.wait()
        executor.submit("step", {**stage, "fence": fence}, body=f"fence-{fence}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(submit, [1, 2, 9, 10]))
    for fence in (1, 2, 9, 10):
        saved = json.loads(executor.pending({**stage, "fence": fence}, "step").read_bytes())
        assert saved["arguments"]["body"] == f"fence-{fence}"
    assert executor.restore({**stage, "fence": 11}, "step")["body"] == "fence-10"
    assert executor.restore({**stage, "fence": 9}, "step")["body"] == "fence-9"
    assert executor.restore({**stage, "input_sha": "43" * 32, "fence": 11}, "step") is None


def test_legacy_pending_recovers_authoritative_publication_after_takeover(account, m12, tmp_path):
    first, version, body, check = publication_fixture(account, m12)
    with engine.begin() as conn:
        conn.execute(text("UPDATE task_stage SET fence=fence+1 WHERE id=:id"), {"id": first["id"]})
    current = {**first, "fence": first["fence"] + 1}
    result = m2_cad.store_step(current, body, check)
    saved_stage = m2_cad.stage_wire(first)
    legacy = tmp_path / "pending" / f"{saved_stage['id']}-{saved_stage['input_sha']}" / "step.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"stage": saved_stage, "arguments": {"body": base64.b64encode(body).decode(), "check": check}}))
    DatabaseExecutor("test", tmp_path).recover_receipts()
    receipts = list(tmp_path.rglob("receipt.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_bytes())
    assert receipt["report_sha256"] == result["report_sha256"] and receipt["fence"] == current["fence"]
    assert receipt["step_sha256"] == check["step_sha256"]
    assert m2_cad.execute({"action": "receipt", "stage": saved_stage, "step_sha256": "00" * 32}) is None
    assert m2_cad.execute({"action": "receipt", "stage": {**saved_stage, "input_sha": "00" * 32},
                          "step_sha256": check["step_sha256"]}) is None
    with pytest.raises(ValueError, match="租约"):
        m2_cad.store_step(first, body, check)
    with engine.connect() as conn:
        assert one(conn, "SELECT COUNT(*) AS total FROM m3_publication WHERE version_id=:id", id=version)["total"] == 1


def test_current_publication_survives_stale_submit_then_restart(account, m12, tmp_path):
    first, version, body, check = publication_fixture(account, m12)
    executor = DatabaseExecutor("test", tmp_path)
    wire = m2_cad.stage_wire(first)
    arguments = {"body": base64.b64encode(body).decode(), "check": check}
    with engine.begin() as conn:
        conn.execute(text("UPDATE task_stage SET fence=fence+1 WHERE id=:id"), {"id": first["id"]})
    current = {**wire, "fence": wire["fence"] + 1}
    executor.submit("step", current, **arguments)
    original = executor.pending(current, "step").read_bytes()
    with pytest.raises(ValueError, match="租约"):
        executor.submit("step", wire, **arguments)
    assert executor.pending(current, "step").read_bytes() == original
    DatabaseExecutor("test", tmp_path).recover_receipts()
    receipt = json.loads(executor.pending(current, "receipt").read_bytes())
    assert receipt["fence"] == current["fence"] and receipt["version_id"] == version
    with engine.connect() as conn:
        assert one(conn, "SELECT COUNT(*) AS total FROM stage_event WHERE stage_id=:id AND kind='published'",
                   id=first["id"])["total"] == 1


def test_takeover_restages_before_commit_and_recovers_lost_receipt(account, m12, tmp_path):
    first, version, body, check = publication_fixture(account, m12)
    wire = m2_cad.stage_wire(first)
    arguments = {"body": base64.b64encode(body).decode(), "check": check}

    class BeforeCommit(Executor):
        def mysql(self, *args, **kwargs):
            raise ValueError("QA before commit")

    with pytest.raises(ValueError, match="before commit"):
        BeforeCommit("test", tmp_path).submit("step", wire, **arguments)
    with engine.begin() as conn:
        conn.execute(text("UPDATE task_stage SET fence=fence+1 WHERE id=:id"), {"id": first["id"]})
    current = {**wire, "fence": wire["fence"] + 1}
    actions = []

    class LoseReceipt(DatabaseExecutor):
        def mysql(self, action, stage=None, **payload):
            actions.append(action)
            result = super().mysql(action, stage, **payload)
            if action == "step":
                raise SystemExit(83)
            return result

        def cad(self, *args, **kwargs):
            pytest.fail("恢复不应重跑 CAD 或导出")

    executor = LoseReceipt("test", tmp_path)
    with pytest.raises(SystemExit) as stopped:
        executor.process(current, None, None, 0)
    assert stopped.value.code == 83 and actions == ["inputs", "step"]
    assert json.loads(executor.pending(current, "step").read_bytes())["stage"]["fence"] == current["fence"]
    restarted = DatabaseExecutor("test", tmp_path)
    with pytest.raises(ValueError, match="租约"):
        restarted.submit("step", wire, **arguments)
    restarted.recover_receipts()
    receipt = json.loads(restarted.pending(current, "receipt").read_bytes())
    assert receipt["fence"] == current["fence"] and receipt["version_id"] == version
    with engine.connect() as conn:
        assert one(conn, "SELECT COUNT(*) AS total FROM m1_call WHERE stage_id=:id", id=first["id"])["total"] == 0
