import json
from decimal import Decimal as D

import pytest

from core.mandate import Mandate
from core.money import Route, quote
from sandbox.ledger import Sandbox

M = Mandate(D("10000"), D("2000"), D("43"), frozenset({"A"}))
A = Route("A", D("0.02"), D("500"), D("0.01"), D("0"), "t", "t")
Z = Route("Z", D("0.02"), D("500"), D("0.01"), D("0"), "t", "t")


def q(route, amount):
    # mid rate 40: for route A and 1000 CNY the total is 41708.00
    return quote(route, D(amount), D("40"))


def make(tmp_path, tier, balance="100000"):
    return Sandbox(D(balance), M, tier, tmp_path / "audit.jsonl")


def test_tier2_within_limit_executes(tmp_path):
    sb = make(tmp_path, 2)
    r = sb.request_transfer(q(A, "1000"))
    assert r["status"] == "executed"
    assert sb.pkr_balance == D("58292")
    assert sb.cny_balance == D("1000")


def test_tier1_pends_then_confirm_executes(tmp_path):
    sb = make(tmp_path, 1)
    r = sb.request_transfer(q(A, "1000"))
    assert r["status"] == "pending_confirmation"
    assert sb.pkr_balance == D("100000")
    assert sb.confirm(r["transfer_id"])["status"] == "executed"
    assert sb.pkr_balance == D("58292")


def test_tier0_blocked_and_unchanged(tmp_path):
    sb = make(tmp_path, 0)
    assert sb.request_transfer(q(A, "1000"))["status"] == "blocked"
    assert sb.pkr_balance == D("100000")
    assert sb.cny_balance == D("0")


def test_over_cap_blocked(tmp_path):
    sb = make(tmp_path, 2)
    assert sb.request_transfer(q(A, "10001"))["status"] == "blocked"
    assert sb.pkr_balance == D("100000")


def test_route_not_allowed_blocked(tmp_path):
    sb = make(tmp_path, 2)
    assert sb.request_transfer(q(Z, "1000"))["status"] == "blocked"


def test_insufficient_funds_at_request(tmp_path):
    sb = make(tmp_path, 2, balance="1000")
    assert sb.request_transfer(q(A, "1000"))["status"] == "insufficient_funds"
    assert sb.pkr_balance == D("1000")
    assert sb.cny_balance == D("0")


def test_audit_log_written(tmp_path):
    sb = make(tmp_path, 2)
    sb.request_transfer(q(A, "1000"))
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    e = json.loads(lines[0])
    assert e["event"] == "request"
    assert e["status"] == "executed"
    assert e["route"] == "A"
    assert e["pkr_balance_after"] == "58292.00"


def test_confirm_unknown_or_twice_raises(tmp_path):
    sb = make(tmp_path, 1)
    with pytest.raises(ValueError):
        sb.confirm("T9999")
    r = sb.request_transfer(q(A, "1000"))
    sb.confirm(r["transfer_id"])
    with pytest.raises(ValueError):
        sb.confirm(r["transfer_id"])


def test_reject_leaves_balance(tmp_path):
    sb = make(tmp_path, 1)
    r = sb.request_transfer(q(A, "1000"))
    assert sb.reject(r["transfer_id"])["status"] == "rejected"
    assert sb.pkr_balance == D("100000")
    with pytest.raises(ValueError):
        sb.confirm(r["transfer_id"])


def test_second_pending_fails_when_funds_gone(tmp_path):
    sb = make(tmp_path, 1, balance="60000")
    r1 = sb.request_transfer(q(A, "1000"))
    r2 = sb.request_transfer(q(A, "1000"))
    assert r1["status"] == r2["status"] == "pending_confirmation"
    assert sb.confirm(r1["transfer_id"])["status"] == "executed"
    assert sb.pkr_balance == D("18292")
    assert sb.confirm(r2["transfer_id"])["status"] == "insufficient_funds"
    assert sb.pkr_balance == D("18292")


def test_invalid_init(tmp_path):
    with pytest.raises(ValueError):
        Sandbox(D("-1"), M, 1, tmp_path / "a.jsonl")
    with pytest.raises(ValueError):
        Sandbox(D("1"), M, 5, tmp_path / "a.jsonl")
