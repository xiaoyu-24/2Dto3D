import argparse
import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageChops
from playwright.sync_api import expect, sync_playwright

SAMPLES = (
    Path("D:/projects/3D/测试文件/B040108M0-2XX2XX01.pdf"),
    Path("D:/projects/3D/测试文件/WLT2F9-141XX0200.pdf"),
    Path("D:/desktop/测试文件.pdf"),
)


def login(browser, url, credentials):
    context = browser.new_context(base_url=url, accept_downloads=True, viewport={"width": 1470, "height": 940})
    page = context.new_page()
    page.goto(url)
    page.get_by_label("账号").fill(credentials["BOOTSTRAP_LOGIN"])
    page.get_by_label("密码").fill(credentials["BOOTSTRAP_PASSWORD"])
    page.get_by_role("button", name="登录", exact=True).click()
    expect(page.get_by_role("heading", name="图纸任务", exact=True)).to_be_visible()
    assert "Edg/" in page.evaluate("navigator.userAgent")
    return page


def request(page, path, body=None, status=200):
    response = (page.request.get(f"/api{path}") if body is None else
                page.request.post(f"/api{path}", data=body, headers={"X-Requested-With": "drawing-web"}))
    assert response.status == status, (path, response.status, response.text()[:300])
    return response


def upload(page, output, paths, batch_id):
    if batch_id is None:
        page.get_by_role("button", name="新建批次").click()
    else:
        page.get_by_label("当前批次").select_option(str(batch_id))
    page.locator('input[type="file"]').set_input_files(paths)
    button = page.get_by_role("button", name="上传并开始处理")
    button.click()
    notice = page.get_by_role("status")
    expect(notice).to_contain_text("创建任务" if batch_id is None else "复用任务", timeout=30000)
    batch_id = int(page.get_by_label("当前批次").input_value())
    before = request(page, f"/batches/{batch_id}").json()
    assert {item["filename"] for item in before["files"]} == {path.name for path in paths}
    button.click()
    expect(notice).to_contain_text("复用任务", timeout=30000)
    after = request(page, f"/batches/{batch_id}").json()
    assert [(item["id"], item["task_id"]) for item in after["files"]] == [(item["id"], item["task_id"]) for item in before["files"]]
    page.screenshot(path=output / "edge-upload.png", full_page=True)
    (output / "batch.json").write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Batch #{batch_id} uploaded in Edge: {[item['task_id'] for item in after['files']]}")


def select_batch(page, batch_id):
    page.get_by_label("当前批次").select_option(str(batch_id))
    expect(page.get_by_text(f"批次 #{batch_id} · 自动更新")).to_be_visible()


def file_card(page, filename):
    return page.locator(".m2-file").filter(has=page.get_by_text(filename, exact=True))


def task_panel(page, task_id, locator, label):
    locator.get_by_role("button", name=label, exact=True).click()
    heading = page.get_by_role("heading", name=f"任务 #{task_id}", exact=True)
    expect(heading).to_be_visible()
    return heading.locator("xpath=../..")


def derive(page, output, batch_id):
    select_batch(page, batch_id)
    existing = request(page, f"/batches/{batch_id}").json()["files"]
    pending = [file for file in existing if file["stage_state"] == "failed"]
    if pending:
        page.get_by_role("button", name="只重试失败项").click()
        expected = '、'.join(f"#{file['task_id']}" for file in pending)
        expect(page.get_by_text(f"已重试任务 {expected}", exact=True)).to_be_visible()
    choices = {SAMPLES[0].name: ["B040108M0-2XX2XX01"], SAMPLES[1].name: ["WLT2F9-141XX0200"],
               SAMPLES[2].name: ["M12A04-03-301-M16", "M12A05-03-301-M16"]}
    for file in existing:
        card = file_card(page, file["filename"])
        expect(card.get_by_text("联合读图完成 · 可选型号", exact=True)).to_be_visible(timeout=120000)
        detail = task_panel(page, file["task_id"], card, "查看详情")
        for part in choices[file["filename"]]:
            detail.get_by_role("checkbox", name=f"选择 {part}", exact=True).check()
        action = detail.get_by_role("button", name="派生选中型号")
        action.click()
        expect(detail.get_by_role("status")).to_contain_text("产品 #")
        action.click()
        expect(detail.get_by_role("status")).to_contain_text("复用产品 #")
    saved = request(page, f"/batches/{batch_id}").json()
    for file in saved["files"]:
        assert set(choices[file["filename"]]).issubset(item["part_number"] for item in file["products"])
    (output / "derived.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Derived", [(item["part_number"], item["task_id"]) for file in saved["files"] for item in file["products"]])


