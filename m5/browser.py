import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m2.qa_browser import confirm_product, file_card, request, task_panel
from m4.qa_browser import session

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/m5-step8"
FROZEN = OUT


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def upload(page, browser):
    manifest = json.loads((OUT / "load-manifest.json").read_bytes())
    receipts, batches, requests = [], [], []
    active, peak = set(), 0

    def sent(item):
        nonlocal peak
        if item.url.endswith("/files") and item.method == "POST":
            active.add(item)
            peak = max(peak, len(active))

    def finished(item):
        active.discard(item)

    def received(item):
        if item.url.endswith("/files") and item.request.method == "POST":
            body = item.json()
            requests.append({"unix": time.time(), "url": item.url, "status": item.status, "receipt": body})

    page.on("request", sent)
    page.on("requestfinished", finished)
    page.on("requestfailed", finished)
    page.on("response", received)
    started = time.time()
    for offset in range(0, 50, 10):
        samples = manifest["samples"][offset:offset + 10]
        page.get_by_role("button", name="新建批次", exact=True).click()
        page.locator('input[type="file"]').set_input_files([OUT / "samples" / item["file"] for item in samples])
        action = page.get_by_role("button", name="上传并开始处理", exact=True)
        action.click()
        expect(page.get_by_role("status")).to_contain_text("创建任务", timeout=90000)
        expect(action).to_be_enabled(timeout=90000)
        batch_id = int(page.get_by_label("当前批次").input_value())
        original = request(page, f"/batches/{batch_id}").json()
        assert len(original["files"]) == 10
        action.click()
        expect(page.get_by_role("status")).to_contain_text("复用任务", timeout=90000)
        expect(action).to_be_enabled(timeout=90000)
        repeated = request(page, f"/batches/{batch_id}").json()
        assert [(item["id"], item["task_id"]) for item in original["files"]] == [(item["id"], item["task_id"]) for item in repeated["files"]]
        for item in repeated["files"]:
            sample = next(sample for sample in samples if sample["file"] == item["filename"])
            assert item["sha256"] == sample["sha256"]
            receipts.append({"sample_id": sample["id"], "batch_id": batch_id, "source_id": item["id"],
                             "task_id": item["task_id"], "sha256": item["sha256"]})
        batches.append(batch_id)
        page.screenshot(path=OUT / f"edge-upload-{batch_id}.png", full_page=True)
        save("upload.json", {"started_unix": started, "ended_unix": time.time(), "batches": batches,
             "receipts": receipts, "http_uploads": requests, "peak_upload_concurrency": peak,
             "channel": "msedge", "edge_version": browser.version})
        print(f"Web uploaded {len(receipts)}/50; duplicate tasks reused", flush=True)
    assert len(receipts) == len({item["task_id"] for item in receipts}) == 50
    assert len(requests) == 100 and sum(item["receipt"]["reused"] for item in requests) == 50


