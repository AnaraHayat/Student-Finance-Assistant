from decimal import Decimal as D

import pytest

from core.mandate import (
    ALLOW_EXECUTE,
    BLOCKED,
    NEEDS_CONFIRMATION,
    Mandate,
    evaluate,
)

M = Mandate(max_transfer_cny=D("10000"), auto_execute_limit_cny=D("2000"),
            max_pkr_per_cny=D("43"), allowed_routes=frozenset({"A", "B"}))


def test_tier0_blocks_everything():
    assert evaluate(M, 0, "A", D("100"), D("42")).outcome == BLOCKED


def test_tier1_needs_confirmation():
    assert evaluate(M, 1, "A", D("100"), D("42")).outcome == NEEDS_CONFIRMATION


def test_tier2_executes_within_limit():
    assert evaluate(M, 2, "A", D("1500"), D("42")).outcome == ALLOW_EXECUTE


def test_tier2_exactly_at_limit_executes():
    assert evaluate(M, 2, "A", D("2000"), D("42")).outcome == ALLOW_EXECUTE


def test_tier2_above_limit_needs_confirmation():
    assert evaluate(M, 2, "A", D("5000"), D("42")).outcome == NEEDS_CONFIRMATION


def test_exactly_at_hard_cap_is_not_blocked():
    assert evaluate(M, 2, "A", D("10000"), D("42")).outcome == NEEDS_CONFIRMATION


def test_above_hard_cap_blocked():
    assert evaluate(M, 2, "A", D("10001"), D("42")).outcome == BLOCKED


def test_worse_rate_blocked_and_boundary_allowed():
    assert evaluate(M, 2, "A", D("100"), D("43.0001")).outcome == BLOCKED
    assert evaluate(M, 2, "A", D("100"), D("43")).outcome == ALLOW_EXECUTE


def test_route_not_allowed_blocked():
    assert evaluate(M, 2, "C", D("100"), D("42")).outcome == BLOCKED


def test_any_route_when_no_list():
    m = Mandate(D("10000"), D("2000"), D("43"))
    assert evaluate(m, 2, "Z", D("100"), D("42")).outcome == ALLOW_EXECUTE


def test_blocked_gives_all_reasons():
    d = evaluate(M, 2, "C", D("20000"), D("50"))
    assert d.outcome == BLOCKED
    assert len(d.reasons) == 3


def test_invalid_mandate_rejected():
    with pytest.raises(ValueError):
        Mandate(D("1000"), D("2000"), D("43"))
    with pytest.raises(ValueError):
        Mandate(D("0"), D("0"), D("43"))
    with pytest.raises(ValueError):
        Mandate(D("1000"), D("500"), D("0"))


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        evaluate(M, 3, "A", D("100"), D("42"))
    with pytest.raises(ValueError):
        evaluate(M, 1, "A", D("0"), D("42"))
    with pytest.raises(ValueError):
        evaluate(M, 1, "A", D("100"), D("0"))
