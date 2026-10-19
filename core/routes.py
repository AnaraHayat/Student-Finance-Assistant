import json
from decimal import Decimal
from pathlib import Path

from core.money import Quote, Route, quote


def load_routes(path) -> list[Route]:
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    return [
        Route(
            name=i["name"],
            markup=Decimal(i["markup"]),
            fixed_fee_pkr=Decimal(i["fixed_fee_pkr"]),
            pct_fee=Decimal(i["pct_fee"]),
            recipient_fee_cny=Decimal(i["recipient_fee_cny"]),
            source=i["source"],
            as_of=i["as_of"],
            route_type=i.get("route_type", "unspecified"),
            typical_days=i.get("typical_days"),
            supports_tuition=bool(i.get("supports_tuition", False)),
        )
        for i in items
    ]


def compare(routes: list[Route], cny_to_deliver: Decimal,
            mid_rate: Decimal, route_type=None) -> list[Quote]:
    chosen = [r for r in routes
              if route_type is None or r.route_type == route_type]
    quotes = [quote(r, cny_to_deliver, mid_rate) for r in chosen]
    return sorted(quotes, key=lambda q: q.total_pkr)
