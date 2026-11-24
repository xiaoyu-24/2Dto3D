import hashlib
import json

from playwright.sync_api import expect


def verify(browser, folder):
    page = browser.new_page(offline=True, viewport={"width": 1280, "height": 840})
    requests, errors = [], []
    page.on("request", lambda request: requests.append(request.url) if request.url.startswith(("http:", "https:")) else None)
    page.on("pageerror", lambda error: errors.append(str(error)))
    path = folder / "model.html"
    page.goto(path.resolve().as_uri())
    expect(page.locator("#status")).to_have_text("1 / 1 个部件可见")
    assert "Edg/" in page.evaluate("navigator.userAgent") and not page.evaluate("navigator.onLine")
    canvas = page.locator("canvas")
    def pixels():
        return canvas.evaluate("""canvas => {
            const gl = canvas.getContext('webgl2'), bytes = new Uint8Array(canvas.width * canvas.height * 4);
            gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,bytes);
            let count = 0, hash = 0;
            for(let offset=0;offset<bytes.length;offset+=4) {
                if(bytes[offset]!=16 || bytes[offset+1]!=29 || bytes[offset+2]!=43) count++;
                hash = (Math.imul(hash,31)+bytes[offset])|0;
            }
            return {count,hash};
        }""")
    before = pixels()
    assert before["count"] > 10000
    box = canvas.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + 90, box["y"] + box["height"] / 2 + 40, steps=5)
    page.mouse.up()
    assert pixels() != before
    page.get_by_role("button", name="放大", exact=True).click()
    page.get_by_role("button", name="配合端 +Z", exact=True).click()
    visible = pixels()
    page.get_by_role("checkbox").uncheck()
    assert pixels()["count"] == 0
    page.get_by_role("checkbox").check()
    assert pixels() == visible and not requests and not errors
    page.screenshot(path=folder / "edge-plate.png")
    check = {"passed": True, "channel": "msedge", "edge_version": browser.version, "scheme": "file:", "offline": True,
             "remote_requests": requests, "page_errors": errors, "html_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
             "geometry_sha256": page.locator("#geometry").text_content(), "operations": {"visible": visible, "initial": before}}
    (folder / "edge-offline.json").write_text(json.dumps(check, indent=2), encoding="utf-8")
    page.close()
    return check
