import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/m5-step8"


def read(path):
    return json.loads(path.read_bytes())


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0, "p50_seconds": None, "p95_seconds": None}
    return {"count": len(values), "p50_seconds": round(values[math.ceil(len(values) * .50) - 1], 3),
            "p95_seconds": round(values[math.ceil(len(values) * .95) - 1], 3), "total_seconds": round(sum(values), 3)}


def maximum_concurrency(intervals):
    events = sorted((point, change) for begin, end in intervals for point, change in ((begin, 1), (end, -1)))
    active = peak = 0
    for point, change in events:
        active += change
        peak = max(peak, active)
    return peak


def size(value):
    number, unit = value.strip().split() if " " in value.strip() else (None, None)
    if number is None:
        import re
        match = re.fullmatch(r"([\d.]+)(\w+)", value.strip())
        number, unit = match.groups()
    return float(number) * {"B": 1, "kB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3,
                          "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3}[unit]


def main():
    manifest = read(OUT / "load-manifest.json")
    upload = read(OUT / "upload.json")
    results = read(OUT / "load-results.json")
    database = read(OUT / "linux/database.json")
    faults = read(OUT / "linux/faults.json")
    samples = {item["id"]: item for item in manifest["samples"]}
    file_tasks = {item["task_id"] for item in upload["receipts"]}
    source_ids = {item["source_id"] for item in upload["receipts"]}
    tasks = {item["id"] for item in database["product_task"] if item["source_id"] in source_ids}
    stages = {item["id"]: item for item in database["task_stage"] if item["task_id"] in tasks}
    all_events = defaultdict(list)
    for event in database["stage_event"]:
        all_events[event["stage_id"]].append(event)
    durations, waits = defaultdict(list), defaultdict(list)
    duration_samples = []
    file_end = []
    for stage_id, stage in stages.items():
        started, queued = {}, None
        for event in all_events[stage_id]:
            when = stamp(event["created_at"])
            if event["kind"] in ("accepted", "shared_read_queued", "product_derived", "retried"):
                queued = when
            if event["kind"] in ("started", "reclaimed"):
                started[event["fence"]] = when
                if queued is not None:
                    waits[stage["stage_name"]].append(when - queued)
            elif event["kind"] in ("completed", "shared_read_completed", "failed", "needs_confirmation", "cad_job_pending", "basis_frozen", "published", "model_backoff"):
                begin = started.pop(event["fence"], None)
                if begin is not None:
                    durations[stage["stage_name"]].append(when - begin)
                    duration_samples.append((stage["task_id"], stage["stage_name"], when - begin))
                if stage["task_id"] in file_tasks and stage["stage_name"] == "m2_read":
                    file_end.append(when)
    calls = [item for item in database["m1_call"] if item["stage_id"] in stages]
    real_calls, simulated_calls, reconciliations = [], [], []
    total, input_tokens, output_tokens = Decimal(0), 0, 0
    end = max(file_end)
    intervals = []
    for call in calls:
        result = json.loads(call["result"]) if call["result"] else {}
        if result.get("actual_model") == "qa-simulated":
            simulated_calls.append(call)
            continue
        real_calls.append(call)
        usage = result.get("usage", {})
        input_tokens += sum(usage.get(key, 0) for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        output_tokens += usage.get("output_tokens", 0)
        if call["usage_ceiling_cny"] is None:
            reconciliations.append(call)
        else:
            total += Decimal(call["usage_ceiling_cny"])
        intervals.append((stamp(call["started_at"]), stamp(call["finished_at"]) if call.get("finished_at") else end))
    resource_records = [json.loads(line) for line in (OUT / "linux/resources.jsonl").read_text().splitlines()]
    peaks = defaultdict(float)
    load_samples = 0
    for record in resource_records:
        if not (upload["started_unix"] <= record["unix"] <= end):
            continue
        load_samples += 1
        containers = [item for item in record["containers"] if item["Name"].startswith("drawing-step8-qa")]
        peaks["container_memory_sum_bytes"] = max(peaks["container_memory_sum_bytes"], sum(size(item["MemUsage"].split("/")[0]) for item in containers))
        peaks["container_cpu_sum_percent"] = max(peaks["container_cpu_sum_percent"], sum(float(item["CPUPerc"].strip("%")) for item in containers))
        memory = {key: int(value.split()[0]) * 1024 for key, value in
                  (line.split(":", 1) for line in record["memory"].splitlines()) if key in ("MemTotal", "MemAvailable")}
        peaks["host_total_minus_available_bytes"] = max(peaks["host_total_minus_available_bytes"], memory["MemTotal"] - memory["MemAvailable"])
        peaks["loadavg_one_minute"] = max(peaks["loadavg_one_minute"], float(record["loadavg"].split()[0]))
        peaks["running_stages"] = max(peaks["running_stages"], sum(item["count"] for item in record["queue"]["stages"] if item["state"] == "running"))
        peaks["ready_backlog"] = max(peaks["ready_backlog"], sum(item["count"] for item in record["queue"]["stages"] if item["state"] == "ready"))
    category_rows = []
    for category in ("connector", "plate", "shaft", "gear", "assembly"):
        selected = [item for item in results["results"] if samples[item["sample_id"]]["category"] == category]
        selected_tasks = {item["task_id"] for item in selected}
        category_sources = {receipt["source_id"] for receipt in upload["receipts"] if receipt["task_id"] in selected_tasks}
        category_tasks = {task["id"] for task in database["product_task"] if task["source_id"] in category_sources}
        category_stages = {stage_id for stage_id, stage in stages.items() if stage["task_id"] in category_tasks}
        category_calls = [call for call in real_calls if call["stage_id"] in category_stages]
        waiting_tasks = {item["task_id"] for item in database["m2_confirmation"]
                         if item["task_id"] in category_tasks and item["answer"] is None}
        category_cost = sum((Decimal(call["usage_ceiling_cny"]) for call in category_calls if call["usage_ceiling_cny"] is not None), Decimal(0))
        failures = [item for item in selected if item["state"] == "failed" or item["missing_targets"]]
        versions = [item for item in database["m2_version"] if item["task_id"] in tasks and
                    any(task["id"] == item["task_id"] and task["source_id"] in
                        {receipt["source_id"] for receipt in upload["receipts"] if receipt["task_id"] in selected_tasks}
                        for task in database["product_task"])]
        category_rows.append({"category": category, "load_files": len(selected), "input_targets": sum(len(samples[item["sample_id"]]["targets"]) for item in selected),
                              "load_read_passed": sum(item["state"] == "selection_ready" for item in selected),
                              "failures": [{key: item.get(key) for key in ("sample_id", "task_id", "state", "error", "missing_targets")} for item in failures],
                              "published_load_versions": sum(item["state"] == "published" for item in versions), "blind_sample_count": 0,
                              "processing": {name: distribution([seconds for task_id, stage_name, seconds in duration_samples
                                                                  if task_id in category_tasks and stage_name == name])
                                             for name in durations},
                              "real_model_calls": len(category_calls), "estimated_model_upper_bound_cny": str(category_cost),
                              "unreconciled_calls": [call["stage_id"] for call in category_calls if call["usage_ceiling_cny"] is None],
                              "open_confirmations": sum(item["task_id"] in category_tasks and item["answer"] is None for item in database["m2_confirmation"]),
                              "observed_load_waiting_target_tasks": len(waiting_tasks),
                              "observed_load_rates": {
                                  "automatic_correct_published": {"numerator": 0,
                                      "denominator": sum(len(samples[item["sample_id"]]["targets"]) for item in selected),
                                      "value": 0 if selected else None},
                                  "critical_release": {"numerator": 0,
                                      "denominator": sum(item["state"] == "published" for item in versions), "value": None},
                                  "confirmation": {"numerator": len(waiting_tasks),
                                      "denominator": sum(len(samples[item["sample_id"]]["targets"]) for item in selected),
                                      "value": len(waiting_tasks) / sum(len(samples[item["sample_id"]]["targets"]) for item in selected) if selected else None}},
                              "automatic_correct": {"numerator": 0, "denominator": 0, "value": None},
                              "critical_release": {"numerator": 0, "denominator": 0, "value": None},
                              "confirmation_rate": {"numerator": 0, "denominator": 0, "value": None},
                              "cost_per_correct_product_cny": None, "reason": "no eligible independent human annotation; not calculable"})
    commands = [json.loads(line) for line in (OUT / "linux/commands.jsonl").read_text().splitlines()]
    cad_commands = [entry for entry in commands if "run" in entry["command"] and any(service in entry["command"] for service in ("cad", "step-check", "html-check"))]
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "load_manifest_sha256": hashlib.sha256((OUT / "load-manifest.json").read_bytes()).hexdigest(),
              "originals": manifest["original_count"],
              "derived_fixtures": 50 - manifest["original_count"], "unique_load_files": len(file_tasks), "http_submissions": len(upload["http_uploads"]),
              "duplicate_receipts": sum(item["receipt"]["reused"] for item in upload["http_uploads"]),
              "file_outcomes": dict(Counter(item["state"] for item in results["results"])), "category_rows": category_rows,
              "processing": {name: distribution(values) for name, values in durations.items()},
              "queue_wait": {name: distribution(values) for name, values in waits.items()},
              "file_batch_elapsed_seconds": round(end - upload["started_unix"], 3), "upload_elapsed_seconds": upload["ended_unix"] - upload["started_unix"],
              "manual_wait": {"answered": 0, "open_confirmations": sum(item["task_id"] in tasks and item["answer"] is None for item in database["m2_confirmation"]),
                              "completed_wait_seconds": None, "note": "no human annotation or confirmation invented; unanswered waits are censored"},
              "actual_concurrency": {"web_upload": upload["peak_upload_concurrency"], "model_calls": maximum_concurrency(intervals),
                                     "configured_business_workers": 2, "cad_dispatchers": 1},
              "peaks_including_fault_and_monitor_overhead": dict(peaks), "resource_samples": len(resource_records),
              "resource_samples_during_file_load": load_samples,
              "cpu_note": "sum of separately sampled Docker CPU counters, not host CPU utilization; /proc/stat was not sampled",
              "cad_wall": {"scope": "M5 fault runs and their retries; load products without a supported frozen job did not run CAD",
                           **distribution([entry["seconds"] for entry in cad_commands])},
              "model_cost": {"real_calls": len(real_calls), "input_tokens_including_cache": input_tokens, "output_tokens": output_tokens,
                             "price_cny_per_million": {"input_including_cache_upper_bound": 2, "output": 8},
                             "estimated_known_upper_bound_cny": str(total), "unreconciled": reconciliations,
                             "reserved_unreconciled_cny": str(sum((Decimal(call["reserved_cny"]) for call in reconciliations), Decimal(0))),
                             "simulated_calls_in_load": len(simulated_calls), "cost_per_correct_product_cny": None,
                             "correct_product_denominator": 0, "supplier_invoice_reconciled": False},
              "faults_passed": bool(faults["late_cad_rejected"] and faults["checkpoint_reused"] and faults["database_retry_model_call_delta"] == 0),
              "gates": {"50_read_attempts_recorded": len(results["results"]) == 50,
                        "50_file_outcomes_recorded": len(results["results"]) == 50 and all(item["state"] in ("selection_ready", "failed") for item in results["results"]),
                        "five_category_blind_records": False, "observed_blind_critical_releases_zero": None, "step8_passed": False}}
    (OUT / "metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "model-calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"file_outcomes": report["file_outcomes"], "known_cost_upper_bound_cny": str(total), "step8_passed": False}))


if __name__ == "__main__":
    main()
