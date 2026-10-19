from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

OBLIGATION = "obligation"
INFLOW = "inflow"


@dataclass(frozen=True)
class Event:
    name: str
    on: date
    amount_cny: Decimal
    kind: str  # "obligation" (money out) or "inflow" (money in)

    def __post_init__(self):
        if self.kind not in (OBLIGATION, INFLOW):
            raise ValueError("kind must be 'obligation' or 'inflow'")
        if self.amount_cny <= 0:
            raise ValueError("amount_cny must be positive")


@dataclass(frozen=True)
class Forecast:
    daily: tuple                      # ((date, end-of-day balance), ...)
    min_balance: Decimal
    min_balance_date: date
    first_shortfall_date: date | None
    topup_needed_cny: Decimal
    transfer_by: date | None
    is_late: bool


def forecast(start_balance: Decimal, start: date, end: date, events,
             buffer: Decimal = Decimal("0"), lead_days: int = 0) -> Forecast:
    if end < start:
        raise ValueError("end must not be before start")
    if buffer < 0:
        raise ValueError("buffer cannot be negative")
    if lead_days < 0:
        raise ValueError("lead_days cannot be negative")
    for e in events:
        if e.on < start:
            raise ValueError(f"event '{e.name}' is before the start date")

    net = {}
    for e in events:
        if e.on > end:
            continue
        delta = e.amount_cny if e.kind == INFLOW else -e.amount_cny
        net[e.on] = net.get(e.on, Decimal("0")) + delta

    daily = []
    balance = start_balance
    day = start
    while day <= end:
        balance += net.get(day, Decimal("0"))
        daily.append((day, balance))
        day += timedelta(days=1)

    min_date, min_bal = daily[0]
    first_short = None
    for d, b in daily:
        if b < min_bal:
            min_date, min_bal = d, b
        if first_short is None and b < buffer:
            first_short = d

    if first_short is None:
        return Forecast(tuple(daily), min_bal, min_date, None,
                        Decimal("0"), None, False)

    latest = first_short - timedelta(days=lead_days)
    return Forecast(tuple(daily), min_bal, min_date, first_short,
                    buffer - min_bal, max(latest, start), latest < start)
