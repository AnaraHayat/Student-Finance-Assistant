"""Security helpers: signed sessions, CSRF tokens, security-event logging
and best-effort file permission tightening.

Standard library only, so no new runtime dependency is added.

Design:
- A session is a stateless, HMAC-SHA256 signed cookie. It carries an issue
  time, a last-seen time and a CSRF token. The signing key is random per
  process, so a restart invalidates every session.
- CSRF uses the double-submit pattern: the CSRF token lives in a readable
  cookie and must be echoed in the ``X-CSRF-Token`` header on every
  state-changing request.
- Expiry is a sliding idle timeout plus an absolute maximum age.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

COOKIE_SESSION = "sfx_session"
COOKIE_CSRF = "sfx_csrf"

_LOG_PATH = Path(__file__).resolve().parent / "logs" / "security.jsonl"
_log_lock = threading.Lock()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


class SessionSigner:
    def __init__(self, secret: bytes, ttl_seconds: int,
                 absolute_seconds: int | None = None):
        self.secret = secret
        self.ttl = int(ttl_seconds)
        self.absolute = int(absolute_seconds if absolute_seconds is not None
                             else max(self.ttl, 1) * 8)

    def issue(self) -> dict:
        now = int(time.time())
        return {"v": 1, "iat": now, "last": now,
                "csrf": secrets.token_urlsafe(32)}

    def encode(self, payload: dict) -> str:
        raw = json.dumps(payload, separators=(",", ":"),
                         sort_keys=True).encode("utf-8")
        body = _b64e(raw)
        sig = _b64e(hmac.new(self.secret, body.encode("ascii"),
                             hashlib.sha256).digest())
        return body + "." + sig

    def decode(self, cookie: str | None) -> dict | None:
        if not cookie or "." not in cookie:
            return None
        body, sig = cookie.rsplit(".", 1)
        expected = _b64e(hmac.new(self.secret, body.encode("ascii"),
                                  hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            payload = json.loads(_b64d(body))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("v") != 1:
            return None
        try:
            last = int(payload.get("last", 0))
            iat = int(payload.get("iat", 0))
        except (TypeError, ValueError):
            return None
        now = int(time.time())
        if now - last > self.ttl:
            return None
        if now - iat > self.absolute:
            return None
        return payload

    def touch(self, payload: dict) -> dict:
        out = dict(payload)
        out["last"] = int(time.time())
        return out


def token_matches(given: str, expected: str) -> bool:
    """Constant-time comparison, safe on empty strings."""
    return hmac.compare_digest(given or "", expected or "")


def restrict_file(path) -> None:
    """Best effort: owner-only permissions on POSIX. Windows needs ACLs,
    which are managed by the OS, so this is a no-op there."""
    try:
        if os.name == "posix":
            os.chmod(path, 0o600)
    except OSError:
        pass


def log_security(event: str, host: str = "", detail: str = "") -> None:
    """Record a security event to logs/security.jsonl and the terminal.
    Never raises: logging must not break the request."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "host": host or "-",
        "detail": (detail or "")[:200],
    }
    try:
        with _log_lock:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            restrict_file(_LOG_PATH)
    except OSError:
        pass
    print(f"[security] {event} host={entry['host']} {entry['detail']}".rstrip(),
          flush=True)
