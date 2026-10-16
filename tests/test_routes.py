import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from core.money import Route
from core.rates import parse_response
from core.routes import compare, load_routes

A = Route("A", D("0.02"), D("500"), D("0.01"), D("0"), "t", "t")
B = Route("B", D("0.01"), D("0"), D("0"), D("0"), "t", "t")
C = Route("C", D("0.03"), D("0"), D("0"), D("0"), "t", "t")


def test_compare_sorts_cheapest_first():
    # B = 40400, C = 41200, A = 41708 (mid rate 40, 1000 CNY)
    qs = compare([A, B, C], D("1000"), D("40"))
    assert [q.route for q in qs] == ["B", "C", "A"]
    assert qs[0].total_pkr == D("40400.00")


def test_parse_response_ok():
    d = {"result": "success",
         "time_last_update_utc": "x", "time_next_update_utc": "y",
         "rates": {"PKR": 41.318899}}
    assert parse_response(d).pkr_per_cny == D("41.318899")


def test_parse_response_rejects_failure():
    with pytest.raises(ValueError):
        parse_response({"result": "error"})


def test_load_routes_from_file(tmp_path):
    f = tmp_path / "fees.json"
    f.write_text(json.dumps([{
        "name": "X", "markup": "0.02", "fixed_fee_pkr": "500",
        "pct_fee": "0.01", "recipient_fee_cny": "0",
        "source": "s", "as_of": "d"}]), encoding="utf-8")
    routes = load_routes(f)
    assert routes[0].markup == D("0.02")


def test_real_fees_file_loads():
    path = Path(__file__).resolve().parent.parent / "data" / "fees.json"
    assert len(load_routes(path)) >= 3