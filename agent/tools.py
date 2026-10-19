import threading
from datetime import date
from decimal import Decimal, InvalidOperation

from core.forecast import INFLOW, OBLIGATION, Event, forecast
from core.money import quote
from core.routes import compare
from core.spending import Expense, summarize


def _d(x) -> Decimal:
    text = str(x).strip()
    if len(text) > 48:
        raise ValueError("number is too long")
    try:
        v = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"not a number: {x!r}")
    if not v.is_finite():
        raise ValueError(f"not a finite number: {x!r}")
    # Bound the exponent so a huge value cannot make arithmetic slow.
    if v != 0 and not -15 <= v.adjusted() <= 15:
        raise ValueError("number is out of range")
    return v


def _text(value, field, limit=120) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} cannot be empty")
    if len(text) > limit:
        raise ValueError(f"{field} is too long (max {limit} characters)")
    return text


class ToolBox:
    """The only functions the AI may call. There is deliberately no
    confirm, reject or mandate change here: only a human (UI) may do those."""

    def __init__(self, sandbox, routes, rate, store=None):
        self.sandbox = sandbox
        self.routes = routes
        self.rate = rate
        self.expenses = []
        self.events = []
        self.last_effective_rate = None
        self.store = store
        self.lock = threading.RLock()
        self._handlers = {
            "get_rate": self._get_rate,
            "get_balances": self._get_balances,
            "compare_routes": self._compare_routes,
            "request_transfer": self._request_transfer,
            "add_expense": self._add_expense,
            "spending_summary": self._spending_summary,
            "add_event": self._add_event,
            "forecast_cashflow": self._forecast,
        }

    @property
    def tool_names(self):
        return tuple(sorted(self._handlers))

    def load(self, *, expenses, events) -> None:
        """Restore saved collections (used by the Store on startup)."""
        self.expenses = list(expenses)
        self.events = list(events)

    def call(self, name, args=None):
        args = args or {}
        if name not in self._handlers:
            return {"error": f"unknown tool '{name}'"}
        with self.lock:
            try:
                return self._handlers[name](**args)
            except (ValueError, KeyError, TypeError, InvalidOperation) as e:
                return {"error": str(e)}

    def _get_rate(self):
        return {
            "pkr_per_cny": str(self.rate.pkr_per_cny),
            "as_of": self.rate.as_of,
            "from_cache": self.rate.from_cache,
            "note": "mid-market reference rate, not a rate any route offers",
        }

    def _get_balances(self):
        return {
            "pkr_balance": str(self.sandbox.pkr_balance),
            "cny_balance": str(self.sandbox.cny_balance),
            "tier": self.sandbox.tier,
        }

    def _compare_routes(self, cny_to_deliver, route_type=None):
        amount = _d(cny_to_deliver)
        if route_type is not None:
            route_type = _text(route_type, "route_type", 40)
        by_name = {r.name: r for r in self.routes}
        qs = compare(self.routes, amount, self.rate.pkr_per_cny, route_type)
        return {
            "mid_rate": str(self.rate.pkr_per_cny),
            "quotes": [{
                "route": q.route,
                "route_type": by_name[q.route].route_type,
                "typical_days": by_name[q.route].typical_days,
                "supports_tuition": by_name[q.route].supports_tuition,
                "cny_to_send": str(q.cny_to_send),
                "offered_rate": str(q.offered_rate),
                "total_pkr": str(q.total_pkr),
                "effective_rate": str(q.effective_rate),
                "hidden_cost_pkr": str(q.hidden_cost_pkr),
                "hidden_cost_pct": str(q.hidden_cost_pct),
                "fee_source": by_name[q.route].source,
                "fees_as_of": by_name[q.route].as_of,
            } for q in qs],
        }

    def _request_transfer(self, route_name, cny_to_deliver):
        amount = _d(cny_to_deliver)
        route_name = _text(route_name, "route_name")
        route = next((r for r in self.routes if r.name == route_name), None)
        if route is None:
            raise ValueError(f"unknown route '{route_name}'")
        q = quote(route, amount, self.rate.pkr_per_cny)
        result = self.sandbox.request_transfer(q)
        if result["status"] == "executed":
            self.last_effective_rate = q.effective_rate
        result["total_pkr"] = str(q.total_pkr)
        return result

    def _add_expense(self, name, on, amount_cny, category):
        self.expenses.append(
            Expense(_text(name, "name"), date.fromisoformat(on),
                    _d(amount_cny), _text(category, "category", 60)))
        if self.store is not None:
            self.store.save_expenses(self.expenses)
        return {"ok": True, "expense_count": len(self.expenses)}

    def _spending_summary(self, year, month):
        s = summarize(self.expenses, int(year), int(month),
                      self.rate.pkr_per_cny, self.last_effective_rate)
        return {
            "year": s.year, "month": s.month, "count": s.count,
            "total_cny": str(s.total_cny),
            "total_pkr_mid": str(s.total_pkr_mid),
            "total_pkr_effective": str(s.total_pkr_effective),
            "hidden_cost_pkr": str(s.hidden_cost_pkr),
            "categories": [{
                "category": c.category, "cny": str(c.cny),
                "pkr_mid": str(c.pkr_mid),
                "pkr_effective": str(c.pkr_effective),
                "share": str(c.share),
            } for c in s.categories],
        }

    def _add_event(self, name, on, amount_cny, kind):
        self.events.append(
            Event(_text(name, "name"), date.fromisoformat(on), _d(amount_cny),
                  _text(kind, "kind", 20)))
        if self.store is not None:
            self.store.save_events(self.events)
        return {"ok": True, "event_count": len(self.events)}

    def _forecast(self, start, end, start_balance_cny=None,
                  buffer_cny="0", lead_days=0):
        bal = (self.sandbox.cny_balance if start_balance_cny is None
               else _d(start_balance_cny))
        f = forecast(bal, date.fromisoformat(start), date.fromisoformat(end),
                     self.events, _d(buffer_cny), int(lead_days))
        return {
            "min_balance_cny": str(f.min_balance),
            "min_balance_date": f.min_balance_date.isoformat(),
            "first_shortfall_date": (f.first_shortfall_date.isoformat()
                                     if f.first_shortfall_date else None),
            "topup_needed_cny": str(f.topup_needed_cny),
            "transfer_by": f.transfer_by.isoformat() if f.transfer_by else None,
            "is_late": f.is_late,
        }