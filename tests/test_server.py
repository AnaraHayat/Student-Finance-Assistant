from datetime import date
from decimal import Decimal as D

from fastapi.testclient import TestClient

import threading

from agent.llm import LLMError
from agent.loop import Agent
from agent.mock import MockModel
from agent.tools import ToolBox
from core.mandate import Mandate
from core.money import Route
from core.rates import Rate
from sandbox.ledger import Sandbox
from server import AppState, create_app, is_illustrative

A = Route("A", D("0.02"), D("500"), D("0.01"), D("0"), "srcA", "dA")
B = Route("B", D("0.01"), D("0"), D("0"), D("0"), "srcB", "dB",
          route_type="bank_transfer", typical_days=3)
RATE = Rate(D("40"), "asof", "next", "src", False)
M = Mandate(D("10000"), D("2000"), D("43"))


class FailingModel:
    def generate(self, contents, tools, system):
        raise LLMError("down")


class BrokenModel:
    def generate(self, contents, tools, system):
        raise RuntimeError("boom")


class ProbeModel:
    """While the 'AI call' runs, checks from another thread whether the
    shared state lock is free (it must be, or the page freezes)."""

    def __init__(self):
        self.state = None
        self.got = None

    def generate(self, contents, tools, system):
        def probe():
            ok = self.state.lock.acquire(timeout=1)
            if ok:
                self.state.lock.release()
            self.got = ok

        t = threading.Thread(target=probe)
        t.start()
        t.join(3)
        return [{"text": "ok"}]


def make_client(tmp_path, tier=1, model=None):
    routes = [A, B]
    sb = Sandbox(D("100000"), M, tier, tmp_path / "audit.jsonl")
    tb = ToolBox(sb, routes, RATE)
    agent = Agent(model or MockModel(), tb, today=date(2026, 10, 12))
    st = AppState(sb, tb, agent, RATE, routes, "mock")
    return TestClient(create_app(st)), st


def request_b(client, amount=1000):
    return client.post("/api/tool", json={
        "name": "request_transfer",
        "args": {"route_name": "B", "cny_to_deliver": amount}}).json()


def test_index_served(tmp_path):
    client, _ = make_client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert "<html" in r.text.lower()


def test_state_snapshot(tmp_path):
    client, st = make_client(tmp_path)
    j = client.get("/api/state").json()
    assert j["rate"]["pkr_per_cny"] == "40"
    assert j["rate"]["source"] == "src"
    assert j["today"] == date.today().isoformat()
    assert j["balances"] == {"pkr": "100000", "cny": "0"}
    assert j["tier"] == 1
    assert j["mandate"]["max_transfer_cny"] == "10000"
    assert len(j["routes"]) == 2
    assert j["routes"][0]["illustrative"] is False
    assert j["pending"] == []
    assert j["my_quotes"] == []
    assert j["llm_mode"] == "mock"
    assert st.toolbox.lock is st.lock


def test_chat_demo_mode(tmp_path):
    client, _ = make_client(tmp_path)
    r = client.post("/api/chat", json={"message": "cheapest for 1000 CNY"})
    assert r.status_code == 200
    j = r.json()
    assert j["trace"][0]["tool"] == "compare_routes"
    assert "40400.00" in j["text"]
    assert j["seconds"] >= 0


