import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.m1_read import harness


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-timeout", action="store_true")
    args = parser.parse_args()
    requests = []

    class SlowResponse(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(self.headers.get("X-Drawing-Call"))
            time.sleep(130)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"http_status":200,"request_id":"qa-late-receipt"}')

    with ThreadingHTTPServer(("127.0.0.1", 0), SlowResponse) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        route = {"harness_url": f"http://127.0.0.1:{server.server_port}", "route_id": "a1" * 32, "ledger_key": "1:read:1"}
        started = time.monotonic()
        try:
            response = harness("/read", route, {"images": []})
        except TimeoutError:
            response = {"error": "timeout"}
        finally:
            server.shutdown()
            thread.join()
    print(json.dumps({"elapsed_seconds": time.monotonic() - started, "call_keys": requests, "result": response}), flush=True)
    assert len(requests) == 1
    if args.expect_timeout:
        assert response == {"error": "timeout"}
    else:
        assert requests == ["1:read:1"] and response.get("request_id") == "qa-late-receipt"


if __name__ == "__main__":
    main()
