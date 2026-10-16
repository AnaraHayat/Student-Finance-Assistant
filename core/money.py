from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

_2DP = Decimal("0.01")
_4DP = Decimal("0.0001")


def _round(x: Decimal, step: Decimal) -> Decimal:
    return x.quantize(step, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Route:
    name: str
    markup: Decimal             # spread over mid-market, e.g. 0.02 = 2%
    fixed_fee_pkr: Decimal      # flat fee charged in PKR
    pct_fee: Decimal            # fee as a fraction of the converted PKR amount
    recipient_fee_cny: Decimal  # fee deducted on the China side, in CNY
    source: str                 # URL of the page the numbers came from
    as_of: str                  # date the numbers were checked
    route_type: str = "unspecified"   # e.g. bank_transfer, remittance_service
    typical_days: Optional[int] = None
    supports_tuition: bool = False


@dataclass(frozen=True)
class Quote:
    route: str
    cny_to_deliver: Decimal
    cny_to_send: Decimal
    offered_rate: Decimal       # PKR per CNY the route applies
    total_pkr: Decimal
    effective_rate: Decimal     # total PKR / CNY delivered
    hidden_cost_pkr: Decimal    # total minus the mid-market cost
    hidden_cost_pct: Decimal    # as a fraction of the mid-market cost


def quote(route: Route, cny_to_deliver: Decimal, mid_rate: Decimal) -> Quote:
    if cny_to_deliver <= 0:
        raise ValueError("cny_to_deliver must be positive")
    if mid_rate <= 0:
        raise ValueError("mid_rate must be positive")

    send = cny_to_deliver + route.recipient_fee_cny
    offered = mid_rate * (Decimal(1) + route.markup)
    base = send * offered
    total = base + route.fixed_fee_pkr + route.pct_fee * base

    benchmark = cny_to_deliver * mid_rate
    hidden = total - benchmark

    return Quote(
        route=route.name,
        cny_to_deliver=cny_to_deliver,
        cny_to_send=send,
        offered_rate=_round(offered, _4DP),
        total_pkr=_round(total, _2DP),
        effective_rate=_round(total / cny_to_deliver, _4DP),
        hidden_cost_pkr=_round(hidden, _2DP),
        hidden_cost_pct=_round(hidden / benchmark, _4DP),
    )