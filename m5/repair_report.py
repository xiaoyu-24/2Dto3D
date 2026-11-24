import argparse
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

import fitz

from metrics import distribution, maximum_concurrency, read, size, stamp


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def call_id(call):
    return tuple(call[key] for key in ("stage_id", "route_sha", "phase", "attempt"))


def costs(calls):
    known, pending = Decimal(0), Decimal(0)
    tokens = Counter()
    for call in calls:
        result = json.loads(call["result"]) if call["result"] else {}
        assert result.get("actual_model") in (None, "deepseek-flash")
        if call["usage_ceiling_cny"] is None:
            pending += Decimal(call["reserved_cny"])
        else:
            known += Decimal(call["usage_ceiling_cny"])
        tokens.update({key: value for key, value in result.get("usage", {}).items() if isinstance(value, int)})
    return {"calls": len(calls), "known_upper_cny": str(known), "pending_reserve_cny": str(pending), "tokens": dict(tokens)}


def validate_ledger_extensions(calls, database, ledger):
    stages = {item["id"]: item for item in database["task_stage"]}
    events = defaultdict(list)
    for item in database["stage_event"]:
        events[item["stage_id"]].append(item)
    known = {call_id(item) for item in ledger}
    extensions = [item for item in calls if call_id(item) not in known]
    records = []
    for call in extensions:
        stage = stages[call["stage_id"]]
        result = json.loads(call["result"] or "{}")
        if stage["stage_name"] == "m5_title_review":
            if call["phase"] == "probe":
                assert (result.get("actual_model") == "deepseek-flash" and result.get("http_status") == 200
                        and result.get("finish_reason") == "completed" and result.get("request_id")
                        and result.get("passed") is True and call["usage_ceiling_cny"] is not None)
                status = "completed_probe"
            else:
                assert call["phase"] == "read"
                checked = [json.loads(item["detail"]) for item in events[call["stage_id"]]
                           if item["kind"] == "focused_title_checked"]
                assert len(checked) == 1
                record = checked[0]
                assert record["task"] == stage["task_id"] and record.get("request_id") == result.get("request_id")
                assert record.get("transcribed", "") == result.get("response", "")
                assert record.get("error") == result.get("error_type")
                if call["usage_ceiling_cny"] is None:
                    assert (result.get("error_type") == "reconciliation_pending" and result.get("request_id") is None
                            and record["exact_match"] is False)
                    status = "unknown_receipt_preserved"
                else:
                    assert (result.get("actual_model") == "deepseek-flash" and result.get("http_status") == 200
                            and result.get("finish_reason") == "completed" and result.get("request_id")
                            and float(call["usage_ceiling_cny"]) > 0)
                    if record["exact_match"]:
                        assert record["transcribed"] == result["response"]
                    status = "exact_title" if record["exact_match"] else "nonmatching_title"
            records.append({"stage_id": call["stage_id"], "task_id": stage["task_id"], "phase": call["phase"],
                            "status": status, "request_id": result.get("request_id"),
                            "usage_ceiling_cny": str(call["usage_ceiling_cny"]) if call["usage_ceiling_cny"] is not None else None})
        else:
            assert stage["stage_name"] == "m2_cad" and call["phase"] == "cad_select"
            assert (result.get("actual_model") == "deepseek-flash" and result.get("http_status") == 200
                    and result.get("finish_reason") == "completed" and result.get("request_id")
                    and call["usage_ceiling_cny"] is not None)
            records.append({"stage_id": call["stage_id"], "task_id": stage["task_id"], "phase": call["phase"],
                            "status": "completed_cad_select", "request_id": result["request_id"],
                            "usage_ceiling_cny": str(call["usage_ceiling_cny"])})
    return records