def test_chat_rejects_empty_and_long(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.post("/api/chat", json={"message": "   "}).status_code == 400
    long_msg = "x" * 2001
    assert client.post("/api/chat", json={"message": long_msg}).status_code == 400


def test_transfer_pending_then_confirm(tmp_path):
    client, _ = make_client(tmp_path)
    r = request_b(client)
    assert r["status"] == "pending_confirmation"
    assert r["transfer_id"] == "T0001"
    assert len(client.get("/api/state").json()["pending"]) == 1
    c = client.post("/api/confirm/T0001").json()
    assert c["status"] == "executed"
    j = client.get("/api/state").json()
    assert j["pending"] == []
    assert j["balances"] == {"pkr": "59600.00", "cny": "1000"}


def test_reject_and_unknown_ids(tmp_path):
    client, _ = make_client(tmp_path)
    request_b(client)
    assert client.post("/api/reject/T0001").json()["status"] == "rejected"
    assert client.get("/api/state").json()["balances"]["pkr"] == "100000"
    assert client.post("/api/confirm/T9999").status_code == 404
    assert client.post("/api/reject/T0001").status_code == 404


def test_tier_endpoint(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.post("/api/tier", json={"tier": 2}).status_code == 200
    assert client.get("/api/state").json()["tier"] == 2
    assert client.post("/api/tier", json={"tier": 3}).status_code == 400
    assert client.post("/api/tier", json={"tier": -1}).status_code == 400


def test_tool_endpoint_cannot_confirm(tmp_path):
    client, _ = make_client(tmp_path)
    request_b(client)
    out = client.post("/api/tool", json={
        "name": "confirm", "args": {"transfer_id": "T0001"}}).json()
    assert "error" in out
    j = client.get("/api/state").json()
    assert len(j["pending"]) == 1
    assert j["balances"]["pkr"] == "100000"


def test_quotes_endpoint(tmp_path):
    client, _ = make_client(tmp_path)
    r = client.post("/api/quotes", json={
        "provider": "Bank X", "pkr_paid": "41000", "cny_received": "1000"})
    assert r.status_code == 200
    j = r.json()
    assert j["effective_rate"] == "41.0000"
    assert j["hidden_cost_pkr"] == "1000.00"
    assert j["hidden_cost_pct"] == "0.0250"
    assert len(client.get("/api/state").json()["my_quotes"]) == 1
    bad_zero = client.post("/api/quotes", json={
        "provider": "X", "pkr_paid": "100", "cny_received": "0"})
    assert bad_zero.status_code == 400
    bad_text = client.post("/api/quotes", json={
        "provider": "X", "pkr_paid": "abc", "cny_received": "5"})
    assert bad_text.status_code == 400


def test_llm_failure_returns_502_and_rolls_back(tmp_path):
    client, st = make_client(tmp_path, model=FailingModel())
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 502
    assert "unavailable" in r.json()["detail"]
    assert st.agent.contents == []


def test_log_endpoint(tmp_path):
    client, _ = make_client(tmp_path, tier=2)
    assert client.get("/api/log").json() == {"entries": []}
    assert request_b(client)["status"] == "executed"
    entries = client.get("/api/log").json()["entries"]
    assert len(entries) == 1
    assert entries[0]["status"] == "executed"


def test_mandate_update(tmp_path):
    client, _ = make_client(tmp_path)
    r = client.post("/api/mandate", json={
        "max_transfer_cny": "500", "auto_execute_limit_cny": "100",
        "max_pkr_per_cny": "43"})
    assert r.status_code == 200
    m = client.get("/api/state").json()["mandate"]
    assert m == {"max_transfer_cny": "500", "auto_execute_limit_cny": "100",
                 "max_pkr_per_cny": "43"}


def test_mandate_invalid_rejected_and_unchanged(tmp_path):
    client, _ = make_client(tmp_path)
    over = client.post("/api/mandate", json={
        "max_transfer_cny": "500", "auto_execute_limit_cny": "600",
        "max_pkr_per_cny": "43"})
    assert over.status_code == 400
    text = client.post("/api/mandate", json={
        "max_transfer_cny": "abc", "auto_execute_limit_cny": "1",
        "max_pkr_per_cny": "43"})
    assert text.status_code == 400
    zero = client.post("/api/mandate", json={
        "max_transfer_cny": "0", "auto_execute_limit_cny": "0",
        "max_pkr_per_cny": "43"})
    assert zero.status_code == 400
    m = client.get("/api/state").json()["mandate"]
    assert m["max_transfer_cny"] == "10000"


def test_lower_mandate_blocks_transfer(tmp_path):
    client, _ = make_client(tmp_path)
    client.post("/api/mandate", json={
        "max_transfer_cny": "500", "auto_execute_limit_cny": "100",
        "max_pkr_per_cny": "43"})
    assert request_b(client, 1000)["status"] == "blocked"


def test_mandate_cannot_be_changed_through_ai_tool_endpoint(tmp_path):
    client, _ = make_client(tmp_path)
    out = client.post("/api/tool", json={
        "name": "set_mandate",
        "args": {"max_transfer_cny": "999999",
                 "auto_execute_limit_cny": "999999",
                 "max_pkr_per_cny": "999"}}).json()
    assert "error" in out
    m = client.get("/api/state").json()["mandate"]
    assert m["max_transfer_cny"] == "10000"


def test_mandate_change_is_logged(tmp_path):
    client, _ = make_client(tmp_path)
    client.post("/api/mandate", json={
        "max_transfer_cny": "500", "auto_execute_limit_cny": "100",
        "max_pkr_per_cny": "43"})
    entries = client.get("/api/log").json()["entries"]
    assert entries[-1]["event"] == "mandate_change"
    assert entries[-1]["status"] == "mandate_updated"


def test_is_illustrative():
    def r(source, as_of):
        return Route("X", D("0"), D("0"), D("0"), D("0"), source, as_of)

    assert is_illustrative(r("PLACEHOLDER - replace", "not checked")) is True
    assert is_illustrative(r("ILLUSTRATIVE", "2026-10-14")) is True
    assert is_illustrative(r("https://example.com/fees", "2026-10-14")) is False


def test_second_chat_while_busy_returns_409(tmp_path):
    client, st = make_client(tmp_path)
    assert st.chat_lock.acquire(blocking=False)
    try:
        r = client.post("/api/chat", json={"message": "hello"})
    finally:
        st.chat_lock.release()
    assert r.status_code == 409


def test_chat_does_not_hold_state_lock_during_model_call(tmp_path):
    probe = ProbeModel()
    client, st = make_client(tmp_path, model=probe)
    probe.state = st
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    assert probe.got is True


def test_unexpected_error_returns_500_and_rolls_back(tmp_path):
    client, st = make_client(tmp_path, model=BrokenModel())
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 500
    assert "RuntimeError" in r.json()["detail"]
    assert st.agent.contents == []