def select_variant(page, output, batch_id, template, part):
    select_batch(page, batch_id)
    batch = request(page, f"/batches/{batch_id}").json()
    file = next(item for item in batch["files"] if item["filename"] == f"{template}.pdf")
    detail = task_panel(page, file["task_id"], file_card(page, file["filename"]), "查看详情")
    detail.get_by_role("checkbox", name=f"选择 {template}", exact=True).check()
    detail.get_by_label("具体料号").fill(part)
    action = detail.get_by_role("button", name="派生选中型号", exact=True)
    endpoint = f"/api/tasks/{file['task_id']}/products"
    with page.expect_response(lambda response: response.url.endswith(endpoint)) as created:
        action.click()
    assert created.value.ok, created.value.text()
    product = created.value.json()["products"][0]
    assert product["part_number"] == part
    with page.expect_response(lambda response: response.url.endswith(endpoint)) as repeated:
        action.click()
    assert repeated.value.json() == {"products": [{**product, "reused": True}]}
    before = request(page, f"/tasks/{file['task_id']}/m2").json()
    child = request(page, f"/tasks/{product['task_id']}/m2").json()
    assert before["shared_read_sha256"] == child["shared_read_sha256"] and before["calls"] == child["calls"]
    confirm_product(page, output, batch_id, part, f"确认 {part}")
    row = page.get_by_role("row").filter(has=page.get_by_text(part, exact=True))
    expect(row.get_by_text("等待建模作业", exact=True)).to_be_visible()
    page.screenshot(path=output / f"edge-variant-{product['task_id']}.png", full_page=True)


def candidate_selection(page, output, batch_id):
    select_batch(page, batch_id)
    batch = request(page, f"/batches/{batch_id}").json()
    tasks = [item["task_id"] for file in batch["files"] for item in [file, *file["products"]]]
    before = {task_id: {"detail": request(page, f"/tasks/{task_id}/m2").json(),
                        "events": request(page, f"/tasks/{task_id}/events").json()} for task_id in tasks}
    packaging = "包装（同料号，卷盘/载带）"
    rejected, reused = [], []
    cases = [(SAMPLES[0].name, ["B040108M0-2XX2XX01"], ["B040108M0-2102AA01"], "B040108M0-2202AA01",
              [packaging, "B040108M0-2XX2XX01（XX=针数，模板）", "B040108M0-2XX2XX01（60针示例配置）", "B040108M0-2112AA01", "M12A04-03-301-M16"]),
             (SAMPLES[1].name, ["WLT2F9-141XX0200"], ["WLT2F9-1415A0200"], "WLT2F9-1411A0200",
              [packaging, "WLT2F9-141XX0200（模板）", "WLT2F9-1415S0200", "M12A04-03-301-M16"]),
             (SAMPLES[2].name, ["M12A04-03-301-M16", "M12A05-03-301-M16"],
              ["M12A04-03-301-M16", "M12A05-03-301-M16"], None, [])]
    for filename, candidates, parts, new_part, invalid in cases:
        file = next(item for item in batch["files"] if item["filename"] == filename)
        task_id = file["task_id"]
        endpoint = f"/tasks/{task_id}/products"
        evidence = before[task_id]["detail"]["evidence"]
        assert [item["part_number"] for item in evidence["products"]] == candidates
        panel = task_panel(page, task_id, file_card(page, filename), "查看详情")
        expect(panel.get_by_role("checkbox")).to_have_count(len(candidates))
        expect(panel.get_by_role("checkbox", name=f"选择 {packaging}", exact=True)).to_have_count(0)
        for part in invalid:
            for selection in ([part], [new_part, part]):
                response = request(page, endpoint, {"part_numbers": selection}, status=422)
                rejected.append({"task_id": task_id, "part_numbers": selection, "status": response.status})
        if invalid:
            package = evidence["regions"]["verified-package"]
            panel.get_by_role("button", name=package["view"], exact=True).first.click()
            frame = next(item for item in evidence["pages"] if item["number"] == 2)
            locate(page, panel, task_id, frame, package, "verified-package", evidence["images"]["verified-package"], output)
        for candidate in candidates:
            panel.get_by_role("checkbox", name=f"选择 {candidate}", exact=True).check()
        if len(candidates) == 1:
            panel.get_by_label("具体料号").fill(parts[0])
        expected = [{"task_id": product["task_id"], "part_number": part, "reused": True} for part in parts
                    for product in file["products"] if product["part_number"] == part]
        assert len(expected) == len(parts)
        for _ in range(2):
            with page.expect_response(lambda response: response.url.endswith(f"/api{endpoint}")) as reply:
                panel.get_by_role("button", name="派生选中型号", exact=True).click()
            assert reply.value.status == 200 and reply.value.json() == {"products": expected}
            reused.extend(reply.value.json()["products"])
        page.screenshot(path=output / f"edge-candidates-{task_id}.png", full_page=True)
    assert request(page, f"/batches/{batch_id}").json() == batch
    for task_id, original in before.items():
        assert request(page, f"/tasks/{task_id}/m2").json() == original["detail"]
        assert request(page, f"/tasks/{task_id}/events").json() == original["events"]
    audit = {"user_agent": page.evaluate("navigator.userAgent"), "rejected": rejected, "reused": reused,
             "batch_unchanged": True, "tasks_unchanged": tasks, "before": before}
    (output / "candidate-selection.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Candidates verified in Edge: {len(rejected)} rejected requests, {len(reused)} reused products; tasks and evidence unchanged")


