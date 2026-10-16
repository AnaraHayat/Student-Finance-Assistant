from datetime import date
from decimal import Decimal as D

import pytest

from core.spending import Expense, summarize

EXP = [
    Expense("rent", date(2026, 10, 5), D("1800"), "housing"),
    Expense("lunch", date(2026, 10, 6), D("25"), "food"),
    Expense("lunch", date(2026, 10, 7), D("35"), "food"),
    Expense("metro", date(2026, 10, 8), D("6"), "transport"),
    Expense("snack", date(2026, 11, 1), D("100"), "food"),
]


def test_totals_and_categories():
    s = summarize(EXP, 2026, 10, D("40"), D("42"))
    assert s.count == 4
    assert s.total_cny == D("1866")
    assert s.total_pkr_mid == D("74640.00")
    assert s.total_pkr_effective == D("78372.00")
    assert s.hidden_cost_pkr == D("3732.00")
    assert [c.category for c in s.categories] == ["housing", "food", "transport"]
    assert s.categories[0].pkr_mid == D("72000.00")
    assert s.categories[1].cny == D("60")
    assert s.categories[1].share == D("0.0322")
    assert s.categories[0].share == D("0.9646")
    assert s.categories[2].share == D("0.0032")


def test_default_effective_equals_mid():
    s = summarize(EXP, 2026, 10, D("40"))
    assert s.total_pkr_effective == s.total_pkr_mid
    assert s.hidden_cost_pkr == D("0.00")


def test_other_month_excluded():
    s = summarize(EXP, 2026, 11, D("40"))
    assert s.count == 1
    assert s.total_cny == D("100")


def test_category_names_normalized():
    ex = [Expense("a", date(2026, 10, 1), D("10"), "Food"),
          Expense("b", date(2026, 10, 2), D("5"), "  food ")]
    s = summarize(ex, 2026, 10, D("40"))
    assert len(s.categories) == 1
    assert s.categories[0].category == "food"
    assert s.categories[0].cny == D("15")


def test_empty_month():
    s = summarize(EXP, 2026, 9, D("40"))
    assert s.count == 0
    assert s.total_cny == D("0")
    assert s.categories == ()
    assert s.hidden_cost_pkr == D("0.00")


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        summarize(EXP, 2026, 13, D("40"))
    with pytest.raises(ValueError):
        summarize(EXP, 2026, 10, D("0"))
    with pytest.raises(ValueError):
        summarize(EXP, 2026, 10, D("40"), D("-1"))
    with pytest.raises(ValueError):
        Expense("x", date(2026, 10, 1), D("0"), "food")
    with pytest.raises(ValueError):
        Expense("x", date(2026, 10, 1), D("5"), "   ")