def target_rows(folder, frozen, database, results):
    tasks = {task["id"]: task for task in database["product_task"]}
    sources = {item["id"]: item for item in database["source_file"]}
    products = {product["task_id"]: product for product in database["m2_product"]}
    versions = {version["task_id"]: version for version in database["m2_version"]}
    entries = []
    for sample in frozen["samples"]:
        path = folder / "samples" / sample["file"]
        result = results[sample["id"]]
        file_task = tasks[result["task_id"]]
        source = sources[file_task["source_id"]]
        assert path.stat().st_size == sample["bytes"] and digest(path) == source["sha"] == sample["sha256"]
        assert result["state"] == file_task["state"] and result["target_parts"] == sample["targets"]
        with fitz.open(path) as document:
            for part in sample["targets"]:
                product = next((row for row in products.values() if tasks[row["task_id"]]["source_id"] == source["id"]
                                and row["part_number"] == part), None)
                task_id = product["task_id"] if product else None
                confirmations = [item for item in database["m2_confirmation"] if item["task_id"] == task_id]
                version = versions.get(task_id)
                state = tasks[task_id]["state"] if product else "receipt_pending" if file_task["state"] == "failed" else "target_missing"
                text_hits = [{"page": page.number + 1, "rotation": page.rotation, "unrotated_rect": list(box)} for page in document
                             if part in page.get_text() for box in page.search_for(part)]
                entries.append({"sample": sample["id"], "source_sha256": source["sha"], "category": sample["category"],
                                "file_task": file_task["id"], "product_task": task_id, "target": part, "state": state,
                                "candidates": result.get("candidates", []), "text_layer_hits_for_review": text_hits,
                                "confirmations": confirmations, "specification": json.loads(product["specification"]) if product else None,
                                "version_id": version["id"] if version else None})
    return entries


def timings(database, stages):
    starts, waits, completed, measurements = {}, {}, [], defaultdict(list)
    for event in database["stage_event"]:
        stage_id = event["stage_id"]
        if stage_id not in stages:
            continue
        key = (stage_id, event["fence"])
        when, kind = stamp(event["created_at"]), event["kind"]
        if kind in ("started", "reclaimed"):
            starts[key] = when
        if kind == "derived" and json.loads(event["detail"]).get("question_count", 0):
            waits[stage_id] = when
        if kind == "confirmation_resumed":
            completed.append(when - waits.pop(stage_id))
        if key in starts and kind in ("completed", "shared_read_completed", "failed", "model_backoff", "needs_confirmation",
                                      "cad_job_pending", "basis_frozen", "published", "focused_title_checked"):
            measurements[stages[stage_id]["stage_name"]].append(when - starts.pop(key))
    return ({name: distribution(measurements[name]) for name in ("pdf_extract", "m2_read", "m5_title_review", "m2_prepare", "m2_cad")},
            {"completed": distribution(completed), "open_task_count": len(waits)})


def verify_downloads(run):
    files = run / "downloads"
    with ZipFile(files / "deliveries.zip") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        members = {"manifest.json": archive.read("manifest.json")}
        assert manifest == read(files / "manifest.json")
        for version in read(files / "versions.json"):
            zipped = next(item for item in manifest["versions"] if item["id"] == version["id"])
            assert set(version["files"]) == {"html", "step", "report"}
            for kind, item in version["files"].items():
                name = zipped["files"][kind]["path"]
                path = files / item["filename"]
                members[name] = archive.read(name)
                assert members[name] == path.read_bytes() and digest(path) == item["sha256"] and path.stat().st_size == item["byte_count"]
        assert sorted(archive.namelist()) == sorted(members)
    for version in (1, 4, 17, 18):
        offline = read(run / f"offline-{version}/edge-offline.json")
        assert offline["passed"] and offline["offline"] and offline["channel"] == "msedge" and offline["remote_requests"] == offline["page_errors"] == []
    assert read(run / "permissions/permissions.json")["zip_status"] == 404
    return {"sha256": digest(files / "deliveries.zip"), "bytes": (files / "deliveries.zip").stat().st_size,
            "members": {name: {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()} for name, body in members.items()}}