def confirm_product(page, output, batch_id, part, answer):
    select_batch(page, batch_id)
    data = request(page, f"/batches/{batch_id}").json()
    product = next(product for file in data["files"] for product in file["products"] if product["part_number"] == part)
    task_id = product["task_id"]
    detail = task_panel(page, task_id, page.get_by_role("row").filter(has=page.get_by_text(part, exact=True)), "查看产品")
    result = request(page, f"/tasks/{task_id}/m2").json()
    for item in result["confirmations"]:
        question = item["question"]
        heading = detail.get_by_role("heading", name=f"确认 · {question}", exact=True)
        section = heading.locator("xpath=..")
        evidence = item["evidence"][0]
        section.get_by_role("button", name=evidence["view"], exact=True).click()
        expect(detail.get_by_role("img", name=f"{evidence['view']}局部图", exact=True)).to_be_visible()
        if item["answer"]:
            continue
        expected = next(spec.get("expected") for spec in result["specification"]["questions"] if spec["key"] == item["question_key"])
        entry = section.get_by_label("确认结论")
        if expected:
            entry.select_option(expected)
        else:
            entry.fill(answer)
        with page.expect_response(lambda response: response.url.endswith(f"/confirmations/{item['id']}/answer")) as reply:
            section.get_by_role("button", name="提交确认", exact=True).click()
        assert reply.value.ok, reply.value.text()
        expect(section.get_by_text("已确认", exact=False)).to_be_visible()
        duplicate = request(page, f"/confirmations/{item['id']}/answer", {"answer": expected or answer}).json()
        assert duplicate == {"reused": True, "task_id": task_id, "resumed": False}
    audit = request(page, f"/tasks/{task_id}/m2").json()
    assert audit["confirmations"] and all(item["answer"] and item["operator"] and item["answered_at"] for item in audit["confirmations"])
    events = request(page, f"/tasks/{task_id}/events").json()
    assert sum(event["kind"] == "confirmation_resumed" for event in events) == 1
    page.screenshot(path=output / f"edge-confirm-{task_id}.png", full_page=True)
    print(f"Confirmed #{task_id} {part}, questions={len(audit['confirmations'])}, resumed exactly once")


