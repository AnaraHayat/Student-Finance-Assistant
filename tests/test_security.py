from datetime import date
from decimal import Decimal as D

import json
import pytest
from fastapi.testclient import TestClient

from agent.loop import Agent
from agent.mock import MockModel
from agent.tools import ToolBox, _d
from audit import append as append_audit
from audit import verify as verify_audit
from core.mandate import Mandate
from core.money import Route
from core.rates import Rate
from sandbox.ledger import Sandbox
from security import SessionSigner
from server import AppState, create_app

A = Route("A", D("0.02"), D("500"), D("0.01"), D("0"), "srcA", "dA")
RATE = Rate(D("40"), "asof", "next", "src", False)
M = Mandate(D("10000"), D("2000"), D("43"))


def make_client(tmp_path, **kw):
    sb = Sandbox(D("100000"), M, 1, tmp_path / "audit.jsonl")
    tb = ToolBox(sb, [A], RATE)
    agent = Agent(MockModel(), tb, today=date(2026, 10, 12))
    st = AppState(sb, tb, agent, RATE, [A], "mock")
    kw.setdefault("access_token", "")
    kw.setdefault("local_only", False)
    kw.setdefault("allowed_hosts", ["testserver"])
    return TestClient(create_app(st, **kw))


def auth_client(tmp_path, token="s3cret", **kw):
    sb = Sandbox(D("100000"), M, 1, tmp_path / "audit.jsonl")
    tb = ToolBox(sb, [A], RATE)
    agent = Agent(MockModel(), tb, today=date(2026, 10, 12))
    st = AppState(sb, tb, agent, RATE, [A], "mock")
    kw.setdefault("allowed_hosts", ["testserver"])
    return TestClient(create_app(st, access_token=token, local_only=False, **kw))


def login(client, token="s3cret"):
    return client.post("/api/login", json={"token": token})


# --- authentication ---------------------------------------------------------