def finish(page):
    upload_record = json.loads((FROZEN / "upload.json").read_bytes())
    manifest = json.loads((FROZEN / "load-manifest.json").read_bytes())
    deadline = time.monotonic() + 2400
    while True:
        batches = [request(page, f"/batches/{batch}").json() for batch in upload_record["batches"]]
        pending = [item for batch in batches for item in batch["files"] if item["state"] not in ("selection_ready", "failed")]
        save("load-progress.json", {"unix": time.time(), "pending_count": len(pending), "batches": batches})
        if not pending:
            break
        assert time.monotonic() < deadline, [(item["task_id"], item["state"]) for item in pending]
        page.wait_for_timeout(3000)
    results = []
    for batch in batches:
        page.get_by_label("当前批次").select_option(str(batch["id"]))
        for item in batch["files"]:
            sample = next(sample for sample in manifest["samples"] if sample["file"] == item["filename"])
            result = {"sample_id": sample["id"], "task_id": item["task_id"], "state": item["state"],
                      "error": item["error_text"], "target_parts": sample["targets"], "products": [], "missing_targets": []}
            if item["state"] == "selection_ready":
                detail = request(page, f"/tasks/{item['task_id']}/m2").json()
                panel = task_panel(page, item["task_id"], file_card(page, item["filename"]), "查看详情")
                candidates = [product["part_number"] for product in detail["evidence"]["products"]]
                chosen = [part for part in sample["targets"] if part in candidates]
                result.update(candidates=candidates, missing_targets=[part for part in sample["targets"] if part not in candidates])
                for part in chosen:
                    panel.get_by_role("checkbox", name=f"选择 {part}", exact=True).check()
                if chosen:
                    endpoint = f"/api/tasks/{item['task_id']}/products"
                    with page.expect_response(lambda response: response.url.endswith(endpoint)) as response:
                        panel.get_by_role("button", name="派生选中型号", exact=True).click()
                    assert response.value.ok, response.value.text()
                    result["products"] = response.value.json()["products"]
                result["read"] = detail
            results.append(result)
            save("load-results.json", {"ended_unix": time.time(), "results": results})
    save("final-batches.json", [request(page, f"/batches/{batch}").json() for batch in upload_record["batches"]])
    page.screenshot(path=OUT / "edge-final-load.png", full_page=True)
    print(f"50 file tasks reached recorded outcomes; {sum(item['state']=='selection_ready' for item in results)} shared reads passed", flush=True)


def retry_routes(page):
    upload_record = json.loads((OUT / "upload.json").read_bytes())
    retried = []
    for batch_id in upload_record["batches"]:
        page.get_by_label("当前批次").select_option(str(batch_id))
        batch = request(page, f"/batches/{batch_id}").json()
        for item in batch["files"]:
            if item["state"] != "failed" or item["error_text"] != "当前项目的已获准视觉渠道不可用":
                continue
            endpoint = f"/api/tasks/{item['task_id']}/retry"
            with page.expect_response(lambda response: response.url.endswith(endpoint)) as response:
                file_card(page, item["filename"]).get_by_role("button", name="重试此项", exact=True).click()
            assert response.value.ok and response.value.json() == {"retried": [item["task_id"]]}
            retried.append({"batch_id": batch_id, "task_id": item["task_id"], "before": item, "unix": time.time()})
    save("route-retries.json", retried)
    print(f"Retried {len(retried)} route failures using original tasks in Edge", flush=True)


def repair(page, browser):
    receipts = json.loads((FROZEN / "upload.json").read_bytes())["receipts"]
    before = json.loads((OUT / "database-before.json").read_bytes())
    history = json.loads((FROZEN / "linux/database.json").read_bytes())
    log = []
    for receipt in receipts:
        task = receipt["task_id"]
        stage = next(stage for stage in before["task_stage"] if stage["task_id"] == task and stage["stage_name"] == "m2_read")
        if stage["state"] != "failed":
            continue
        calls = [call for call in before["m1_call"] if call["stage_id"] == stage["id"]]
        assert calls == [call for call in history["m1_call"] if call["stage_id"] == stage["id"]]
        complete = [call for call in calls if call["phase"] == "read" and call["result"] and
                    json.loads(call["result"]).get("finish_reason") == "completed" and json.loads(call["result"]).get("request_id")]
        skipped = any(event["kind"] == "model_route_skipped" and event["stage_id"] == stage["id"] for event in before["stage_event"])
        action = "cached" if complete else "unsent" if not calls and skipped else "hold"
        entry = {**receipt, "stage_id": stage["id"], "action": action, "ledger_rows": len(calls), "unix": time.time()}
        if action != "hold":
            page.get_by_label("当前批次").select_option(str(receipt["batch_id"]))
            batch = request(page, f"/batches/{receipt['batch_id']}").json()
            item = next(item for item in batch["files"] if item["task_id"] == task)
            assert item["sha256"] == receipt["sha256"] and item["id"] == receipt["source_id"] and item["state"] == "failed"
            with page.expect_response(lambda response: response.url.endswith(f"/api/tasks/{task}/retry")) as response:
                file_card(page, item["filename"]).get_by_role("button", name="重试此项", exact=True).click()
            assert response.value.ok and response.value.json() == {"retried": [task]}
            entry.update(channel="msedge", edge_version=browser.version, http_status=response.value.status)
        log.append(entry)
        save("repair-retries.json", log)
    print(f"Reviewed {len(log)} failed original tasks and submitted {sum(entry['action'] != 'hold' for entry in log)} retries")