def retry(page, output, batch_id, part):
    select_batch(page, batch_id)
    data = request(page, f"/batches/{batch_id}").json()
    if part:
        product = next(item for file in data["files"] for item in file["products"] if item["part_number"] == part)
        command = page.get_by_role("row").filter(has=page.get_by_text(part, exact=True)).get_by_role("button", name="重试此项")
        expected = [product["task_id"]] if product["stage_state"] == "failed" else []
        endpoint = f"/api/tasks/{product['task_id']}/retry"
    else:
        command = page.get_by_role("button", name="只重试失败项")
        expected = sorted(item["task_id"] for file in data["files"] for item in [file, *file["products"]] if item["stage_state"] == "failed")
        endpoint = f"/api/batches/{batch_id}/retry-failed"
    assert expected
    with page.expect_response(lambda response: response.url.endswith(endpoint)) as received:
        command.click()
    assert received.value.ok, received.value.text()
    actual = received.value.json()["retried"]
    assert sorted(actual) == expected
    expect(page.get_by_text("已重试任务", exact=False)).to_be_visible()
    page.screenshot(path=output / f"edge-retry-{'single' if part else 'batch'}.png", full_page=True)
    print("Retried only", actual)


def locate(page, panel, task_id, frame, region, image_key, image_sha, output):
    expect(panel.get_by_role("img", name=f"原图第 {frame['number']} 页", exact=True)).to_be_visible()
    expect(panel.get_by_role("img", name=f"{region['view']}局部图", exact=True)).to_be_visible()
    expect(panel.get_by_text(f"原图第 {frame['number']} 页 · 旋转 {frame['rotation']}°", exact=True)).to_be_visible()
    sheet = panel.locator(".m1-sheet")
    sheet_box = sheet.bounding_box()
    overlay = panel.locator('[aria-label="证据高亮"]').bounding_box()
    left, top, width, height = region["pixel_rect"]
    expected = {"x": sheet_box["x"] + left / frame["width"] * sheet_box["width"],
                "y": sheet_box["y"] + top / frame["height"] * sheet_box["height"],
                "width": width / frame["width"] * sheet_box["width"], "height": height / frame["height"] * sheet_box["height"]}
    assert all(abs(overlay[key] - value) <= 1 for key, value in expected.items()), (overlay, expected)
    prefix = f"/tasks/{task_id}/m2/images"
    full = request(page, f"{prefix}/{frame['image_key']}").body()
    response = request(page, f"{prefix}/{image_key}")
    body = response.body()
    assert hashlib.sha256(body).hexdigest() == response.headers["x-content-sha256"] == image_sha
    full_image = Image.open(BytesIO(full)).convert("RGB")
    area = full_image.crop((left, top, left + width, top + height))
    crop = Image.open(BytesIO(body)).convert("RGB")
    assert area.size == crop.size
    delta = ImageChops.difference(area, crop).convert("L").histogram()
    changed = 1 - delta[0] / (width * height)
    average = sum(value * count for value, count in enumerate(delta)) / (width * height)
    assert changed < 0.005 and average < 0.5, (task_id, image_key, changed, average)
    sheet.scroll_into_view_if_needed()
    panel.locator(".m1-images").screenshot(path=output / f"edge-evidence-{task_id}-{image_key}.png")
    (output / f"crop-{task_id}-{image_key}.png").write_bytes(body)
    return {"task_id": task_id, "key": image_key, "rotation": frame["rotation"], "image_sha256": image_sha,
            "pdf_rect": region["pdf_rect"], "pixel_rect": region["pixel_rect"], "page": frame["number"],
            "fraction_of_pixels_changed": changed, "mean_rendering_difference": average}


