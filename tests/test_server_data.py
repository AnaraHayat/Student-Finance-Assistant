from datetime import date
from decimal import Decimal as D

from fastapi.testclient import TestClient

from agent.llm import LLMError
from agent.loop import Agent
from agent.mock import MockModel
from agent.tools import ToolBox
from core.mandate import Mandate
from core.money import Route
from core.rates import Rate
from sandbox.ledger import Sandbox
from server import AppState, create_app

B = Route("B", D("0.01"), D("0"), D("0"), D("0"), "srcB", "dB")
RATE = Rate(D("40"), "asof", "next", "src", False)
M = Mandate(D("10000"), D("2000"), D("43"))


class ToolThenFail:
    def __init__(self):
        self.n = 0

    def generate(self, contents, tools, system):
        self.n += 1
        if self.n == 1:
            return [{"functionCall": {"name": "add_expense", "args": {
                "name": "tea", "on": "2026-10-12",
                "amount_cny": 5, "category": "food"}}}]
        raise LLMError("down")


def make_client(tmp_path, model=None):
    sb = Sandbox(D("100000"), M, 1, tmp_path / "audit.jsonl")
    tb = ToolBox(sb, [B], RATE)
    agent = Agent(model or MockModel(), tb, today=date(2026, 10, 12))
    st = AppState(sb, tb, agent, RATE, [B], "mock")
    return TestClient(create_app(st, access_token="", local_only=False,
                                 allowed_hosts=["testserver"])), st


def test_state_lists_expenses_and_events(tmp_path):
    client, _ = make_client(tmp_path)
    j = client.get("/api/state").json()
    assert j["expenses"] == [] and j["events"] == []
    client.post("/api/tool", json={"name": "add_expense", "args": {
        "name": "lunch", "on": "2026-10-12",
        "amount_cny": 25, "category": "food"}})
    client.post("/api/tool", json={"name": "add_event", "args": {
        "name": "rent", "on": "2026-10-20",
        "amount_cny": 1800, "kind": "obligation"}})
    j = client.get("/api/state").json()
    assert j["expenses"] == [{"name": "lunch", "on": "2026-10-12",
                              "amount_cny": "25", "category": "food"}]
    assert j["events"] == [{"name": "rent", "on": "2026-10-20",
                            "amount_cny": "1800", "kind": "obligation"}]


def test_failure_after_a_tool_call_reports_completed_actions(tmp_path):
    client, st = make_client(tmp_path, model=ToolThenFail())
    r = client.post("/api/chat", json={"message": "add tea 5"})
    assert r.status_code == 502
    assert "add_expense" in r.json()["detail"]
    assert st.agent.contents == []
    j = client.get("/api/state").json()
    assert len(j["expenses"]) == 1
    assert j["expenses"][0]["name"] == "tea"
