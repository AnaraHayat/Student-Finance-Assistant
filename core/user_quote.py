from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_2DP = Decimal("0.01")
_4DP = Decimal("0.0001")


def _round(x: Decimal, step: Decimal) -> Decimal:
    return x.quantize(step, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class UserQuote:
    provider: str
    pkr_paid: Decimal
    cny_received: Decimal
    mid_rate: Decimal
    effective_rate: Decimal
    hidden_cost_pkr: Decimal
    hidden_cost_pct: Decimal


def analyze_quote(provider: str, pkr_paid: Decimal, cny_received: Decimal,
                  mid_rate: Decimal) -> UserQuote:
    """pkr_paid must be the TOTAL the user pays, all fees included."""
    if not provider or not provider.strip():
        raise ValueError("provider name is required")
    if pkr_paid <= 0:
        raise ValueError("pkr_paid must be positive")
    if cny_received <= 0:
        raise ValueError("cny_received must be positive")
    if mid_rate <= 0:
        raise ValueError("mid_rate must be positive")

    benchmark = cny_received * mid_rate
    hidden = pkr_paid - benchmark
    return UserQuote(
        provider=provider.strip(),
        pkr_paid=pkr_paid,
        cny_received=cny_received,
        mid_rate=mid_rate,
        effective_rate=_round(pkr_paid / cny_received, _4DP),
        hidden_cost_pkr=_round(hidden, _2DP),
        hidden_cost_pct=_round(hidden / benchmark, _4DP),
    )
