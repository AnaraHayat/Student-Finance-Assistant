from decimal import Decimal as D

import pytest

from core.money import Route, quote

ROUTE = Route("PLACEHOLDER_A", D("0.02"), D("500"), D("0.01"), D("0"),
              "placeholder", "n/a")


def test_basic_quote():
    # offered = 40.8; base = 40800; fee = 500 + 408; total = 41708
    q = quote(ROUTE, D("1000"), D("40"))
    assert q.offered_rate == D("40.8000")
    assert q.total_pkr == D("41708.00")
    assert q.effective_rate == D("41.7080")
    assert q.hidden_cost_pkr == D("1708.00")
    assert q.hidden_cost_pct == D("0.0427")


def test_recipient_fee_raises_amount_sent():
    # send 1010; base = 41208; fee = 500 + 412.08; total = 42120.08
    r = Route("PLACEHOLDER_B", D("0.02"), D("500"), D("0.01"), D("10"),
              "placeholder", "n/a")
    q = quote(r, D("1000"), D("40"))
    assert q.cny_to_send == D("1010")
    assert q.total_pkr == D("42120.08")
    assert q.hidden_cost_pct == D("0.0530")


def test_zero_cost_route_has_no_hidden_cost():
    r = Route("IDEAL", D("0"), D("0"), D("0"), D("0"), "n/a", "n/a")
    assert quote(r, D("500"), D("41.318899")).hidden_cost_pkr == D("0.00")


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        quote(ROUTE, D("0"), D("40"))
    with pytest.raises(ValueError):
        quote(ROUTE, D("100"), D("-1"))
