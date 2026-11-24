import json
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy import text

from app.db import engine, one, rows
from app.m1_read import recorded_call
from app.queue import event, live_stage

RETRYABLE = (429, 503)


class Deferred(Exception):
    def __init__(self, seconds, reason):
        self.seconds = seconds
        self.reason = reason
        super().__init__(reason)


def retry_delay(value):
    if value:
        try:
            return max(0, int(value))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return 1 + random.uniform(0, 0.5)


def defer(stage, failure):
    with engine.begin() as conn:
        if not live_stage(conn, stage):
            return False
        conn.execute(text("""UPDATE task_stage SET state='ready',owner=NULL,lease_until=NULL,
            ready_at=TIMESTAMPADD(MICROSECOND,:delay,UTC_TIMESTAMP(6)),error_text=:reason WHERE id=:id"""),
                     {"id": stage["id"], "delay": round(failure.seconds * 1_000_000), "reason": failure.reason})
        conn.execute(text("UPDATE product_task SET state='retry_wait' WHERE id=:id"), {"id": stage["task_id"]})
        event(conn, stage, "model_backoff", {"seconds": failure.seconds, "reason": failure.reason})
    return True


def route_state(stage, project, route, waits=None):
    with engine.begin() as conn:
        if not live_stage(conn, stage):
            return None
        key = {"project": project, "route": bytes.fromhex(route["route_id"])}
        conn.execute(text("""INSERT INTO m3_route (project_id,route_sha) VALUES (:project,:route)
            ON DUPLICATE KEY UPDATE project_id=m3_route.project_id"""), key)
        state = one(conn, """SELECT *,TIMESTAMPDIFF(MICROSECOND,UTC_TIMESTAMP(6),cooldown_until)/1000000 AS wait,
            probe_until>UTC_TIMESTAMP(6) AS probing FROM m3_route WHERE project_id=:project AND route_sha=:route FOR UPDATE""", **key)
        if state["disabled"] or state["wait"] and state["wait"] > 0 or state["probing"]:
            if waits is not None and not state["disabled"]:
                waits.append(max(1, float(state["wait"] or 0), 5 if state["probing"] else 0))
            event(conn, stage, "model_route_skipped", {"route_id": route["route_id"], "reason": state["reason"] or "half_open_probe"})
            return None
        if state["cooldown_until"]:
            conn.execute(text("""UPDATE m3_route SET probe_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 180 SECOND)
                WHERE project_id=:project AND route_sha=:route"""), key)
        return state


def circuit(stage, project, route, result, exhausted=False):
    status = result.get("http_status")
    disabled = status in (401, 403)
    seconds = max(30, retry_delay(result.get("retry_after"))) if status in RETRYABLE else 30
    unhealthy = disabled or exhausted or status != 200 or result.get("passed") is False
    with engine.begin() as conn:
        if not live_stage(conn, stage):
            return
        conn.execute(text("""UPDATE m3_route SET disabled=:disabled,probe_until=NULL,
            cooldown_until=IF(:unhealthy,TIMESTAMPADD(MICROSECOND,:delay,UTC_TIMESTAMP(6)),NULL),reason=:reason
            WHERE project_id=:project AND route_sha=:route"""),
                     {"disabled": disabled, "unhealthy": unhealthy, "delay": round(seconds * 1_000_000),
                      "reason": f"http_{status}" if unhealthy else None, "project": project, "route": bytes.fromhex(route["route_id"])})
        if unhealthy:
            event(conn, stage, "model_route_unavailable", {"route_id": route["route_id"], "status": status,
                                                          "disabled": disabled, "cooldown_seconds": seconds})


