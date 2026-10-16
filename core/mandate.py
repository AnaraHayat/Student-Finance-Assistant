from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

BLOCKED = "blocked"
NEEDS_CONFIRMATION = "needs_confirmation"
ALLOW_EXECUTE = "allow_execute"


@dataclass(frozen=True)
class Mandate:
    max_transfer_cny: Decimal        # hard cap per transfer, in CNY delivered
    auto_execute_limit_cny: Decimal  # tier 2 may act alone up to this
    max_pkr_per_cny: Decimal         # refuse routes offering a worse rate
    allowed_routes: Optional[frozenset] = None  # None = any route

    def __post_init__(self):
        if self.max_transfer_cny <= 0:
            raise ValueError("max_transfer_cny must be positive")
        if self.auto_execute_limit_cny < 0:
            raise ValueError("auto_execute_limit_cny cannot be negative")
        if self.auto_execute_limit_cny > self.max_transfer_cny:
            raise ValueError("auto_execute_limit_cny cannot exceed max_transfer_cny")
        if self.max_pkr_per_cny <= 0:
            raise ValueError("max_pkr_per_cny must be positive")


@dataclass(frozen=True)
class Decision:
    outcome: str
    reasons: tuple


def evaluate(mandate: Mandate, tier: int, route_name: str,
             cny_to_deliver: Decimal, offered_rate: Decimal) -> Decision:
    if tier not in (0, 1, 2):
        raise ValueError("tier must be 0, 1 or 2")
    if cny_to_deliver <= 0:
        raise ValueError("cny_to_deliver must be positive")
    if offered_rate <= 0:
        raise ValueError("offered_rate must be positive")

    if tier == 0:
        return Decision(BLOCKED, ("tier 0 is read-only: no actions allowed",))

    reasons = []
    if (mandate.allowed_routes is not None
            and route_name not in mandate.allowed_routes):
        reasons.append(f"route '{route_name}' is not in the allowed routes")
    if cny_to_deliver > mandate.max_transfer_cny:
        reasons.append(
            f"{cny_to_deliver} CNY exceeds the maximum transfer of "
            f"{mandate.max_transfer_cny} CNY")
    if offered_rate > mandate.max_pkr_per_cny:
        reasons.append(
            f"offered rate {offered_rate} PKR/CNY is worse than the limit "
            f"of {mandate.max_pkr_per_cny}")
    if reasons:
        return Decision(BLOCKED, tuple(reasons))

    if tier == 1:
        return Decision(NEEDS_CONFIRMATION,
                        ("tier 1: drafts only, user must confirm",))
    if cny_to_deliver <= mandate.auto_execute_limit_cny:
        return Decision(ALLOW_EXECUTE,
                        ("tier 2: within the auto-execute limit",))
    return Decision(NEEDS_CONFIRMATION,
                    ("tier 2: above the auto-execute limit, user must confirm",))