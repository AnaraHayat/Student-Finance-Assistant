from datetime import date
from decimal import Decimal as D

import pytest
import requests
from fastapi.testclient import TestClient

from agent.loop import Agent
from agent.mock import MockModel
from agent.tools import ToolBox
from core import rates
from core.mandate import Mandate
from core.money import Route
from core.rates import Rate
from sandbox.ledger import Sandbox
from server import AppState, create_app, is_local_host

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


def test_security_headers_on_responses(tmp_path):
    r = make_client(tmp_path).get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"


def test_chat_is_rate_limited(tmp_path):
    client = make_client(tmp_path, chat_rate_limit=2, chat_rate_window=60)
    assert client.post("/api/chat",
                       json={"message": "cheapest 1000"}).status_code == 200
    assert client.post("/api/chat",
                       json={"message": "cheapest 1000"}).status_code == 200
    blocked = client.post("/api/chat", json={"message": "cheapest 1000"})
    assert blocked.status_code == 429
    assert "wait" in blocked.json()["detail"].lower()


def test_first_run_offline_error_is_plain_language(tmp_path, monkeypatch):
    monkeypatch.setattr(rates, "CACHE", tmp_path / "missing.json")

    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(rates.requests, "get", offline)
    with pytest.raises(FileNotFoundError) as exc:
        rates.get_rate()
    assert "Connect to the internet" in str(exc.value)


def test_is_local_host():
    assert is_local_host("127.0.0.1")
    assert is_local_host("::1")
    assert is_local_host("localhost")
    assert not is_local_host("10.0.0.5")
    assert not is_local_host("testclient")  # the TestClient's non-loopback host


def test_local_only_is_off_by_default(tmp_path):
    client = make_client(tmp_path, local_only=False)
    assert client.get("/").status_code == 200
    assert client.get("/api/state").status_code == 200


def test_local_only_guard_blocks_non_local_clients(tmp_path):
    client = make_client(tmp_path, local_only=True)
    assert client.get("/").status_code == 403
    assert client.get("/api/state").status_code == 403
