import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROUTES = [{"route_id": value * 32, "model": "qa-simulated", "endpoint": f"http://simulator/{name}", "name": name}
          for value, name in (("a1", "primary"), ("b2", "backup"))]
LOCK = threading.Lock()
ROLE = os.environ.get("M3_SIM_ROLE", "primary")
STATE = {"sequence": {}, "calls": [], "discoveries": [], "reply": "m12-a04-m16-a0"}


class Service(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, data, status=200, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path == "/route":
            with LOCK:
                delay = STATE.get("route_delay", 0)
                status = STATE.get("route_status", 200)
                available = STATE.get("route_available", True)
                single = STATE.get("single_route", ROLE == "backup")
                STATE["discoveries"].append({"time": time.time(), "status": status, "available": available})
            time.sleep(delay)
        if self.path == "/routes":
            self.respond({"routes": ROUTES})
        elif self.path == "/route":
            route = ROUTES[1] if ROLE == "backup" else {**ROUTES[0], "alternates": [] if single else ROUTES[1:]}
            self.respond({"route": route if available else None}, status)
        elif self.path == "/stats":
            with LOCK:
                self.respond({"calls": STATE["calls"], "discoveries": STATE["discoveries"]})
        else:
            self.respond({}, 404)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        if self.path == "/configure":
            with LOCK:
                STATE.update(sequence=payload.get("sequence", {}), calls=[], discoveries=[], reply=payload.get("reply", "m12-a04-m16-a0"),
                             route_delay=payload.get("route_delay", 0), route_status=payload.get("route_status", 200),
                             route_available=payload.get("route_available", True), single_route=payload.get("single_route", ROLE == "backup"))
            self.respond({"configured": True})
            return
        route = next((item for item in ROUTES if item["route_id"] == self.headers.get("X-M1-Route")), None)
        if not route or STATE.get("single_route", ROLE == "backup") and route["name"] != ROLE:
            self.respond({}, 403)
            return
        key = f"{route['name']}:{self.path[1:]}"
        with LOCK:
            sequence = STATE["sequence"].get(key, [])
            result = sequence.pop(0) if sequence else {"status": 200}
            record = {"route": route["name"], "phase": self.path[1:], "time": time.time(),
                      "status": result.get("status", 200), "payload": payload}
            STATE["calls"].append(record)
            number = len(STATE["calls"])
            reply = STATE["reply"]
        time.sleep(result.get("delay", 0))
        if result.get("disconnect"):
            self.close_connection = True
            return
        status = result.get("status", 200)
        audit = {"http_status": status, "request_id": f"qa-{number}", "actual_model": "qa-simulated",
                 "elapsed_ms": int(result.get("delay", 0) * 1000), "usage": {}}
        if status == 200:
            audit.update(passed=result.get("passed", True), finish_reason="completed", response=reply,
                         usage={"input_tokens": 20, "output_tokens": 10})
        headers = {"x-request-id": audit["request_id"]}
        if "retry_after" in result:
            headers["Retry-After"] = result["retry_after"]
        self.respond(audit, status, headers)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8011), Service).serve_forever()