def peer_evidence(frozen, targets, results, output):
    samples = read(frozen / "load-manifest.json")["samples"]
    pages = {}
    for sample in samples:
        with fitz.open(frozen / "samples" / sample["file"]) as document:
            pages[sample["id"]] = []
            for page in document:
                page.set_rotation(0)
                image = page.get_pixmap(dpi=144, colorspace=fitz.csRGB, alpha=False)
                pages[sample["id"]].append((image.width, image.height, hashlib.sha256(image.samples).hexdigest()))
    review = []
    for target in targets:
        if target["state"] not in ("target_missing", "receipt_pending"):
            continue
        sample, part = target["sample"], target["target"]
        peers = [{"sample": other, "task": result["task_id"], "source_page": source + 1, "peer_page": peer + 1,
                  "width_height_pixel_sha256": identity, "peer_read_sha256": result["read"]["shared_read_sha256"]}
                 for other, result in results.items() if other != sample and part in result.get("candidates", [])
                 for source, identity in enumerate(pages[sample]) for peer, candidate in enumerate(pages[other]) if identity == candidate]
        entry = {"sample": sample, "part_number": part, "same_page_candidates": peers}
        if sample in ("L16", "L20", "L37", "L41"):
            with fitz.open(frozen / "samples" / (sample + ".pdf")) as document:
                page = document[0]
                page.set_rotation(0)
                width, height = page.rect.width, page.rect.height
                bounds = fitz.Rect(width * .55, height * .822, width * .975, height * .852)
                crop = output / f"{sample}-order-number.png"
                page.get_pixmap(dpi=144, clip=bounds).save(crop)
                entry.update(order_number_crop=crop.name, crop_sha256=digest(crop), unrotated_bbox=list(bounds))
        review.append(entry)
    write(output / "page-comparison.json", review)