def evidence(page, output, batch_id):
    select_batch(page, batch_id)
    result = request(page, f"/batches/{batch_id}").json()
    audit = []
    for name, rotation in ((SAMPLES[0].name, 0), (SAMPLES[1].name, 270), (SAMPLES[2].name, 0)):
        file = next(item for item in result["files"] if item["filename"] == name)
        panel = task_panel(page, file["task_id"], file_card(page, name), "查看详情")
        detail = request(page, f"/tasks/{file['task_id']}/m2").json()
        proof = detail["evidence"]
        multipage = name != SAMPLES[2].name
        assert [frame["rotation"] for frame in proof["pages"]] == [rotation] * (2 if multipage else 1)
        if multipage:
            for key in ("verified-model", "verified-package", "verified-dimension"):
                region = proof["regions"][key]
                candidate = panel.locator(".m2-candidate").filter(has=page.get_by_role("checkbox", name=f"选择 {file['products'][0]['part_number']}", exact=True))
                if key == "verified-dimension":
                    candidate.get_by_text("尺寸与视向证据", exact=True).click()
                candidate.get_by_role("button", name=region["view"], exact=True).click()
                audit.append(locate(page, panel, file["task_id"], proof["pages"][region["page"] - 1], region, key, proof["images"][key], output))
        else:
            for product, length in zip(sorted(file["products"], key=lambda item: item["part_number"]), ("24.70", "26.70"), strict=True):
                part = product["part_number"]
                child = request(page, f"/tasks/{product['task_id']}/m2").json()
                assert child["shared_read_sha256"] == detail["shared_read_sha256"]
                candidate = panel.locator(".m2-candidate").filter(has=page.get_by_role("checkbox", name=f"选择 {part}", exact=True))
                expect(candidate.get_by_text(f" · 总长 {length} mm", exact=False)).to_be_visible()
                candidate.get_by_text("尺寸与视向证据", exact=True).click()
                pins = child["specification"]["pin_count"]
                for key in (f"verified-variant-{pins}", f"verified-length-{pins}"):
                    region = proof["regions"][key]
                    control = candidate.get_by_role("button", name=region["view"], exact=True)
                    if key.startswith("verified-variant"):
                        control = candidate.locator(".m2-actions").get_by_role("button", name=region["view"], exact=True)
                    control.click()
                    audit.append(locate(page, panel, file["task_id"], proof["pages"][0], region, key, proof["images"][key], output))
    (output / "page-evidence.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Verified {len(audit)} overlays and crops against their displayed original pages")


def download_file(page, link, directory, metadata):
    with page.expect_download() as received:
        link.click()
    download = received.value
    assert download.failure() is None and download.suggested_filename == metadata["filename"]
    path = directory / download.suggested_filename
    download.save_as(path)
    assert path.stat().st_size == metadata["byte_count"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]


def legacy_history(page, output, baseline_path):
    project = request(page, "/projects").json()[0]["id"]
    baseline = json.loads(baseline_path.read_bytes())
    history = page.get_by_role("region", name="历史交付")
    history.get_by_label("批次号").fill("2")
    history.get_by_label("料号").fill("M12A04-03-301-M16")
    history.get_by_label("版本").fill("A0-M1.3")
    history.get_by_role("button", name="查询", exact=True).click()
    row = history.get_by_role("row").filter(has=page.get_by_role("cell", name="A0-M1.3 / G1", exact=True))
    expect(row).to_be_visible()
    expect(history.get_by_role("row")).to_have_count(2)
    query = f"/projects/{project}/versions?batch_id=2&part_number=M12A04-03-301-M16&version=A0-M1.3"
    versions = request(page, query).json()
    assert len(versions) == 1 and versions[0]["task_id"] == 2 and versions[0]["state"] == "published"
    version = versions[0]
    assert request(page, f"/versions/{version['id']}").json() == version
    for kind, label in (("html", "HTML"), ("step", "STEP"), ("report", "验收报告")):
        metadata = version["files"][kind]
        assert {key: metadata[key] for key in ("sha256", "byte_count", "filename")} == baseline[kind]
        download_file(page, row.get_by_role("link", name=f"下载 {label}", exact=True), output, metadata)
    history.screenshot(path=output / "edge-legacy-history.png")
    print("Migrated first delivery retained original HTML, STEP and report through the history page")


