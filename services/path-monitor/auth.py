"""One environment-configured login, expiring sessions, and a read-only API token."""

from collections import OrderedDict
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import os
import secrets
import threading
import time

ITERATIONS = 600_000
SESSION_SECONDS = 8 * 3600


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def password_matches(password, encoded):
    _, iterations, salt, expected = encoded.split("$")
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
    return hmac.compare_digest(actual, expected)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, username, password, api_token, secure_cookies=True, allow_any_origin=False):
        if not isinstance(username, str) or not username.strip() or len(username) > 100:
            raise ValueError("PATH_MONITOR_USERNAME must contain 1..100 characters")
        if not isinstance(password, str) or not password.strip() or len(password) > 1024:
            raise ValueError("PATH_MONITOR_PASSWORD must contain 1..1024 characters")
        if not isinstance(api_token, str) or not 32 <= len(api_token) <= 200 or any(not 33 <= ord(c) <= 126 for c in api_token):
            raise ValueError("PATH_MONITOR_API_TOKEN must contain 32..200 printable non-space characters")
        self.username = username
        self.password_digest = password_hash(password)
        self.token_digest = token_hash(api_token)
        self.secure = secure_cookies
        self.allow_any_origin = allow_any_origin
        prefix = "__Host-" if secure_cookies else ""
        self.session_cookie = prefix + "path_monitor_session"
        self.login_cookie = prefix + "path_monitor_login"
        self.secret = secrets.token_bytes(32)
        self.sessions = OrderedDict()
        self.attempts = OrderedDict()
        self.lock = threading.RLock()
        self.password_slots = threading.BoundedSemaphore(2)

    @classmethod
    def from_env(cls):
        secure = os.environ.get("PATH_MONITOR_COOKIE_SECURE", "true").lower()
        if secure not in {"true", "false"}:
            raise ValueError("PATH_MONITOR_COOKIE_SECURE must be true or false")
        allow_any_origin = os.environ.get("PATH_MONITOR_ALLOW_ANY_ORIGIN", "false").lower()
        if allow_any_origin not in {"true", "false"}:
            raise ValueError("PATH_MONITOR_ALLOW_ANY_ORIGIN must be true or false")
        return cls(os.environ.get("PATH_MONITOR_USERNAME", ""),
                   os.environ.get("PATH_MONITOR_PASSWORD", ""),
                   os.environ.get("PATH_MONITOR_API_TOKEN", ""), secure == "true", allow_any_origin == "true")

    def cookie(self, name, value, max_age):
        cookie = SimpleCookie()
        cookie[name] = value
        cookie[name]["path"] = "/"
        cookie[name]["httponly"] = True
        cookie[name]["samesite"] = "Strict"
        cookie[name]["max-age"] = max_age
        if self.secure:
            cookie[name]["secure"] = True
        return cookie.output(header="").strip()

    @staticmethod
    def cookies(header):
        cookie = SimpleCookie()
        try:
            cookie.load(header or "")
        except CookieError:
            return {}
        return {k: v.value for k, v in cookie.items()}

    def login_challenge(self):
        message = f"{int(time.time())}.{secrets.token_hex(24)}"
        signature = hmac.new(self.secret, message.encode(), "sha256").hexdigest()
        value = message + "." + signature
        return value, self.cookie(self.login_cookie, value, 600)

    def valid_challenge(self, token, cookie_header):
        cookie = self.cookies(cookie_header).get(self.login_cookie, "")
        if not token or not hmac.compare_digest(token.encode(), cookie.encode()):
            return False
        try:
            timestamp, nonce, signature = token.split(".")
            if not 0 <= time.time() - int(timestamp) <= 600:
                return False
        except (ValueError, OverflowError):
            return False
        expected = hmac.new(self.secret, f"{timestamp}.{nonce}".encode(), "sha256").hexdigest()
        return hmac.compare_digest(signature.encode(), expected.encode())

    def login(self, username, password, client):
        """Return a new session token, or a generic failure; bound expensive work."""
        now = time.time()
        with self.lock:
            attempts = [t for t in self.attempts.get(client, []) if now - t < 600]
            if len(attempts) >= 10:
                return None, 429
            self.attempts[client] = attempts + [now]
            self.attempts.move_to_end(client)
            while len(self.attempts) > 1024:
                self.attempts.popitem(last=False)
        if not self.password_slots.acquire(blocking=False):
            return None, 429
        try:
            valid = password_matches(password, self.password_digest)
        finally:
            self.password_slots.release()
        if not valid or not hmac.compare_digest(username.encode(), self.username.encode()):
            return None, 401
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.attempts.pop(client, None)
            self._prune_sessions(now)
            while len(self.sessions) >= 1000:
                self.sessions.popitem(last=False)
            self.sessions[token_hash(token)] = {"username": username, "expires": now + SESSION_SECONDS,
                                                "csrf": secrets.token_urlsafe(32)}
        return token, 200

    def _prune_sessions(self, now):
        for key, session in list(self.sessions.items()):
            if session["expires"] <= now:
                del self.sessions[key]

    def session(self, cookie_header):
        value = self.cookies(cookie_header).get(self.session_cookie)
        if not value or len(value) > 256:
            return None
        with self.lock:
            self._prune_sessions(time.time())
            return self.sessions.get(token_hash(value))

    def logout(self, cookie_header):
        value = self.cookies(cookie_header).get(self.session_cookie, "")
        with self.lock:
            self.sessions.pop(token_hash(value), None)

    def api_key(self, authorization):
        if not authorization or len(authorization) > 256:
            return None
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        digest = token_hash(parts[1])
        return hmac.compare_digest(digest, self.token_digest)
