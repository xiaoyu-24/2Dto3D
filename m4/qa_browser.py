import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m2.qa_browser import confirm_product, file_card, request, task_panel, upload
from m3.qa_browser import deliveries


def session(browser, url, credentials):
    context = browser.new_context(base_url=url, ignore_https_errors=True, accept_downloads=True,
                                  viewport={"width": 1470, "height": 940})
    page = context.new_page()
    page.goto(url)
    page.get_by_label("账号").fill(credentials["BOOTSTRAP_LOGIN"])
    page.get_by_label("密码").fill(credentials["BOOTSTRAP_PASSWORD"])
    page.get_by_role("button", name="登录", exact=True).click()
    expect(page.get_by_role("heading", name="图纸任务", exact=True)).to_be_visible()
    assert "Edg/" in page.evaluate("navigator.userAgent")
    assert all(cookie["secure"] for cookie in context.cookies())
    return page


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("upload", "derive", "inspect", "confirm", "downloads", "permissions"))
    parser.add_argument("--url", required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--batch", type=int)
    parser.add_argument("--project", type=int, default=1)
    parser.add_argument("--versions", type=int, nargs="+")
    parser.add_argument("--answer")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    credentials = dict(line.split("=", 1) for line in args.env.read_text(encoding="utf-8-sig").splitlines()
                       if line.startswith(("BOOTSTRAP_LOGIN=", "BOOTSTRAP_PASSWORD=")))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = session(browser, args.url, credentials)
        if args.project != 1:
            selector = page.locator(".project-select select")
            expect(selector).to_be_enabled()
            selector.select_option(str(args.project))
        if args.action == "upload":
            upload(page, args.output, [args.pdf], None)
        elif args.action == "derive":
            page.get_by_label("当前批次").select_option(str(args.batch))
            data = request(page, f"/batches/{args.batch}").json()
            item = data["files"][0]
            card = file_card(page, item["filename"])
            expect(card.get_by_text("联合读图完成 · 可选型号", exact=True)).to_be_visible(timeout=180000)
            detail = task_panel(page, item["task_id"], card, "查看详情")
            detail.get_by_role("checkbox", name="选择 M12A04-03-301-M16", exact=True).check()
            detail.get_by_role("button", name="派生选中型号", exact=True).click()
            expect(detail.get_by_role("status")).to_contain_text("产品 #")
            page.screenshot(path=args.output / "edge-derived.png", full_page=True)
            (args.output / "derived.json").write_text(json.dumps(request(page, f"/batches/{args.batch}").json(),
                                                     ensure_ascii=False, indent=2), encoding="utf-8")
        elif args.action in ("inspect", "confirm"):
            if args.action == "confirm":
                if not args.answer:
                    raise ValueError("Supply the reviewed drawing answer")
                confirm_product(page, args.output, args.batch, "M12A04-03-301-M16", args.answer)
            data = request(page, f"/batches/{args.batch}").json()
            details = [request(page, f"/tasks/{product['task_id']}/m2").json()
                       for item in data["files"] for product in item["products"]]
            (args.output / "details.json").write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps([detail.get("confirmations") for detail in details], ensure_ascii=True))
        elif args.action == "downloads":
            deliveries(page, args.output, args.versions, "published")
            for version in args.versions:
                metadata = request(page, f"/versions/{version}").json()
                for kind, item in metadata["files"].items():
                    response = page.request.get(f"/api/artifacts/{version}-{kind}/download")
                    assert response.status == 200
                    assert int(response.headers["content-length"]) == item["byte_count"]
                    assert response.headers["x-content-sha256"] == item["sha256"]
        else:
            denied = ["/projects/1/versions", "/versions/4", "/artifacts/4-html/download",
                      "/tasks/2/m1/delivery/step", "/tasks/3/m2/images/verified-package"]
            for path in denied:
                request(page, path, status=404)
            request(page, "/projects/1/zip", {"version_ids": [4]}, status=404)
            request(page, f"/projects/{args.project}/zip", {"version_ids": [4]}, status=404)
            (args.output / "permissions.json").write_text(json.dumps({"denied": denied, "zip_status": 404}, indent=2))
            page.screenshot(path=args.output / "edge-permissions.png")
        record = {"edge_version": browser.version, "channel": "msedge", "new_browser_process": True,
                  "url": args.url, "secure_cookie": True, "qa_self_signed_certificate_exception": True,
                  "action": args.action, "unix_time": time.time()}
        (args.output / "browser.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        browser.close()


if __name__ == "__main__":
    main()