def deliveries(page, output, batch_id, label):
    select_batch(page, batch_id)
    project = request(page, "/projects").json()[0]["id"]
    base = f"/projects/{project}"
    saved = request(page, f"{base}/versions?batch_id={batch_id}").json()
    expected = {"M12A04-03-301-M16": label, "M12A05-03-301-M16": "published"}
    assert {item["part_number"]: item["state"] for item in saved} == expected
    history = page.get_by_role("region", name="历史交付")
    query = history.get_by_role("button", name="查询", exact=True)
    history.get_by_label("批次号").fill(str(batch_id))
    for item in saved:
        history.get_by_label("料号").fill(item["part_number"])
        history.get_by_label("版本").fill(item["version"])
        query.click()
        controlled = history.get_by_role("row").filter(has=page.get_by_role("cell", name=f"{item['version']} / G1", exact=True))
        expect(controlled).to_be_visible()
        expect(history.get_by_role("row")).to_have_count(2)
    history.get_by_label("料号").fill("")
    history.get_by_label("版本").fill("")
    query.click()
    expect(history.get_by_role("row")).to_have_count(len(saved) + 1)
    directory = output / label
    directory.mkdir(exist_ok=True)
    for item in saved:
        row = history.get_by_role("row").filter(has=page.get_by_role("cell", name=f"{item['version']} / G1", exact=True))
        kinds = {"html": "HTML", "step": "STEP", "report": "验收报告"}
        if item["state"] == "partial":
            expect(row.get_by_text("部分完成", exact=True)).to_be_visible()
            assert set(item["files"]) == {"html"}
            for kind in ("step", "report"):
                request(page, f"/artifacts/{item['id']}-{kind}/download", status=404)
        for kind, metadata in item["files"].items():
            name = f"下载 {kinds[kind]}" + ("（部分完成）" if item["state"] == "partial" else "")
            download_file(page, row.get_by_role("link", name=name, exact=True), directory, metadata)
        row.get_by_role("checkbox").check()
    with page.expect_download() as received:
        history.get_by_role("button", name="下载选中 ZIP").click()
    zip_path = directory / received.value.suggested_filename
    received.value.save_as(zip_path)
    expected_versions = {item["id"]: item for item in saved}
    with ZipFile(zip_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["project_id"] == project
        assert {item["id"] for item in manifest["versions"]} == set(expected_versions)
        names = {"manifest.json"}
        for version in manifest["versions"]:
            original = expected_versions[version["id"]]
            for key in ("part_number", "version", "state", "geometry_sha256"):
                assert version[key] == original[key]
            for kind, metadata in version["files"].items():
                path = metadata["path"]
                body = archive.read(path)
                assert body == (directory / original["files"][kind]["filename"]).read_bytes()
                assert len(body) == metadata["byte_count"] and hashlib.sha256(body).hexdigest() == metadata["sha256"]
                assert metadata["sha256"] == original["files"][kind]["sha256"]
                names.add(path)
        assert set(archive.namelist()) == names
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    history.screenshot(path=directory / "edge-history.png")
    evidence = {"versions": saved, "tasks": {str(item["task_id"]): {"detail": request(page, f"/tasks/{item['task_id']}/m2").json(),
                 "events": request(page, f"/tasks/{item['task_id']}/events").json()} for item in saved}}
    (directory / "before-after.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{label}: checked {len(names) - 1} individual downloads and ZIP entries, SHA-256 {hashlib.sha256(zip_path.read_bytes()).hexdigest()}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("upload", "derive", "variant", "candidates", "confirm", "retry", "evidence", "deliveries", "legacy"))
    parser.add_argument("--url", default="http://127.0.0.1:8085")
    parser.add_argument("--output", type=Path, default=Path("output/m2-step5"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--samples", nargs=3, type=Path, default=SAMPLES)
    parser.add_argument("--batch", type=int)
    parser.add_argument("--part")
    parser.add_argument("--template")
    parser.add_argument("--answer")
    parser.add_argument("--legacy-baseline", type=Path, default=Path("output/m1-step4-evidence/db-files.json"))
    parser.add_argument("--delivery-state", choices=("partial", "published"), default="published")
    args = parser.parse_args()
    if args.batch is None and args.mode not in ("upload", "legacy"):
        parser.error("--batch is required for this check")
    args.output.mkdir(parents=True, exist_ok=True)
    credentials = dict(line.split("=", 1) for line in args.env_file.read_text(encoding="utf-8-sig").splitlines()
                       if line.startswith(("BOOTSTRAP_LOGIN=", "BOOTSTRAP_PASSWORD=")))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = login(browser, args.url, credentials)
        print("Microsoft Edge", browser.version)
        if args.mode == "upload":
            upload(page, args.output, args.samples, args.batch)
        elif args.mode == "derive":
            derive(page, args.output, args.batch)
        elif args.mode == "variant":
            select_variant(page, args.output, args.batch, args.template, args.part)
        elif args.mode == "candidates":
            candidate_selection(page, args.output, args.batch)
        elif args.mode == "confirm":
            confirm_product(page, args.output, args.batch, args.part, args.answer)
        elif args.mode == "retry":
            retry(page, args.output, args.batch, args.part)
        elif args.mode == "evidence":
            evidence(page, args.output, args.batch)
        elif args.mode == "legacy":
            legacy_history(page, args.output, args.legacy_baseline)
        else:
            deliveries(page, args.output, args.batch, args.delivery_state)
        browser.close()


if __name__ == "__main__":
    main()
