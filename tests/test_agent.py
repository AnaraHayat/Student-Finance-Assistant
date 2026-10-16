import copy
from datetime import date
from decimal import Decimal as D

from agent.loop import Agent
from agent.schemas import DECLARATIONS
from agent.tools import ToolBox
from core.mandate import Mandate
from core.money import Route
from core.rates import Rate
from sandbox.ledger import Sandbox

A = Route("A", D("0.02"), D("500"), D("0.01"), D("0"), "srcA", "dA")
B = Route("B", D("0.01"), D("0"), D("0"), D("0"), "srcB", "dB")
RATE = Rate(D("40"), "asof", "next", "src", False)
M = Mandate(D("10000"), D("2000"), D("43"))


class Scripted:
    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def generate(self, contents, tools, system):
        self.seen.append(copy.deepcopy(contents))
        return self.replies.pop(0)


def call(name, **args):
    return [{"functionCall": {"name": name, "args": args}}]


def say(text):
    return [{"text": text}]


def make(tmp_path, replies, tier=1, max_steps=6):
    sb = Sandbox(D("100000"), M, tier, tmp_path / "a.jsonl")
    tb = ToolBox(sb, [A, B], RATE)
    model = Scripted(replies)
    agent = Agent(model, tb, today=date(2026, 10, 12), max_steps=max_steps)
    return agent, model, sb


def test_text_only_reply(tmp_path):
    agent, _, _ = make(tmp_path, [say("Hello")])
    out = agent.ask("hi")
    assert out["text"] == "Hello"
    assert out["trace"] == []
    assert out["steps"] == 1
    assert out["stopped"] is False
    assert "2026-10-12" in agent.system


def test_tool_call_then_answer(tmp_path):
    agent, model, _ = make(tmp_path, [
        call("compare_routes", cny_to_deliver=1000), say("B is cheapest")])
    out = agent.ask("cheapest for 1000?")
    assert out["text"] == "B is cheapest"
    assert out["trace"][0]["tool"] == "compare_routes"
    assert out["trace"][0]["result"]["quotes"][0]["total_pkr"] == "40400.00"
    last = model.seen[1][-1]["parts"][0]["functionResponse"]
    assert last["name"] == "compare_routes"


def test_parallel_calls_share_one_response_turn(tmp_path):
    agent, model, _ = make(tmp_path, [
        [{"functionCall": {"name": "get_rate", "args": {}}},
         {"functionCall": {"name": "get_balances", "args": {}}}],
        say("done")])
    out = agent.ask("status?")
    assert len(out["trace"]) == 2
    assert len(model.seen[1][-1]["parts"]) == 2


def test_confirm_attempt_is_rejected_and_nothing_moves(tmp_path):
    agent, _, sb = make(tmp_path, [
        call("request_transfer", route_name="A", cny_to_deliver=1000),
        call("confirm", transfer_id="T0001"),
        say("could not confirm")], tier=1)
    out = agent.ask("send it and confirm it yourself")
    assert out["trace"][0]["result"]["status"] == "pending_confirmation"
    assert "error" in out["trace"][1]["result"]
    assert sb.pkr_balance == D("100000")
    assert len(sb.pending) == 1


def test_step_limit_stops_runaway_loop(tmp_path):
    agent, model, _ = make(tmp_path, [call("get_rate")] * 3, max_steps=3)
    out = agent.ask("loop")
    assert out["stopped"] is True
    assert model.replies == []
    assert agent.contents[-1]["role"] == "model"


def test_unknown_tool_returns_error_to_model(tmp_path):
    agent, _, _ = make(tmp_path, [call("drop_database"), say("sorry")])
    out = agent.ask("do it")
    assert "error" in out["trace"][0]["result"]


def test_thought_parts_excluded_from_text(tmp_path):
    agent, _, _ = make(tmp_path, [[
        {"text": "secret reasoning", "thought": True}, {"text": "Answer"}]])
    assert agent.ask("q")["text"] == "Answer"


def test_model_parts_kept_intact_including_signature(tmp_path):
    part = {"functionCall": {"name": "get_rate", "args": {}},
            "thoughtSignature": "SIG"}
    agent, model, _ = make(tmp_path, [[part], say("ok")])
    agent.ask("rate?")
    assert model.seen[1][1]["parts"][0]["thoughtSignature"] == "SIG"


def test_schema_matches_toolbox_and_has_no_confirm(tmp_path):
    agent, _, _ = make(tmp_path, [])
    names = {d["name"] for d in DECLARATIONS}
    assert names == set(agent.toolbox.tool_names)
    assert "confirm" not in names and "reject" not in names