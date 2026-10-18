import csv
import io
import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from monitor import Collector, Detector, Store
from server import Server, demo_config
from test_monitor import DEST, A, observation, settings


class APITests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.collector = Collector(demo_config(), self.store)
        self.server = Server(("127.0.0.1", 0), self.collector, demo=True)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.store.close()

    def get(self, path):
        return urllib.request.urlopen(self.url + path)

    def fill(self):
        detector = Detector(settings())
        for i in range(1, 5):
            self.store.record(DEST, observation([A], time.time() - 4 + i), detector)

    def test_health_starts_unavailable_then_healthy(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/healthz")
        self.assertEqual(caught.exception.code, 503)
        caught.exception.close()
        self.fill()
        self.assertTrue(json.load(self.get("/healthz"))["healthy"])
        self.collector.error = "failed to write database"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/healthz")
        self.assertEqual(caught.exception.code, 503)
        caught.exception.close()

    def test_filtered_json_and_csv(self):
        self.fill()
        result = json.load(self.get(f"/api/events?target={DEST}&limit=1"))
        self.assertEqual(len(result["items"]), 1)
        self.assertIsNotNone(result["next_before_id"])
        response = self.get(f"/api/events.csv?target={DEST}")
        rows = list(csv.DictReader(io.StringIO(response.read().decode())))
        self.assertEqual(rows[0]["kind"], "baseline")
        self.assertIn("hops", json.loads(rows[0]["after"]))
        self.assertTrue(json.load(self.get("/api/status"))["demo"])

    def test_validation_and_static_allowlist(self):
        for path in ("/api/events?limit=0", "/api/events?limit=1001", "/api/events?since=nan", "/api/events?until=inf", "/api/events?target=hello", "/api/events?limit=1&limit=2", "/api/events?before_id=-1", "/api/traces?foo=bar"):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code, 400)
            caught.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/../monitor.py")
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()
        response = self.get("/")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("Path monitor", response.read().decode())


if __name__ == "__main__":
    unittest.main()
