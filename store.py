"""SQLite persistence for sandbox wallets, pending transfers, the mandate,
expenses, events and saved quotes.

Design notes:
- Money is stored as TEXT so the exact ``Decimal`` value round-trips (never
  floats, per the project rules).
- The audit log stays an append-only JSON-lines file; this store only keeps
  the mutable application state that used to reset on restart.
- One connection is shared. Every write happens while the caller holds the
  app state lock, so ``check_same_thread=False`` is safe.
"""

import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from core.forecast import Event
from core.mandate import Mandate
from core.money import Quote
from core.spending import Expense

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending (
  transfer_id     TEXT PRIMARY KEY,
  route           TEXT NOT NULL,
  cny_to_deliver  TEXT NOT NULL,
  cny_to_send     TEXT NOT NULL,
  offered_rate    TEXT NOT NULL,
  total_pkr       TEXT NOT NULL,
  effective_rate  TEXT NOT NULL,
  hidden_cost_pkr TEXT NOT NULL,
  hidden_cost_pct TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expenses (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL,
  on_date    TEXT NOT NULL,
  amount_cny TEXT NOT NULL,
  category   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL,
  on_date    TEXT NOT NULL,
  amount_cny TEXT NOT NULL,
  kind       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  provider        TEXT NOT NULL,
  on_date         TEXT NOT NULL,
  pkr_paid        TEXT NOT NULL,
  cny_received    TEXT NOT NULL,
  mid_rate        TEXT NOT NULL,
  effective_rate  TEXT NOT NULL,
  hidden_cost_pkr TEXT NOT NULL,
  hidden_cost_pct TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        self._conn.close()

    # ----- sandbox -----

    def save_sandbox(self, sandbox) -> None:
        m = sandbox.mandate
        mandate = {
            "max_transfer_cny": str(m.max_transfer_cny),
            "auto_execute_limit_cny": str(m.auto_execute_limit_cny),
            "max_pkr_per_cny": str(m.max_pkr_per_cny),
            "allowed_routes": (sorted(m.allowed_routes)
                               if m.allowed_routes is not None else None),
        }
        meta = {
            "pkr_balance": str(sandbox.pkr_balance),
            "cny_balance": str(sandbox.cny_balance),
            "tier": str(sandbox.tier),
            "next_id": str(sandbox.next_id),
            "mandate": json.dumps(mandate),
        }
        rows = [
            (tid, q.route, str(q.cny_to_deliver), str(q.cny_to_send),
             str(q.offered_rate), str(q.total_pkr), str(q.effective_rate),
             str(q.hidden_cost_pkr), str(q.hidden_cost_pct))
            for tid, q in sandbox.pending.items()
        ]
        with self._conn:
            self._conn.execute("DELETE FROM pending")
            self._conn.executemany(
                "INSERT INTO pending VALUES (?,?,?,?,?,?,?,?,?)", rows)
            self._conn.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                list(meta.items()))

    # ----- toolbox collections -----

    def save_expenses(self, expenses) -> None:
        rows = [(e.name, e.on.isoformat(), str(e.amount_cny), e.category)
                for e in expenses]
        with self._conn:
            self._conn.execute("DELETE FROM expenses")
            self._conn.executemany(
                "INSERT INTO expenses(name, on_date, amount_cny, category) "
                "VALUES (?,?,?,?)", rows)

    def save_events(self, events) -> None:
        rows = [(e.name, e.on.isoformat(), str(e.amount_cny), e.kind)
                for e in events]
        with self._conn:
            self._conn.execute("DELETE FROM events")
            self._conn.executemany(
                "INSERT INTO events(name, on_date, amount_cny, kind) "
                "VALUES (?,?,?,?)", rows)

    def save_quotes(self, quotes) -> None:
        rows = [(q["provider"], q["on"], q["pkr_paid"], q["cny_received"],
                 q["mid_rate"], q["effective_rate"], q["hidden_cost_pkr"],
                 q["hidden_cost_pct"]) for q in quotes]
        with self._conn:
            self._conn.execute("DELETE FROM quotes")
            self._conn.executemany(
                "INSERT INTO quotes(provider, on_date, pkr_paid, cny_received,"
                " mid_rate, effective_rate, hidden_cost_pkr, hidden_cost_pct) "
                "VALUES (?,?,?,?,?,?,?,?)", rows)

    # ----- load -----

    def restore(self, sandbox, toolbox) -> list:
        """Apply saved state onto a fresh Sandbox and ToolBox, then return
        the saved quotes (as plain dicts, ready for the API snapshot)."""
        meta = {r["key"]: r["value"]
                for r in self._conn.execute("SELECT key, value FROM meta")}
        if meta:
            m = json.loads(meta["mandate"])
            allowed = m["allowed_routes"]
            sandbox.load(
                pkr_balance=Decimal(meta["pkr_balance"]),
                cny_balance=Decimal(meta["cny_balance"]),
                tier=int(meta["tier"]),
                next_id=int(meta["next_id"]),
                mandate=Mandate(Decimal(m["max_transfer_cny"]),
                                Decimal(m["auto_execute_limit_cny"]),
                                Decimal(m["max_pkr_per_cny"]),
                                frozenset(allowed) if allowed else None),
                pending={
                    r["transfer_id"]: Quote(
                        route=r["route"],
                        cny_to_deliver=Decimal(r["cny_to_deliver"]),
                        cny_to_send=Decimal(r["cny_to_send"]),
                        offered_rate=Decimal(r["offered_rate"]),
                        total_pkr=Decimal(r["total_pkr"]),
                        effective_rate=Decimal(r["effective_rate"]),
                        hidden_cost_pkr=Decimal(r["hidden_cost_pkr"]),
                        hidden_cost_pct=Decimal(r["hidden_cost_pct"]),
                    )
                    for r in self._conn.execute("SELECT * FROM pending")
                })
        toolbox.load(
            expenses=[Expense(r["name"], date.fromisoformat(r["on_date"]),
                              Decimal(r["amount_cny"]), r["category"])
                      for r in self._conn.execute("SELECT * FROM expenses")],
            events=[Event(r["name"], date.fromisoformat(r["on_date"]),
                          Decimal(r["amount_cny"]), r["kind"])
                    for r in self._conn.execute("SELECT * FROM events")])
        return [{
            "provider": r["provider"], "on": r["on_date"],
            "pkr_paid": r["pkr_paid"], "cny_received": r["cny_received"],
            "mid_rate": r["mid_rate"], "effective_rate": r["effective_rate"],
            "hidden_cost_pkr": r["hidden_cost_pkr"],
            "hidden_cost_pct": r["hidden_cost_pct"],
        } for r in self._conn.execute("SELECT * FROM quotes ORDER BY id")]
