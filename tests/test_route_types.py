import json
from decimal import Decimal as D

from agent.tools import ToolBox
from core.mandate import Mandate
from core.money import Route
from core.rates import Rate
from core.routes import compare, load_routes
from sandbox.ledger import Sandbox

BANK = Route("Bank", D("0.01"), D("0"), D("0"), D("0"), "s", "d",
             route_type="bank_transfer", typical_days=3, supports_tuition=True)
APP = Route("App", D("0.02"), D("0"), D("0"), D("0"), "s", "d",
            route_type="remittance_service")


def test_new_fields_default():
    r = Route("A", D("0"), D("0"), D("0"), D("0"), "s", "d")
    assert r.route_type == "unspecified"
    assert r.typical_days is None
    assert r.supports_tuition is False


def test_compare_filters_by_type():
    qs = compare([BANK, APP], D("1000"), D("40"), route_type="bank_transfer")
    assert [q.route for q in qs] == ["Bank"]
    assert compare([BANK, APP], D("1000"), D("40"), route_type="nope") == []
    assert len(compare([BANK, APP], D("1000"), D("40"))) == 2


def test_load_routes_reads_new_and_missing_fields(tmp_path):
    f = tmp_path / "fees.json"
    f.write_text(json.dumps([
        {"name": "X", "markup": "0.01", "fixed_fee_pkr": "0",
         "pct_fee": "0", "recipient_fee_cny": "0", "source": "s",
         "as_of": "d", "route_type": "bank_transfer", "typical_days": 3,
         "supports_tuition": True},
        {"name": "Y", "markup": "0.01", "fixed_fee_pkr": "0",
         "pct_fee": "0", "recipient_fee_cny": "0", "source": "s",
         "as_of": "d"}]), encoding="utf-8")
    x, y = load_routes(f)
    assert (x.route_type, x.typical_days, x.supports_tuition) == (
        "bank_transfer", 3, True)
    assert (y.route_type, y.typical_days, y.supports_tuition) == (
        "unspecified", None, False)


def make(tmp_path):
    sb = Sandbox(D("100000"), Mandate(D("10000"), D("2000"), D("43")), 1,
                 tmp_path / "a.jsonl")
    return ToolBox(sb, [BANK, APP], Rate(D("40"), "a", "n", "s", False))


def test_tool_filters_and_reports_type(tmp_path):
    out = make(tmp_path).call("compare_routes", {
        "cny_to_deliver": 1000, "route_type": "bank_transfer"})
    assert len(out["quotes"]) == 1
    q = out["quotes"][0]
    assert q["route"] == "Bank"
    assert q["total_pkr"] == "40400.00"
    assert q["typical_days"] == 3
    assert q["supports_tuition"] is True


def test_tool_unknown_type_returns_no_quotes(tmp_path):
    out = make(tmp_path).call("compare_routes", {
        "cny_to_deliver": 1000, "route_type": "nope"})
    assert out["quotes"] == []