def main(run, supplement=None):
    frozen, linux = run.parent, run / "linux"
    output = supplement or run
    database, before = read((supplement or linux) / "database.json"), read(run / "database-before.json")
    manifest, upload = read(frozen / "load-manifest.json"), read(frozen / "upload.json")
    results = {item["sample_id"]: item for item in read(output / "load-results.json")["results"]}
    assert len(results) == len(set(item["task_id"] for item in results.values())) == 50
    assert all(results[item["sample_id"]]["task_id"] == item["task_id"] for item in upload["receipts"])
    targets = target_rows(frozen, manifest, database, results)
    task_ids = {item[key] for item in targets for key in ("file_task", "product_task") if item[key]}
    stages = {item["id"]: item for item in database["task_stage"] if item["task_id"] in task_ids}
    calls = [item for item in database["m1_call"] if item["stage_id"] in stages]
    previous = {call_id(item): item for item in before["m1_call"] if item["stage_id"] in stages}
    assert previous == {call_id(item): item for item in calls if call_id(item) in previous}
    new_calls = [item for item in calls if call_id(item) not in previous]
    route = {call["route_sha"] for call in previous.values()}
    assert len(route) == 1 and all(call["project_id"] == 1 and call["route_sha"] in route for call in calls)
    retry = read(run / "repair-retries.json")
    for item in retry:
        stage_id = item["stage_id"]
        additions = [call for call in new_calls if call["stage_id"] == stage_id]
        if item["action"] in ("hold", "cached"):
            assert not additions
        if item["action"] == "cached":
            request_id = results[item["sample_id"]]["read"]["evidence"]["model_run"]["request_id"]
            assert any(call["stage_id"] == stage_id and call["result"] and json.loads(call["result"]).get("request_id") == request_id for call in previous.values())
    published = [row for row in targets if row["state"] == "published"]
    for row in published:
        version = next(item for item in database["m2_version"] if item["id"] == row["version_id"])
        assert json.loads(version["step_check"])["passed"] and json.loads(version["html_check"])["passed"]
        assert sum(item["version_id"] == version["id"] for item in database["m3_publication"]) == 1
    categories = []
    for category in read(frozen / "blind-freeze.json")["categories"]:
        selected = [row for row in targets if row["category"] == category]
        failures = [{"sample": row["sample"], "file_task": row["file_task"], "target": row["target"],
                     "state": row["state"], "product_task": row["product_task"], "candidates": row["candidates"]}
                    for row in selected if row["state"] in ("target_missing", "receipt_pending", "basis_pending")]
        source_ids = {row["file_task"] for row in selected}
        eligible = source_ids | {row["product_task"] for row in selected}
        group = {key: stage for key, stage in stages.items() if stage["task_id"] in eligible}
        elapsed, human = timings(database, group)
        categories.append({"category": category, "files": len(source_ids), "targets": len(selected),
                           "selection_ready": sum(row["state"] == "selection_ready" for row in results.values() if row["task_id"] in source_ids),
                           "states": dict(Counter(row["state"] for row in selected)), "failure_count": len(failures),
                           "failures": failures, "ever_confirm": sum(bool(row["confirmations"]) for row in selected),
                           "durations": elapsed, "human_wait": human, "cost": costs([call for call in calls if call["stage_id"] in group])})
    faults = read(linux / "faults.json")
    assert [item["exit"] for item in faults["operations"]] == [0, 83, 0, 0] and faults["load_completions_during_faults"]
    assert all(faults[key] for key in ("checkpoint_reused", "stale_read_rejected", "lost_receipt_database_unchanged", "late_cad_rejected", "current_pending_unchanged"))
    assert faults["database_retry_model_call_delta"] == faults["database_retry_cad_build_export_verify_delta"] == 0
    retried_at = min(item["unix"] for item in retry if item["action"] != "hold")
    latest_read = max(stamp(event["created_at"]) for event in database["stage_event"] if event["stage_id"] in stages and event["kind"] == "shared_read_completed")
    captures = [json.loads(line) for line in (linux / "resources.jsonl").read_text().splitlines()]
    resources = [record for record in captures if retried_at <= record["unix"] <= latest_read]
    outcome = {"frozen_manifest_sha256": digest(frozen / "load-manifest.json"), "files": dict(Counter(row["state"] for row in results.values())),
               "targets": dict(Counter(row["state"] for row in targets)), "categories": categories, "retry_actions": dict(Counter(row["action"] for row in retry)),
               "new_model_cost": costs(new_calls), "total_model_cost": costs(calls), "unreconciled": [call for call in calls if call["usage_ceiling_cny"] is None],
               "elapsed_from_original_upload_to_last_read_s": latest_read - upload["started_unix"], "repair_to_last_read_s": latest_read - retried_at,
               "repair_model_peak_known_calls": maximum_concurrency([(stamp(call["started_at"]), stamp(call["finished_at"])) for call in new_calls if call["finished_at"]]),
               "file_window_resource_samples": len(resources), "observed_peaks": {"ready": max(sum(row["count"] for row in rec["queue"]["stages"] if row["state"] == "ready") for rec in resources),
                   "cpu_sum_percent": max(sum(float(item["CPUPerc"].strip("%")) for item in rec["containers"]) for rec in resources),
                   "container_ram_bytes": max(sum(size(item["MemUsage"].split("/")[0]) for item in rec["containers"]) for rec in resources)},
               "load_cad_wall": distribution([row["ended_unix"]-row["started_unix"] for row in read(linux / "load-cad.json")]),
               "downloads": verify_downloads(run), "step8_passed": False}
    if supplement:
        previous_calls = read(run / "load-call-ledger.json")
        previous = read(run / "pages/database.json")
        previous_call_ids = {call_id(item) for item in previous_calls}
        prior_database_calls = [item for item in previous["m1_call"] if item["stage_id"] in
                                {stage_id for stage_id, stage in stages.items() if stage["task_id"] in task_ids}]
        extension_receipts = validate_ledger_extensions(prior_database_calls, previous, previous_calls)
        assert calls == prior_database_calls
        previous_stages = {item["id"] for item in previous["task_stage"]}
        previous_versions = {item["id"] for item in previous["m2_version"]}
        new_cad_stages = [item for item in database["task_stage"] if item["task_id"] in task_ids
                          and item["stage_name"] == "m2_cad" and item["id"] not in previous_stages]
        new_versions = [item for item in database["m2_version"] if item["task_id"] in task_ids
                        and item["id"] not in previous_versions]
        repeat_check = {"browser_action": "finish_only", "new_file_task_ids": len(set(item["task_id"] for item in results.values())
                           - {item["task_id"] for item in upload["receipts"]}),
                        "preexisting_ledger_extensions": extension_receipts,
                        "new_m1_call_ids_since_previous_database": len({call_id(item) for item in calls}
                                                                        - {call_id(item) for item in prior_database_calls}),
                        "new_m2_cad_stage_ids": len(new_cad_stages), "new_m2_version_ids": len(new_versions)}
        write(output / "repeated.json", repeat_check)
        assert not any(repeat_check[key] for key in ("new_file_task_ids", "new_m1_call_ids_since_previous_database",
                                                     "new_m2_cad_stage_ids", "new_m2_version_ids"))
        assert not (previous_call_ids - {call_id(item) for item in prior_database_calls})
        peer_evidence(frozen, targets, results, output)
    write(output / "target-review.json", targets)
    write(output / "load-call-ledger.json", calls)
    write(output / "repair-metrics.json", outcome)
    print(json.dumps({key: outcome[key] for key in ("files", "targets", "total_model_cost", "step8_passed")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--supplement", type=Path)
    args = parser.parse_args()
    main(args.output, args.supplement)
