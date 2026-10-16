import json
from decimal import Decimal as D

from agent.tools import ToolBox
from core.mandate import Mandate
from core.money import Route
from core.rates import Rate
from sandbox.ledger import Sandbox

A = Route("A", D("0.02"), D("500"), D("0.01"), D("0"), "srcA", "dA")
B = Route("B", D("0.01"), D("0"), D("0"), D("0"), "srcB", "dB")
RATE = Rate(D("40"), "asof", "next", "src", False)
M = Mandate(D("10000"), D("2000"), D("43"))


def make(tmp_path, tier):
    sb = Sandbox(D("100000"), M, tier, tmp_path / "audit.jsonl")
    return ToolBox(sb, [A, B], RATE), sb


def test_tool_names_exclude_confirm_and_reject(tmp_path):
    tb, _ = make(tmp_path, 1)
    assert set(tb.tool_names) == {
        "get_rate", "get_balances", "compare_routes", "request_transfer",
        "add_expense", "spending_summary", "add_event", "forecast_cashflow"}
    assert "confirm" not in tb.tool_names
    assert "reject" not in tb.tool_names


def test_agent_cannot_confirm_by_calling_unknown_tool(tmp_path):
    tb, sb = make(tmp_path, 1)
    r = tb.call("request_transfer", {"route_name": "A", "cny_to_deliver": 1000})
    out = tb.call("confirm", {"transfer_id": r["transfer_id"]})
    assert "error" in out
    assert sb.pkr_balance == D("100000")


def test_compare_routes_sorted_with_sources(tmp_path):
    tb, _ = make(tmp_path, 1)
    out = tb.call("compare_routes", {"cny_to_deliver": 1000})
    assert out["mid_rate"] == "40"
    assert out["quotes"][0]["route"] == "B"
    assert out["quotes"][0]["total_pkr"] == "40400.00"
    assert out["quotes"][0]["fee_source"] == "srcB"
    assert out["quotes"][1]["route"] == "A"
    assert out["quotes"][1]["total_pkr"] == "41708.00"


def test_tier1_transfer_pends_and_balance_unchanged(tmp_path):
    tb, sb = make(tmp_path, 1)
    out = tb.call("request_transfer", {"route_name": "A", "cny_to_deliver": 1000})
    assert out["status"] == "pending_confirmation"
    assert sb.pkr_balance == D("100000")
    assert len(sb.pending) == 1


def test_tier2_executes_and_summary_uses_effective_rate(tmp_path):
    tb, sb = make(tmp_path, 2)
    out = tb.call("request_transfer", {"route_name": "B", "cny_to_deliver": 1000})
    assert out["status"] == "executed"
    assert out["total_pkr"] == "40400.00"
    assert sb.pkr_balance == D("59600.00")
    assert tb.call("get_balances")["cny_balance"] == "1000"
    tb.call("add_expense", {"name": "rent", "on": "2026-10-05",
                            "amount_cny": 1800, "category": "housing"})
    s = tb.call("spending_summary", {"year": 2026, "month": 10})
    assert s["total_cny"] == "1800"
    assert s["total_pkr_mid"] == "72000.00"
    assert s["total_pkr_effective"] == "72720.00"
    assert s["hidden_cost_pkr"] == "720.00"


def test_over_cap_transfer_blocked(tmp_path):
    tb, sb = make(tmp_path, 2)
    out = tb.call("request_transfer", {"route_name": "B", "cny_to_deliver": 10001})
    assert out["status"] == "blocked"
    assert sb.pkr_balance == D("100000")


def test_unknown_route_returns_error(tmp_path):
    tb, _ = make(tmp_path, 2)
    out = tb.call("request_transfer", {"route_name": "NOPE", "cny_to_deliver": 100})
    assert "error" in out


def test_bad_inputs_return_errors_not_exceptions(tmp_path):
    tb, _ = make(tmp_path, 2)
    assert "error" in tb.call("compare_routes", {"cny_to_deliver": "abc"})
    assert "error" in tb.call("compare_routes", {"cny_to_deliver": "NaN"})
    assert "error" in tb.call("compare_routes", {"cny_to_deliver": -5})
    assert "error" in tb.call("compare_routes", {"wrong_name": 5})
    assert "error" in tb.call("no_such_tool", {})


def test_forecast_through_tools(tmp_path):
    tb, _ = make(tmp_path, 1)
    tb.call("add_event", {"name": "rent", "on": "2026-10-05",
                          "amount_cny": 1800, "kind": "obligation"})
    tb.call("add_event", {"name": "home", "on": "2026-10-03",
                          "amount_cny": 500, "kind": "inflow"})
    out = tb.call("forecast_cashflow", {
        "start": "2026-10-01", "end": "2026-10-10",
        "start_balance_cny": 1000, "lead_days": 3})
    assert out["min_balance_cny"] == "-300"
    assert out["first_shortfall_date"] == "2026-10-05"
    assert out["topup_needed_cny"] == "300"
    assert out["transfer_by"] == "2026-10-02"
    assert out["is_late"] is False


def test_forecast_event_before_start_returns_error(tmp_path):
    tb, _ = make(tmp_path, 1)
    tb.call("add_event", {"name": "old", "on": "2026-09-30",
                          "amount_cny": 10, "kind": "obligation"})
    out = tb.call("forecast_cashflow", {"start": "2026-10-01",
                                        "end": "2026-10-10"})
    assert "error" in out


def test_bad_date_returns_error(tmp_path):
    tb, _ = make(tmp_path, 1)
    out = tb.call("add_expense", {"name": "x", "on": "not-a-date",
                                  "amount_cny": 5, "category": "food"})
    assert "error" in out


def test_all_results_are_json_serializable(tmp_path):
    tb, _ = make(tmp_path, 2)
    results = [
        tb.call("get_rate"),
        tb.call("get_balances"),
        tb.call("compare_routes", {"cny_to_deliver": 1000}),
        tb.call("spending_summary", {"year": 2026, "month": 10}),
        tb.call("forecast_cashflow", {"start": "2026-10-01",
                                      "end": "2026-10-10",
                                      "start_balance_cny": 100}),
    ]
    for r in results:
        json.dumps(r)