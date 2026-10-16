import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from core.mandate import ALLOW_EXECUTE, BLOCKED, Mandate, evaluate
from core.money import Quote


class Sandbox:
    def __init__(self, pkr_balance: Decimal, mandate: Mandate, tier: int,
                 log_path, store=None):
        if pkr_balance < 0:
            raise ValueError("pkr_balance cannot be negative")
        if tier not in (0, 1, 2):
            raise ValueError("tier must be 0, 1 or 2")
        self.pkr_balance = pkr_balance
        self.cny_balance = Decimal("0")
        self.mandate = mandate
        self.tier = tier
        self.log_path = Path(log_path)
        self.pending = {}
        self._n = 0
        self.store = store

    @property
    def next_id(self) -> int:
        return self._n

    def load(self, *, pkr_balance, cny_balance, tier, next_id, mandate,
             pending) -> None:
        """Restore saved state (used by the Store on startup)."""
        self.pkr_balance = pkr_balance
        self.cny_balance = cny_balance
        self.tier = tier
        self._n = next_id
        self.mandate = mandate
        self.pending = pending

    def _persist(self) -> None:
        if self.store is not None:
            self.store.save_sandbox(self)

    def save(self) -> None:
        self._persist()

    def set_tier(self, tier: int) -> None:
        if tier not in (0, 1, 2):
            raise ValueError("tier must be 0, 1 or 2")
        self.tier = tier
        self._persist()

    def _new_id(self) -> str:
        self._n += 1
        return f"T{self._n:04d}"

    def _apply(self, q: Quote) -> None:
        self.pkr_balance -= q.total_pkr
        self.cny_balance += q.cny_to_deliver

    def _finish(self, tid, q, status, reasons, event="request") -> dict:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "transfer_id": tid,
            "tier": self.tier,
            "route": q.route,
            "cny_to_deliver": str(q.cny_to_deliver),
            "total_pkr": str(q.total_pkr),
            "offered_rate": str(q.offered_rate),
            "status": status,
            "reasons": list(reasons),
            "pkr_balance_after": str(self.pkr_balance),
            "cny_balance_after": str(self.cny_balance),
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        self._persist()
        return {"transfer_id": tid, "status": status, "reasons": list(reasons)}

    def request_transfer(self, q: Quote) -> dict:
        d = evaluate(self.mandate, self.tier, q.route,
                     q.cny_to_deliver, q.offered_rate)
        tid = self._new_id()
        if d.outcome == BLOCKED:
            return self._finish(tid, q, "blocked", d.reasons)
        if q.total_pkr > self.pkr_balance:
            return self._finish(tid, q, "insufficient_funds",
                                ("balance too low",))
        if d.outcome == ALLOW_EXECUTE:
            self._apply(q)
            return self._finish(tid, q, "executed", d.reasons)
        self.pending[tid] = q
        return self._finish(tid, q, "pending_confirmation", d.reasons)

    def confirm(self, tid: str) -> dict:
        if tid not in self.pending:
            raise ValueError(f"no pending transfer {tid}")
        q = self.pending.pop(tid)
        if q.total_pkr > self.pkr_balance:
            return self._finish(tid, q, "insufficient_funds",
                                ("balance too low at confirmation",),
                                event="confirm")
        self._apply(q)
        return self._finish(tid, q, "executed", ("confirmed by user",),
                            event="confirm")

    def reject(self, tid: str) -> dict:
        if tid not in self.pending:
            raise ValueError(f"no pending transfer {tid}")
        q = self.pending.pop(tid)
        return self._finish(tid, q, "rejected", ("rejected by user",),
                            event="reject")