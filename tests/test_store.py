from decimal import Decimal as D

from fastapi.testclient import TestClient

from agent.loop import Agent
from agent.mock import MockModel
from agent.tools import ToolBox
from core.mandate import Mandate
from core.money import Route, quote
from core.rates import Rate
from sandbox.ledger import Sandbox
from server import AppState, create_app
from store import Store

A = Route("A", D("0.02"), D("500"), D("0.01"), D("0"), "srcA", "dA")
RATE = Rate(D("40"), "asof", "next", "src", False)
M = Mandate(D("10000"), D("2000"), D("43"))


def fresh(tmp_path, store=None, tier=1):
    sb = Sandbox(D("100000"), M, tier, tmp_path / "audit.jsonl", store=store)
    tb = ToolBox(sb, [A], RATE, store=store)
    return sb, tb


def build_client(tmp_path, store, tier=1):
    sb, tb = fresh(tmp_path, store, tier=tier)
    quotes = store.restore(sb, tb)
    st = AppState(sb, tb, Agent(MockModel(), tb), RATE, [A], "mock",
                  store=store)
    st.my_quotes = quotes
    return TestClient(create_app(st)), st


def test_round_trip_sandbox_and_collections(tmp_path):
    store = Store(tmp_path / "fx.db")
    sb, tb = fresh(tmp_path, store, tier=2)

    # a tier-2 transfer executes and moves the balances
    sb.request_transfer(quote(A, D("1000"), D("40")))
    # then a tier-1 style pending transfer
    sb.set_tier(1)
    sb.request_transfer(quote(A, D("500"), D("40")))
    tb.call("add_expense", {"name": "lunch", "on": "2026-10-12",
                            "amount_cny": 25, "category": "food"})
    tb.call("add_event", {"name": "rent", "on": "2026-10-20",
                          "amount_cny": 1800, "kind": "obligation"})
    store.save_quotes([{
        "provider": "Bank X", "on": "2026-10-12", "pkr_paid": "41000",
        "cny_received": "1000", "mid_rate": "40", "effective_rate": "41",
        "hidden_cost_pkr": "1000.00", "hidden_cost_pct": "0.0250"}])

    pkr_after = sb.pkr_balance
    cny_after = sb.cny_balance
    pending_ids = sorted(sb.pending)
    next_id = sb.next_id

    # brand new objects, then restore
    sb2, tb2 = fresh(tmp_path)
    quotes = store.restore(sb2, tb2)

    assert sb2.pkr_balance == pkr_after
    assert sb2.cny_balance == cny_after
    assert sb2.tier == 1
    assert sb2.next_id == next_id
    assert sorted(sb2.pending) == pending_ids
    assert sb2.pending[pending_ids[0]].total_pkr == sb.pending[
        pending_ids[0]].total_pkr
    assert [e.name for e in tb2.expenses] == ["lunch"]
    assert tb2.expenses[0].amount_cny == D("25")
    assert [e.name for e in tb2.events] == ["rent"]
    assert len(quotes) == 1 and quotes[0]["provider"] == "Bank X"


def test_restore_keeps_new_transfer_ids_unique(tmp_path):
    store = Store(tmp_path / "fx.db")
    sb, _ = fresh(tmp_path, store, tier=2)
    first = sb.request_transfer(quote(A, D("1000"), D("40")))
    sb.set_tier(1)
    second = sb.request_transfer(quote(A, D("500"), D("40")))

    sb2, tb2 = fresh(tmp_path)
    store.restore(sb2, tb2)
    third = sb2.request_transfer(quote(A, D("500"), D("40")))
    assert third["transfer_id"] not in (first["transfer_id"],
                                        second["transfer_id"])


def test_empty_store_restores_fresh_state(tmp_path):
    store = Store(tmp_path / "fx.db")
    sb, tb = fresh(tmp_path)
    quotes = store.restore(sb, tb)
    assert quotes == []
    assert sb.pkr_balance == D("100000")
    assert sb.pending == {}
    assert tb.expenses == [] and tb.events == []


def test_state_survives_a_restart_through_the_api(tmp_path):
    store = Store(tmp_path / "fx.db")
    client, _ = build_client(tmp_path, store, tier=2)

    # an executed transfer, an expense, a pending draft, limits and a quote
    assert client.post("/api/tool", json={
        "name": "request_transfer",
        "args": {"route_name": "A", "cny_to_deliver": 1000}}
    ).json()["status"] == "executed"
    client.post("/api/tool", json={
        "name": "add_expense",
        "args": {"name": "lunch", "on": "2026-10-12",
                 "amount_cny": 25, "category": "food"}})
    client.post("/api/tier", json={"tier": 1})
    client.post("/api/tool", json={
        "name": "request_transfer",
        "args": {"route_name": "A", "cny_to_deliver": 500}})
    client.post("/api/mandate", json={
        "max_transfer_cny": "8000", "auto_execute_limit_cny": "1000",
        "max_pkr_per_cny": "44"})
    client.post("/api/quotes", json={
        "provider": "Bank X", "pkr_paid": "41000", "cny_received": "1000"})

    before = client.get("/api/state").json()
    assert before["balances"]["cny"] == "1000"
    assert before["tier"] == 1

    # "restart": a brand new AppState sharing the same database file
    client2, _ = build_client(tmp_path, store, tier=0)
    after = client2.get("/api/state").json()

    assert after["balances"] == before["balances"]
    assert after["tier"] == 1
    assert after["mandate"] == before["mandate"]
    assert [e["name"] for e in after["expenses"]] == ["lunch"]
    assert len(after["pending"]) == 1
    assert after["my_quotes"][0]["provider"] == "Bank X"
    assert client2.post("/api/reject/" +
                        after["pending"][0]["transfer_id"]
                        ).json()["status"] == "rejected"