def review_m12(page, sample, database_file):
    database = json.loads(database_file.read_bytes())
    receipt = next(item for item in json.loads((FROZEN / "upload.json").read_bytes())["receipts"] if item["sample_id"] == sample)
    file = next(item for item in request(page, f"/batches/{receipt['batch_id']}").json()["files"] if item["task_id"] == receipt["task_id"])
    assert receipt["sha256"] == file["sha256"]
    records = []
    for pins, origin, length in ((4, 8, "24.70"), (5, 9, "26.70")):
        part = f"M12A0{pins}-03-301-M16"
        previous = next(item for item in database["product_task"] if item["id"] == origin)
        source = next(item for item in database["source_file"] if item["id"] == previous["source_id"])
        version = next(item for item in database["m2_version"] if item["task_id"] == origin and item["state"] == "published")
        evidence = request(page, f"/tasks/{receipt['task_id']}/m2").json()["evidence"]
        assert evidence["source_sha256"] == file["sha256"] and version["part_number"] == part
        assert source["sha"] == file["sha256"] or evidence["equivalent_read"]["peer_source_sha256"] == source["sha"]
        prior = [item for item in database["m2_confirmation"] if item["task_id"] == origin and item["answered_by"] and item["answer"]]
        assert any(length + " mm" in json.loads(item["answer"])["value"] for item in prior)
        answers = {"verified-length": f"{pins} 针 / {length} mm"}
        if pins == 5:
            answers["five-pin-view"] = next(json.loads(item["answer"])["value"] for item in prior if item["question_key"] == "five-pin-view")
        task = next(item["task_id"] for item in file["products"] if item["part_number"] == part)
        detail = request(page, f"/tasks/{task}/m2").json()
        assert {item["key"]: item["expected"] for item in detail["specification"]["questions"]} == answers
        confirm_product(page, OUT, receipt["batch_id"], part, "")
        records.append({"sample": sample, "source_sha256": file["sha256"], "reviewed_source_sha256": source["sha"],
                        "reviewed_task": origin, "reviewed_report_sha256": version["report_sha"],
                        "reviewed_answers": prior, "load_task": task, "answers": answers, "blind": False, "automatically_delivered": False})
        save("prior-review.json", records)


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("upload", "finish", "retry-routes", "repair", "review-m12"))
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--sample", default="L01")
    parser.add_argument("--review-baseline", type=Path)
    parser.add_argument("--env", type=Path, default=OUT / "deploy.env")
    parser.add_argument("--url", default="https://127.0.0.1:8449")
    args = parser.parse_args()
    OUT = args.output
    credentials = dict(line.split("=", 1) for line in args.env.read_text(encoding="utf-8-sig").splitlines()
                       if line.startswith(("BOOTSTRAP_LOGIN=", "BOOTSTRAP_PASSWORD=")))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = session(browser, args.url, credentials)
        if args.action == "upload":
            upload(page, browser)
        elif args.action == "repair":
            repair(page, browser)
        elif args.action == "review-m12":
            review_m12(page, args.sample, args.review_baseline or OUT / "database-before.json")
        elif args.action == "finish":
            finish(page)
        else:
            retry_routes(page)
        browser.close()


if __name__ == "__main__":
    main()