def test_auth_is_off_when_no_token_is_configured(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/state").status_code == 200
    assert client.post("/api/login", json={"token": "anything"}).json()["auth"] is False


def test_api_requires_a_session_when_auth_is_enabled(tmp_path):
    client = auth_client(tmp_path)
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/tier", json={"tier": 2}).status_code == 401
    assert client.get("/").status_code == 200  # the shell is public


def test_login_rejects_the_wrong_token(tmp_path):
    client = auth_client(tmp_path)
    r = login(client, "wrong")
    assert r.status_code == 401
    assert client.get("/api/state").status_code == 401


def test_login_grants_a_session_and_sets_csrf_cookie(tmp_path):
    client = auth_client(tmp_path)
    r = login(client)
    assert r.status_code == 200
    csrf = r.json()["csrf"]
    assert csrf
    assert client.cookies.get("sfx_session")
    assert client.cookies.get("sfx_csrf") == csrf
    state = client.get("/api/state")
    assert state.status_code == 200
    assert state.json()["auth_required"] is True


def test_write_requests_need_the_csrf_header(tmp_path):
    client = auth_client(tmp_path)
    csrf = login(client).json()["csrf"]
    assert client.post("/api/tier", json={"tier": 2}).status_code == 403
    ok = client.post("/api/tier", json={"tier": 2},
                     headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200
    assert ok.json()["tier"] == 2


def test_logout_clears_the_session(tmp_path):
    client = auth_client(tmp_path)
    csrf = login(client).json()["csrf"]
    assert client.post("/api/logout",
                       headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/api/state").status_code == 401


def test_login_is_rate_limited(tmp_path):
    client = auth_client(tmp_path, login_rate_limit=2)
    assert login(client, "wrong").status_code == 401
    assert login(client, "wrong").status_code == 401
    assert login(client, "wrong").status_code == 429


def test_session_signature_and_expiry():
    signer = SessionSigner(b"k" * 32, ttl_seconds=60)
    payload = signer.issue()
    cookie = signer.encode(payload)
    assert signer.decode(cookie)["csrf"] == payload["csrf"]

    body, sig = cookie.rsplit(".", 1)
    assert signer.decode(body + "x." + sig) is None
    assert signer.decode(signer.encode(payload) + "tamper") is None

    expired = SessionSigner(b"k" * 32, ttl_seconds=-1)
    assert expired.decode(expired.encode(expired.issue())) is None


# --- Layer A controls -------------------------------------------------------

def test_csp_and_hardening_headers(tmp_path):
    r = make_client(tmp_path).get("/")
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers["Permissions-Policy"].startswith("geolocation=()")
    assert r.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert r.headers["X-XSS-Protection"] == "0"


def test_rejected_api_responses_are_hardened_and_uncached(tmp_path):
    client = auth_client(tmp_path)
    r = client.get("/api/state")
    assert r.status_code == 401
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"


def test_api_success_is_not_cached(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store"


def test_numeric_input_is_bounded():
    assert _d("123.45") == D("123.45")
    with pytest.raises(ValueError):
        _d("1e100")
    with pytest.raises(ValueError):
        _d("x" * 60)


def test_mutations_are_rate_limited(tmp_path):
    client = make_client(tmp_path, mutation_rate_limit=2,
                         mutation_rate_window=60)
    body = {"name": "get_rate", "args": {}}
    assert client.post("/api/tool", json=body).status_code == 200
    assert client.post("/api/tool", json=body).status_code == 200
    assert client.post("/api/tool", json=body).status_code == 429


def test_oversized_request_body_is_rejected(tmp_path):
    client = make_client(tmp_path, max_body_bytes=200)
    r = client.post("/api/chat", json={"message": "x" * 400})
    assert r.status_code == 413


def test_tool_arguments_are_size_capped(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/api/tool",
                    json={"name": "get_rate", "args": {"pad": "x" * 9000}})
    assert r.status_code == 413


def test_long_tool_name_is_rejected(tmp_path):
    client = make_client(tmp_path)
    r = client.post("/api/tool", json={"name": "x" * 70, "args": {}})
    assert r.status_code == 400


def test_untrusted_host_is_rejected(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/", headers={"host": "evil.example"}).status_code == 400


# --- tamper-evident audit log ----------------------------------------------

def test_audit_chain_happy_path(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_audit(path, {"event": "request"})
    append_audit(path, {"event": "confirm"})
    assert verify_audit(path) == (True, None)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_audit_chain_detects_edits(tmp_path):
    path = tmp_path / "audit.jsonl"
    append_audit(path, {"event": "request"})
    append_audit(path, {"event": "confirm"})
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"confirm"', '"rejected"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, bad = verify_audit(path)
    assert ok is False
    assert bad == 1


def test_audit_chain_tolerates_legacy_lines(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"event": "old", "ts": "2026-01-01"}\n', encoding="utf-8")
    append_audit(path, {"event": "new"})
    assert verify_audit(path) == (True, None)


def test_state_rechecks_audit_integrity(tmp_path):
    sb = Sandbox(D("100000"), M, 1, tmp_path / "audit.jsonl")
    tb = ToolBox(sb, [A], RATE)
    agent = Agent(MockModel(), tb, today=date(2026, 10, 12))
    st = AppState(sb, tb, agent, RATE, [A], "mock")
    client = TestClient(create_app(st, access_token="", local_only=False,
                                   allowed_hosts=["testserver"]))
    r = client.post("/api/tool", json={
        "name": "request_transfer",
        "args": {"route_name": "A", "cny_to_deliver": "1000"},
    })
    assert r.status_code == 200
    path = tmp_path / "audit.jsonl"
    assert path.exists()

    st.audit_checked_at = 0.0
    assert client.get("/api/state").json()["audit_ok"] is True

    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    entry["status"] = "executed"
    lines[0] = json.dumps(entry)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    st.audit_checked_at = 0.0
    assert client.get("/api/state").json()["audit_ok"] is False
