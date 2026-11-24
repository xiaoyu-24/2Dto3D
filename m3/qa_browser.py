import argparse
import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m2.qa_browser import login, request, select_batch
from m3.qa_cad import database
from playwright.sync_api import expect, sync_playwright


def deliveries(page, output, ids, state):
    history = page.get_by_role("region", name="历史交付")
    if not history.count():
        history = page.locator('section[aria-label="历史交付"]')
    versions = [request(page, f"/versions/{version_id}").json() for version_id in ids]
    assert versions[0]["state"] == state
    if state == "partial":
        assert set(versions[0]["files"]) == {"html"}
    if state == "rejected":
        assert versions[0]["files"] == {}
    files = {}
    for version in versions:
        row = history.get_by_role("row").filter(has=page.get_by_text(f"{version['version']} / {version['geometry_revision']}", exact=True))
        expect(row).to_be_visible()
        for kind in ("html", "step", "report"):
            if kind not in version["files"]:
                request(page, f"/artifacts/{version['id']}-{kind}/download", status=404)
                continue
            metadata = version["files"][kind]
            label = {"html": "HTML", "step": "STEP", "report": "验收报告"}[kind]
            with page.expect_download() as download:
                row.get_by_role("link", name=f"下载 {label}" + ("（部分完成）" if version["state"] == "partial" else ""), exact=True).click()
            target = output / download.value.suggested_filename
            download.value.save_as(target)
            body = target.read_bytes()
            assert len(body) == metadata["byte_count"] and hashlib.sha256(body).hexdigest() == metadata["sha256"]
            files[metadata["filename"]] = body
        checkbox = row.get_by_role("checkbox")
        if version["files"]:
            checkbox.check()
        else:
            expect(checkbox).to_be_disabled()
    if files:
        with page.expect_download() as download:
            history.get_by_role("button", name="下载选中 ZIP").click()
        archive_path = output / "deliveries.zip"
        download.value.save_as(archive_path)
        with ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert {item["id"] for item in manifest["versions"]} == {item["id"] for item in versions if item["files"]}
            for item in manifest["versions"]:
                for metadata in item["files"].values():
                    body = archive.read(metadata["path"])
                    assert body == files[metadata["filename"]]
                    assert len(body) == metadata["byte_count"] and hashlib.sha256(body).hexdigest() == metadata["sha256"]
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    page.screenshot(path=output / "edge-deliveries.png", full_page=True)
    (output / "versions.json").write_text(json.dumps(versions, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Edge {state}: versions {ids}, {len(files)} downloaded files and ZIP bytes verified")


def permissions(browser, url, output):
    credentials = database("permission_account")
    context = browser.new_context(base_url=url)
    page = context.new_page()
    page.goto(url)
    page.get_by_label("账号").fill(credentials["login"])
    page.get_by_label("密码").fill(credentials["password"])
    page.get_by_role("button", name="登录", exact=True).click()
    expect(page.get_by_role("heading", name="图纸任务", exact=True)).to_be_visible()
    paths = ["/projects/1/versions", "/versions/4", "/artifacts/4-html/download",
             "/tasks/3/m2/images/verified-package", "/tasks/2/m1/delivery/html"]
    for path in paths:
        request(page, path, status=404)
    request(page, "/projects/1/zip", {"version_ids": [4]}, status=404)
    request(page, f"/projects/{credentials['project']}/zip", {"version_ids": [4]}, status=404)
    page.screenshot(path=output / "edge-permissions.png")
    (output / "permissions.json").write_text(json.dumps({"denied": paths, "cross_project_zip": 404, "mixed_zip": 404}), encoding="utf-8")
    print("Edge cross-project read, download, evidence and ZIP denied")
    context.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("partial", "rejected", "published", "permissions"))
    parser.add_argument("--versions", type=int, nargs="+", default=[4, 6])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    url = "http://127.0.0.1:8086"
    credentials = dict(line.split("=", 1) for line in Path(".env").read_text(encoding="utf-8-sig").splitlines()
                       if line.startswith(("BOOTSTRAP_LOGIN=", "BOOTSTRAP_PASSWORD=")))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        if args.mode == "permissions":
            permissions(browser, url, args.output)
        else:
            page = login(browser, url, credentials)
            select_batch(page, 4)
            deliveries(page, args.output, args.versions, args.mode)
        browser.close()


if __name__ == "__main__":
    main()
