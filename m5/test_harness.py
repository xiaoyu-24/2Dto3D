import importlib.util
import io
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


class HarnessConcurrency(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("m5_harness", ROOT / "m0/harness/m1_service.py")
        service = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"deepseek_harness": SimpleNamespace(DeepSeekHarness=None)}):
            spec.loader.exec_module(service)
        self.service = service

    def test_route_is_available_during_model_request(self):
        service = self.service
        entered = threading.Event()
        release = threading.Event()
        responses = []

        def request_harness(*arguments):
            entered.set()
            self.assertTrue(release.wait(5))
            return {"http_status": 200}

        def call():
            request = Request(base + "/read", data=json.dumps({"prompt": "test", "images": []}).encode(),
                              headers={"X-M1-Route": "qa-test-route"})
            with urlopen(request, timeout=6) as response:
                responses.append(json.load(response))

        with patch.object(service, "settings", return_value={"route_id": "qa-test-route"}), \
                patch.object(service, "request_harness", side_effect=request_harness):
            server = service.ThreadingHTTPServer(("127.0.0.1", 0), service.Service)
            base = f"http://127.0.0.1:{server.server_port}"
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            caller = threading.Thread(target=call)
            caller.start()
            try:
                self.assertTrue(entered.wait(2))
                started = time.monotonic()
                with urlopen(base + "/route", timeout=1) as response:
                    self.assertEqual(json.load(response), {"route": {"route_id": "qa-test-route"}})
                self.assertLess(time.monotonic() - started, 1)
            finally:
                release.set()
                caller.join(6)
                server.shutdown()
                server.server_close()
                thread.join(2)
            self.assertEqual(responses, [{"http_status": 200}])

    def test_disconnected_worker_leaves_correlated_receipt_without_payload(self):
        service = self.service
        handler = service.Service.__new__(service.Service)
        payload = json.dumps({"prompt": "private prompt", "images": ["private image"]}).encode()
        handler.path = "/read"
        handler.headers = {"Content-Length": str(len(payload)), "X-M1-Route": "qa-route", "X-Drawing-Call": "115:read:1"}
        handler.rfile = io.BytesIO(payload)
        reply = {"http_status": 200, "request_id": "qa-request", "usage": {"output_tokens": 4}, "response": "private answer"}
        with patch.object(service, "settings", return_value={"route_id": "qa-route"}), \
                patch.object(service, "request_harness", return_value=reply), \
                patch.object(handler, "respond", side_effect=BrokenPipeError), patch("sys.stdout", new_callable=io.StringIO) as output:
            with self.assertRaises(BrokenPipeError):
                handler.do_POST()
            self.assertNotIn("private", output.getvalue())
            receipt = json.loads(output.getvalue())
            self.assertEqual((receipt["ledger_key"], receipt["route_id"], receipt["request_id"], receipt["usage"]),
                             ("115:read:1", "qa-route", "qa-request", {"output_tokens": 4}))


if __name__ == "__main__":
    unittest.main()
