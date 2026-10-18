import http.client
import json
from pathlib import Path
import re
import subprocess
import sys
import os
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from auth import Auth, SESSION_SECONDS, password_hash, password_matches
from monitor import Collector, Store
from server import Server, demo_config

PASSWORD = "test-password-for-panel-123"
TOKEN = "test-integration-key-only-not-a-real-secret"
ENCODED = password_hash(PASSWORD)


def auth_instance(secure=True):
    return Auth("reader", PASSWORD, TOKEN, secure_cookies=secure)


class AuthTests(unittest.TestCase):
    def test_salted_passwords_and_constant_format(self):
        second = password_hash(PASSWORD)
        self.assertNotEqual(ENCODED, second)
        self.assertTrue(password_matches(PASSWORD, second))
        self.assertFalse(password_matches("wrong", second))

    def test_bearer_token_and_rotation(self):
        auth = auth_instance()
        self.assertTrue(auth.api_key("Bearer " + TOKEN))
        self.assertTrue(auth.api_key("bearer " + TOKEN))
        for header in (None, "Basic " + TOKEN, "Bearer invalid", "Bearer x y", "Bearer " + "x" * 500):
            self.assertFalse(auth.api_key(header))
        rotated = Auth("reader", PASSWORD, TOKEN + "-rotated")
        self.assertFalse(rotated.api_key("Bearer " + TOKEN))

    def test_expiration_logout_and_secure_cookie_flags(self):
        auth = auth_instance()
        token, status = auth.login("reader", PASSWORD, "test")
        self.assertEqual(status, 200)
        cookie = auth.cookie(auth.session_cookie, token, SESSION_SECONDS)
        for expected in ("__Host-", "Secure", "HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(expected, cookie)
        header = f"{auth.session_cookie}={token}"
        self.assertEqual(auth.session(header)["username"], "reader")
        with patch("auth.time.time", return_value=time.time() + SESSION_SECONDS + 1):
            self.assertIsNone(auth.session(header))
        token, _ = auth.login("reader", PASSWORD, "test")
        header = f"{auth.session_cookie}={token}"
        auth.logout(header)
        self.assertIsNone(auth.session(header))

    def test_login_challenge_is_signed_cookie_bound_and_expires(self):
        auth = auth_instance()
        token, _ = auth.login_challenge()
        header = f"{auth.login_cookie}={token}"
        self.assertTrue(auth.valid_challenge(token, header))
        self.assertFalse(auth.valid_challenge(token, ""))
        self.assertFalse(auth.valid_challenge(token + "x", header))
        self.assertFalse(auth.valid_challenge("☃", f"{auth.login_cookie}=x"))
        with patch("auth.time.time", return_value=time.time() + 601):
            self.assertFalse(auth.valid_challenge(token, header))

    def test_login_throttle_and_unknown_user(self):
        auth = auth_instance()
        self.assertEqual(auth.login("unknown", PASSWORD, "test"), (None, 401))
        auth.attempts["test"] = [time.time()] * 10
        self.assertEqual(auth.login("reader", PASSWORD, "test"), (None, 429))
        with patch("auth.time.time", return_value=time.time() + 601):
            self.assertIsNotNone(auth.login("reader", PASSWORD, "test")[0])

    def test_reads_single_login_and_token_from_environment(self):
        env = {"PATH_MONITOR_USERNAME": "reader", "PATH_MONITOR_PASSWORD": PASSWORD,
               "PATH_MONITOR_API_TOKEN": TOKEN, "PATH_MONITOR_COOKIE_SECURE": "false"}
        with patch.dict(os.environ, env, clear=True):
            auth = Auth.from_env()
        self.assertFalse(auth.secure)
        self.assertTrue(auth.api_key("Bearer " + TOKEN))
        self.assertIsNotNone(auth.login("reader", PASSWORD, "test")[0])
        for key in ("PATH_MONITOR_USERNAME", "PATH_MONITOR_PASSWORD", "PATH_MONITOR_API_TOKEN"):
            with self.subTest(key=key), patch.dict(os.environ, env | {key: ""}, clear=True), self.assertRaises(ValueError):
                Auth.from_env()
        with patch.dict(os.environ, env | {"PATH_MONITOR_COOKIE_SECURE": "invalid"}, clear=True), self.assertRaises(ValueError):
            Auth.from_env()

    def test_service_fails_closed_without_credentials_including_demo(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("PATH_MONITOR_")}
        result = subprocess.run([sys.executable, "server.py", "--demo"], capture_output=True, text=True, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PATH_MONITOR_USERNAME", result.stderr)


class HTTPAuthTests(unittest.TestCase):
    def setUp(self):
        self.auth = auth_instance(secure=False)
        self.store = Store(":memory:")
        self.collector = Collector(demo_config(), self.store)
        self.server = Server(("127.0.0.1", 0), self.collector, self.auth, demo=True)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.store.close()

    def request(self, path, method="GET", fields=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = dict(headers or {})
        if fields is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        connection.request(method, path, urlencode(fields) if fields is not None else None, headers)
        response = connection.getresponse()
        result = response.status, response.getheaders(), response.read().decode()
        connection.close()
        return result

    def challenge(self):
        status, headers, body = self.request("/login")
        self.assertEqual(status, 200)
        cookie = next(v.split(";", 1)[0] for k, v in headers if k.lower() == "set-cookie")
        csrf = re.search(r'name="csrf" value="([^"]+)"', body)[1]
        return cookie, csrf

    def login(self):
        cookie, csrf = self.challenge()
        status, headers, _ = self.request("/login", "POST", {"username": "reader", "password": PASSWORD, "csrf": csrf},
                                          {"Cookie": cookie, "Origin": f"http://127.0.0.1:{self.server.server_port}"})
        self.assertEqual(status, 303)
        return next(v.split(";", 1)[0] for k, v in headers if k.lower() == "set-cookie" and self.auth.session_cookie in v)

    def test_anonymous_data_and_web_are_blocked_health_is_minimal(self):
        for path in ("/api/status", "/api/traces", "/api/events", "/api/events.csv", "/api/openapi.json"):
            status, headers, body = self.request(path)
            self.assertEqual(status, 401)
            self.assertIn("WWW-Authenticate", dict(headers))
            self.assertNotIn("192.0.2.80", body)
        for path in ("/", "/app.js"):
            status, headers, body = self.request(path)
            self.assertEqual(status, 303)
            self.assertEqual(dict(headers)["Location"], "login")
            self.assertNotIn("Destinations", body)
        status, _, body = self.request("/healthz")
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body), {"healthy": False})

    def test_bearer_reads_json_and_csv_but_query_tokens_do_not_authenticate(self):
        for path in ("/api/status", "/api/events", "/api/traces", "/api/events.csv", "/api/openapi.json"):
            self.assertEqual(self.request(path, headers={"Authorization": "Bearer " + TOKEN})[0], 200)
            self.assertEqual(self.request(path, headers={"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.request("/api/events?token=" + TOKEN)[0], 401)
        _, _, body = self.request("/api/openapi.json", headers={"Authorization": "Bearer " + TOKEN})
        document = json.loads(body)
        self.assertEqual(document["security"], [{"bearerAuth": []}])
        self.assertEqual(document["paths"]["/healthz"]["get"]["security"], [])

    def test_browser_session_reads_panel_data_and_export_then_logs_out(self):
        cookie = self.login()
        status, _, body = self.request("/", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIn("Sign out", body)
        self.assertNotIn("{{CSRF}}", body)
        csrf = re.search(r'name="csrf" value="([^"]+)"', body)[1]
        for path in ("/app.js", "/api/status", "/api/events.csv"):
            self.assertEqual(self.request(path, headers={"Cookie": cookie})[0], 200)
        self.assertEqual(self.request("/logout", "POST", {"csrf": "bad"}, {"Cookie": cookie})[0], 403)
        self.assertEqual(self.request("/logout", "POST", {"csrf": csrf}, {"Cookie": cookie, "Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("/logout", "POST", {"csrf": csrf}, {"Cookie": cookie})[0], 303)
        self.assertEqual(self.request("/api/status", headers={"Cookie": cookie})[0], 401)

    def test_login_rejects_csrf_wrong_password_and_cross_origin(self):
        cookie, csrf = self.challenge()
        fields = {"username": "reader", "password": PASSWORD, "csrf": csrf}
        self.assertEqual(self.request("/login", "POST", fields)[0], 403)
        self.assertEqual(self.request("/login", "POST", fields, {"Cookie": cookie, "Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.request("/login", "POST", fields, {"Cookie": cookie, "Origin": "null"})[0], 403)
        fields["password"] = "wrong"
        self.assertEqual(self.request("/login", "POST", fields, {"Cookie": cookie})[0], 401)

    def test_favicon_and_other_login_tabs_do_not_invalidate_form_challenge(self):
        cookie, csrf = self.challenge()
        status, headers, _ = self.request("/favicon.ico", headers={"Cookie": cookie})
        self.assertEqual(status, 404)
        self.assertNotIn("Set-Cookie", dict(headers))
        status, _, body = self.request("/login", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(re.search(r'name="csrf" value="([^"]+)"', body)[1], csrf)
        self.assertEqual(self.request("/login", "POST", {"username": "reader", "password": PASSWORD, "csrf": csrf}, {"Cookie": cookie})[0], 303)

    def test_invalid_bearer_cannot_fall_back_to_valid_cookie_and_expired_sessions_fail(self):
        cookie = self.login()
        self.assertEqual(self.request("/api/status", headers={"Cookie": cookie, "Authorization": "Bearer wrong"})[0], 401)
        for session in self.auth.sessions.values():
            session["expires"] = time.time() - 1
        self.assertEqual(self.request("/api/status", headers={"Cookie": cookie})[0], 401)


if __name__ == "__main__":
    unittest.main()
