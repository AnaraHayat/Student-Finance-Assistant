from decimal import Decimal as D

import pytest

from core.user_quote import analyze_quote


def test_basic_quote():
    q = analyze_quote(" Bank X ", D("41000"), D("1000"), D("40"))
    assert q.provider == "Bank X"
    assert q.effective_rate == D("41.0000")
    assert q.hidden_cost_pkr == D("1000.00")
    assert q.hidden_cost_pct == D("0.0250")


def test_better_than_mid_market_is_negative():
    q = analyze_quote("Y", D("39000"), D("1000"), D("40"))
    assert q.hidden_cost_pkr == D("-1000.00")
    assert q.hidden_cost_pct == D("-0.0250")


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        analyze_quote("  ", D("1"), D("1"), D("40"))
    with pytest.raises(ValueError):
        analyze_quote("X", D("0"), D("1"), D("40"))
    with pytest.raises(ValueError):
        analyze_quote("X", D("1"), D("-1"), D("40"))
    with pytest.raises(ValueError):
        analyze_quote("X", D("1"), D("1"), D("0"))
