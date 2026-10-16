from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

_2DP = Decimal("0.01")
_4DP = Decimal("0.0001")


def _r(x: Decimal, step: Decimal) -> Decimal:
    return x.quantize(step, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Expense:
    name: str
    on: date
    amount_cny: Decimal
    category: str

    def __post_init__(self):
        if self.amount_cny <= 0:
            raise ValueError("amount_cny must be positive")
        if not self.category.strip():
            raise ValueError("category cannot be empty")


@dataclass(frozen=True)
class CategoryTotal:
    category: str
    cny: Decimal
    pkr_mid: Decimal
    pkr_effective: Decimal
    share: Decimal  # fraction of the month's CNY total


@dataclass(frozen=True)
class Summary:
    year: int
    month: int
    count: int
    total_cny: Decimal
    total_pkr_mid: Decimal
    total_pkr_effective: Decimal
    hidden_cost_pkr: Decimal
    categories: tuple  # CategoryTotal, largest first


def summarize(expenses, year: int, month: int, mid_rate: Decimal,
              effective_rate: Optional[Decimal] = None) -> Summary:
    if not 1 <= month <= 12:
        raise ValueError("month must be 1 to 12")
    if mid_rate <= 0:
        raise ValueError("mid_rate must be positive")
    eff = mid_rate if effective_rate is None else effective_rate
    if eff <= 0:
        raise ValueError("effective_rate must be positive")

    totals = {}
    count = 0
    for e in expenses:
        if e.on.year == year and e.on.month == month:
            key = e.category.strip().lower()
            totals[key] = totals.get(key, Decimal("0")) + e.amount_cny
            count += 1

    total = sum(totals.values(), Decimal("0"))
    cats = []
    for name, cny in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])):
        cats.append(CategoryTotal(
            category=name,
            cny=cny,
            pkr_mid=_r(cny * mid_rate, _2DP),
            pkr_effective=_r(cny * eff, _2DP),
            share=_r(cny / total, _4DP),
        ))

    return Summary(
        year=year, month=month, count=count,
        total_cny=total,
        total_pkr_mid=_r(total * mid_rate, _2DP),
        total_pkr_effective=_r(total * eff, _2DP),
        hidden_cost_pkr=_r(total * (eff - mid_rate), _2DP),
        categories=tuple(cats),
    )