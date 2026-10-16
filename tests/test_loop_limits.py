from datetime import date
from decimal import Decimal as D

import pytest

from agent.loop import Agent
from agent.tools import ToolBox
from core.mandate import Mandate
from core.money import Route
from core.rates import Rate
from sandbox.ledger import Sandbox

B = Route("B", D("0.01"), D("0"), D("0"), D("0"), "srcB", "dB")
RATE = Rate(D("40"), "asof", "next", "src", False)
M = Mandate(D("10000"), D("2000"), D("43"))


class Scripted:
    def __init__(self, replies):
        self.replies = list(replies)

    def generate(self, contents, tools, system):
        return self.replies.pop(0)


class ToolThenBreak:
    def __init__(self):
        self.n = 0

    def generate(self, contents, tools, system):
        self.n += 1
        if self.n == 1:
            return [{"functionCall": {"name": "add_expense", "args": {
                "name": "tea", "on": "2026-10-12",
                "amount_cny": 5, "category": "food"}}}]
        raise RuntimeError("boom")


def make(tmp_path, model, **kw):
    sb = Sandbox(D("100000"), M, 1, tmp_path / "a.jsonl")
    tb = ToolBox(sb, [B], RATE)
    return Agent(model, tb, today=date(2026, 10, 12), **kw), tb


def test_time_limit_stops_before_next_model_call(tmp_path):
    model = Scripted([[{"functionCall": {"name": "get_rate", "args": {}}}],
                      [{"text": "x"}]])
    agent, _ = make(tmp_path, model, max_seconds=0)
    out = agent.ask("q")
    assert out["stopped"] is True
    assert len(model.replies) == 1
    assert len(out["trace"]) == 1
    assert agent.contents[-1]["role"] == "model"


def test_error_after_a_tool_call_carries_the_trace(tmp_path):
    agent, tb = make(tmp_path, ToolThenBreak())
    with pytest.raises(RuntimeError) as exc:
        agent.ask("add tea")
    assert exc.value.trace[0]["tool"] == "add_expense"
    assert len(tb.expenses) == 1