from datetime import date
from decimal import Decimal as D

from agent.loop import Agent
from agent.mock import MockModel
from agent.tools import ToolBox
from core.mandate import Mandate
from core.money import Route
from core.rates import Rate
from sandbox.ledger import Sandbox

A = Route("A", D("0.02"), D("500"), D("0.01"), D("0"), "srcA", "dA")
B = Route("B", D("0.01"), D("0"), D("0"), D("0"), "srcB", "dB")
RATE = Rate(D("40"), "asof", "next", "src", False)
M = Mandate(D("10000"), D("2000"), D("43"))


def make_agent(tmp_path):
    sb = Sandbox(D("100000"), M, 1, tmp_path / "a.jsonl")
    return Agent(MockModel(), ToolBox(sb, [A, B], RATE),
                 today=date(2026, 10, 12))


def test_mock_compares_routes_when_amount_given(tmp_path):
    out = make_agent(tmp_path).ask("cheapest way to send 1,000 CNY?")
    assert len(out["trace"]) == 1
    assert out["trace"][0]["tool"] == "compare_routes"
    assert "[Demo mode]" in out["text"]
    assert "B" in out["text"]
    assert "40400.00" in out["text"]


def test_mock_without_amount_makes_no_tool_call(tmp_path):
    out = make_agent(tmp_path).ask("hello there")
    assert out["trace"] == []
    assert "[Demo mode]" in out["text"]