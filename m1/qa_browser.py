import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def file_info(path):
    return {"byte_count": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def offline(browser, html, output, part_count=8):
    context = browser.new_context(offline=True, viewport={"width": 1280, "height": 840})
    page = context.new_page()
    remote_requests, errors = [], []
    page.on("request", lambda request: remote_requests.append(request.url) if request.url.startswith(("http:", "https:")) else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(html.resolve().as_uri())
    expect(page.locator("#status")).to_have_text(f"{part_count} / {part_count} 个部件可见")
    assert urlparse(page.url).scheme == "file" and not page.evaluate("navigator.onLine")
    assert "Edg/" in page.evaluate("navigator.userAgent")

    def pixels():
        return page.locator("canvas").evaluate("""canvas => {
            const gl = canvas.getContext('webgl2');
            const pixels = new Uint8Array(canvas.width * canvas.height * 4);
            gl.readPixels(0, 0, canvas.width, canvas.height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
            let nonBackground = 0, fingerprint = 2166136261;
            for (let offset = 0; offset < pixels.length; offset += 4) {
                if (pixels[offset] !== 16 || pixels[offset + 1] !== 29 || pixels[offset + 2] !== 43) nonBackground++;
                fingerprint = Math.imul(fingerprint ^ pixels[offset], 16777619);
                fingerprint = Math.imul(fingerprint ^ pixels[offset + 1], 16777619);
                fingerprint = Math.imul(fingerprint ^ pixels[offset + 2], 16777619);
            }
            return {non_background_pixels: nonBackground, pixel_fingerprint: (fingerprint >>> 0).toString(16)};
        }""")

    def different(before, after):
        return before["pixel_fingerprint"] != after["pixel_fingerprint"]

    initial = pixels()
    assert initial["non_background_pixels"] > 10000
    page.screenshot(path=output / "edge-offline-iso.png")
    box = page.locator("canvas").bounding_box()
    center_x, center_y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(center_x, center_y)
    page.mouse.down()
    page.mouse.move(center_x + 123, center_y + 44, steps=8)
    page.mouse.up()
    rotated = pixels()
    assert different(initial, rotated)
    page.mouse.wheel(0, -170)
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    zoomed = pixels()
    assert different(rotated, zoomed) and zoomed["non_background_pixels"] > rotated["non_background_pixels"]
    page.get_by_role("button", name="缩小").click()
    zoomed_out = pixels()
    assert zoomed_out["non_background_pixels"] < zoomed["non_background_pixels"]

    page.get_by_role("button", name="配合端 +Z").click()
    expect(page.get_by_role("button", name="配合端 +Z")).to_have_attribute("aria-pressed", "true")
    mating = pixels()
    page.screenshot(path=output / "edge-offline-mating.png")
    page.get_by_role("button", name="焊线端 −Z").click()
    terminal = pixels()
    assert different(zoomed_out, mating) and different(mating, terminal)
    page.screenshot(path=output / "edge-offline-terminal.png")

    nut = page.get_by_role("checkbox", name="D · 独立螺母")
    nut.uncheck()
    expect(page.locator("#status")).to_have_text(f"{part_count - 1} / {part_count} 个部件可见")
    without_nut = pixels()
    assert without_nut["non_background_pixels"] < terminal["non_background_pixels"]
    pin = page.get_by_role("checkbox", name="B-UL · 触点 UL")
    pin.uncheck()
    expect(page.locator("#status")).to_have_text(f"{part_count - 2} / {part_count} 个部件可见")
    without_pin = pixels()
    assert different(without_nut, without_pin)
    pin.check()
    nut.check()
    assert pixels() == terminal
    assert not remote_requests and not errors
    result = {"passed": True, "channel": "msedge", "edge_version": browser.version, "scheme": "file:",
              "offline": True, "remote_requests": remote_requests, "page_errors": errors,
              "html_sha256": file_info(html)["sha256"], "geometry_sha256": page.locator("#geometry").text_content(),
              "operations": {"visible": initial, "drag_rotation": rotated, "wheel_zoom": zoomed, "button_zoom_out": zoomed_out,
                             "mating_view": mating, "terminal_view": terminal,
                             "hide_nut": without_nut, "hide_pin": without_pin, "restore_parts": terminal}}
    (output / "edge-offline.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Edge {browser.version}: file, offline, rotation, zoom, preset views and component visibility passed")
    context.close()


def download(browser, url, output, database, credentials_path):
    credentials = dict(line.split("=", 1) for line in credentials_path.read_text(encoding="utf-8-sig").splitlines()
                       if line.startswith(("BOOTSTRAP_LOGIN=", "BOOTSTRAP_PASSWORD=")))
    baseline = json.loads(database.read_bytes())
    context = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 900})
    page = context.new_page()
    route = "/api/tasks/2/m1/delivery"
    assert context.request.get(f"{url}{route}/step").status == 401
    page.goto(url)
    page.locator('input[name="login"]').fill(credentials["BOOTSTRAP_LOGIN"])
    page.locator('input[name="password"]').fill(credentials["BOOTSTRAP_PASSWORD"])
    page.get_by_role("button", name="登录", exact=True).click()
    batches = page.get_by_label("当前批次")
    expect(batches.locator('option[value="2"]')).to_be_attached()
    batches.select_option("2")
    page.get_by_role("button", name="查看详情").click()
    expect(page.get_by_role("heading", name="任务 #2")).to_be_visible()
    expect(page.locator(".m1-frozen").last.get_by_text(re.compile(r"已发布.*M1\.3.*G1"))).to_be_visible()
    assert "Edg/" in page.evaluate("navigator.userAgent")
    labels = {"html": "下载离线 3D HTML", "step": "下载 STEP", "report": "下载验收报告"}
    delivery = page.locator(".m1-frozen").last
    for kind, label in labels.items():
        with page.expect_download() as received:
            delivery.get_by_role("link", name=label, exact=True).click()
        response = received.value
        assert response.suggested_filename == baseline[kind]["filename"] and response.failure() is None
        path = output / response.suggested_filename
        response.save_as(path)
        assert file_info(path) == {field: baseline[kind][field] for field in ("byte_count", "sha256")}
        print(f"{kind}: {path.stat().st_size} bytes {file_info(path)['sha256']}")
    page.locator(".m1-frozen").last.scroll_into_view_if_needed()
    page.screenshot(path=output / "edge-published.png")


if __name__ == "__main__":
    mode, input_value, output_path, *extra = sys.argv[1:]
    output = Path(output_path)
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        args = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
        if mode == "offline":
            args.append("--host-resolver-rules=MAP * ~NOTFOUND")
        browser = playwright.chromium.launch(channel="msedge", headless=True, args=args)
        if mode == "offline":
            offline(browser, Path(input_value), output)
        else:
            download(browser, input_value, output, *map(Path, extra))
        browser.close()
