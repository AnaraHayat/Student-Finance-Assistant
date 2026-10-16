from decimal import Decimal
from pathlib import Path

from core.rates import get_rate
from core.routes import compare, load_routes

rate = get_rate()
print(f"Mid-market rate: 1 CNY = {rate.pkr_per_cny} PKR")
print(f"As of: {rate.as_of} | from cache: {rate.from_cache}")

routes = load_routes(Path(__file__).parent / "data" / "fees.json")
amount = Decimal("8000")
print(f"\nTo deliver: {amount} CNY\n")

for q in compare(routes, amount, rate.pkr_per_cny):
    print(f"{q.route:22} total {q.total_pkr:>12,} PKR | "
          f"effective {q.effective_rate} | "
          f"hidden {q.hidden_cost_pkr:,} PKR ({q.hidden_cost_pct * 100:.2f}%)")