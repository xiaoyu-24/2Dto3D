import base64
import hashlib
import http.client
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from tempfile import TemporaryDirectory
from threading import Thread

from deepseek_harness import DeepSeekHarness

ENDPOINT = "https://api.deepseek.com/anthropic"
MODEL = "deepseek-flash"
TOKEN_LIMITS = {"probe": 256, "read": 4096, "cad_select": 512}


def settings():
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if (not key or os.environ.get("DEEPSEEK_BASE_URL", ENDPOINT).rstrip("/") != ENDPOINT
            or os.environ.get("DEEPSEEK_MODEL", MODEL) != MODEL):
        return None
    return {"model": MODEL, "endpoint": ENDPOINT, "route_id": hashlib.sha256(
        f"m1-read-v1:{ENDPOINT}:{MODEL}:{key}".encode()).hexdigest()}


def request_harness(prompt, images, phase):
    key = os.environ["DEEPSEEK_API_KEY"].strip()
    limit = TOKEN_LIMITS[phase]
    audit = {"request_id": None, "response_id": None, "actual_model": None, "usage": {}, "http_status": None}
    started = time.monotonic()

    class Messages(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/v1/messages":
                self.send_error(404)
                return
            if audit.get("sent"):
                self.send_error(409)
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            payload = {field: payload[field] for field in ("messages", "system") if field in payload}
            payload.update(model=MODEL, max_tokens=limit, stream=True, thinking={"type": "disabled"})
            connection = http.client.HTTPSConnection("api.deepseek.com", timeout=75)
            audit["sent"] = True
            try:
                connection.request("POST", "/anthropic/v1/messages", json.dumps(payload).encode(), {
                    "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                })
                response = connection.getresponse()
                audit["http_status"] = response.status
                audit["request_id"] = next((response.getheader(header) for header in
                    ("request-id", "x-request-id", "x-ds-request-id", "ds-trace-id", "x-ds-trace-id")
                    if response.getheader(header)), None)
                content = response.read(2_000_000)
                if response.status == 200:
                    for line in content.splitlines():
                        if line.startswith(b"data: {"):
                            event = json.loads(line[6:])
                            if event.get("type") == "message_start":
                                message = event["message"]
                                audit.update(response_id=message.get("id"), actual_model=message.get("model"))
                                audit["usage"].update(message.get("usage", {}))
                            elif event.get("type") == "message_delta":
                                audit["usage"].update(event.get("usage", {}))
                self.send_response(response.status)
                self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
                self.end_headers()
                self.wfile.write(content)
            except (OSError, ValueError, http.client.HTTPException):
                self.close_connection = True
            finally:
                connection.close()

    result = None
    error = None
    session = f"m1-{phase}-{secrets.token_hex(8)}"
    with HTTPServer(("127.0.0.1", 0), Messages) as relay, TemporaryDirectory(dir="/tmp") as work, \
            TemporaryDirectory(dir="/run/dsh-native") as home:
        thread = Thread(target=relay.serve_forever, daemon=True)
        thread.start()
        try:
            with DeepSeekHarness(provider="deepseek-official", model=MODEL, api_key="local-relay",
                                 base_url=f"http://127.0.0.1:{relay.server_port}", profile="sdk-minimal",
                                 patches=("/opt/probe/profile.patch.yml",), cwd=work, dsh_home=home,
                                 request_timeout_seconds=95, max_tokens=limit) as harness:
                result = harness.run([{"type": "text", "text": prompt}, *[
                    {"type": "image", "data": image, "mimeType": "image/png"} for image in images
                ]], session_id=session)
        except Exception as failure:
            error = type(failure).__name__
        finally:
            relay.shutdown()
            thread.join()
    return {**audit, "session_id": session, "elapsed_ms": round((time.monotonic() - started) * 1000),
            "finish_reason": result.finish_reason if result else None, "error_type": error,
            "response": result.final_response if result else None}


def run_probe():
    from vision_probe import build_challenge, parse_answer, validate_png

    image, expected = build_challenge()
    validate_png(image)
    prompt = ("The picture has three panels. Count circles, squares and triangles on the left, read the three digits "
              "in the middle, and identify the arrow direction on the right. Return only a JSON object with the keys "
              "circle, square, triangle (integer counts), code (three-character string), and direction "
              "(up, down, left or right).")
    result = request_harness(prompt, [base64.b64encode(image).decode("ascii")], "probe")
    try:
        answer = parse_answer(result.pop("response"))
    except (ValueError, KeyError, TypeError):
        answer = None
    return {**result, "passed": result["finish_reason"] == "completed" and answer == expected,
            "expected": expected, "answer": answer, "image_sha256": hashlib.sha256(image).hexdigest()}


class Service(BaseHTTPRequestHandler):
    def respond(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_GET(self):
        self.respond({"route": settings()} if self.path == "/route" else {}, 200 if self.path == "/route" else 404)

    def do_POST(self):
        route = settings()
        if not route or self.headers.get("X-M1-Route") != route["route_id"]:
            self.respond({}, 403)
            return
        if self.path == "/probe":
            self.respond(run_probe())
        elif self.path in ("/read", "/cad_select"):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.respond(request_harness(request["prompt"], request["images"], self.path[1:]))
        else:
            self.respond({}, 404)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8011), Service).serve_forever()