def invoke(stage, project, route, phase, payload, attempt=1):
    for number in range(attempt, 3):
        with engine.connect() as conn:
            previous = one(conn, """SELECT result,TIMESTAMPDIFF(MICROSECOND,COALESCE(finished_at,started_at),UTC_TIMESTAMP(6))/1000000 AS age
                FROM m1_call WHERE stage_id=:stage AND route_sha=:route AND phase=:phase AND attempt=:attempt""",
                           stage=stage["id"], route=bytes.fromhex(route["route_id"]), phase=phase, attempt=number)
        result = recorded_call(stage, project, route, phase, payload, attempt=number)
        if result.get("http_status") not in RETRYABLE or number == 2:
            return result
        delay = retry_delay(result.get("retry_after"))
        with engine.connect() as conn:
            elapsed = one(conn, """SELECT TIMESTAMPDIFF(SECOND,MIN(started_at),UTC_TIMESTAMP(6)) AS age
                FROM m1_call WHERE stage_id=:stage""", stage=stage["id"])["age"]
        if elapsed + delay > 300:
            return result
        if previous and previous["age"] >= delay:
            continue
        remaining = max(0, delay - float(previous["age"])) if previous else delay
        with engine.begin() as conn:
            if live_stage(conn, stage):
                conn.execute(text("""UPDATE m3_route SET cooldown_until=TIMESTAMPADD(MICROSECOND,:delay,UTC_TIMESTAMP(6)),
                    probe_until=NULL,reason='retry_after' WHERE project_id=:project AND route_sha=:route"""),
                             {"delay": round(remaining * 1_000_000), "project": project, "route": bytes.fromhex(route["route_id"])})
        raise Deferred(remaining,
                       f"{route.get('name', route['route_id'][:12])} HTTP {result['http_status']}，等待后重试")
    return result


def call(stage, project, route, phase, payload, *, attempt=1):
    routes = [route, *route.get("alternates", [])]
    with engine.connect() as conn:
        saved = rows(conn, """SELECT result FROM m1_call WHERE stage_id=:stage AND phase=:phase
            ORDER BY started_at DESC""", stage=stage["id"], phase=phase)
    results = [json.loads(item["result"]) if item["result"] else {} for item in saved]
    if any(result.get("http_status") is None for result in results):
        return {"error_type": "reconciliation_pending"}
    for result in results:
        if result.get("http_status") == 200 and attempt == 1 and (phase != "probe" or result.get("passed") is True):
            return result
    waits = []
    tried_route = False
    for index, candidate in enumerate(routes):
        state = route_state(stage, project, candidate, waits)
        if state is None:
            continue
        tried_route = True
        if phase != "probe":
            with engine.connect() as conn:
                qualified = one(conn, """SELECT result FROM m1_call WHERE project_id=:project AND route_sha=:route
                    AND phase='probe' AND started_at>=UTC_DATE() ORDER BY started_at DESC LIMIT 1""",
                                project=project, route=bytes.fromhex(candidate["route_id"]))
            proof = json.loads(qualified["result"]) if qualified and qualified["result"] else {}
            if state["cooldown_until"] or not proof.get("passed") or not proof.get("request_id"):
                proof = invoke(stage, project, candidate, "probe", {})
            if not proof.get("passed") or not proof.get("request_id"):
                circuit(stage, project, candidate, proof, exhausted=True)
                if proof.get("error_type") in ("budget_unavailable", "reconciliation_pending", "lease_lost"):
                    return proof
                continue
        result = invoke(stage, project, candidate, phase, payload, attempt)
        if result.get("error_type") not in ("budget_unavailable", "lease_lost"):
            circuit(stage, project, candidate, result, exhausted=result.get("http_status") in RETRYABLE)
        with engine.begin() as conn:
            if live_stage(conn, stage):
                checkpoint = one(conn, "SELECT output_sha FROM task_stage WHERE id=:id", id=stage["id"])["output_sha"]
                event(conn, stage, "model_route_result", {"route_id": candidate["route_id"], "phase": phase,
                    "alternate": index > 0 or bool(candidate.get("discovery_fallback")),
                    "discovery_fallback": candidate.get("discovery_fallback"),
                    "status": result.get("http_status"), "error_type": result.get("error_type"),
                    "checkpoint_sha256": checkpoint.hex() if checkpoint else None})
        if (result.get("http_status") == 200 and (phase != "probe" or result.get("passed") is True)
                or result.get("error_type") in ("budget_unavailable", "reconciliation_pending", "lease_lost")):
            return result
    if waits and not tried_route and not saved:
        with engine.connect() as conn:
            age = one(conn, """SELECT TIMESTAMPDIFF(SECOND,COALESCE(MAX(CASE WHEN kind='retried' THEN created_at END),
                MIN(created_at)),UTC_TIMESTAMP(6)) AS elapsed FROM stage_event WHERE stage_id=:stage
                AND kind IN ('retried','started','model_route_skipped')""", stage=stage["id"])["elapsed"]
        if age is not None and age + min(waits) <= 300:
            raise Deferred(min(waits), "渠道冷却，等待可用渠道")
        return {"error_type": "route_wait_exhausted"}
    return {"error_type": "no_qualified_route"}
