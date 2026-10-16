import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import requests

URL = "https://open.er-api.com/v6/latest/CNY"
CACHE = Path(__file__).resolve().parent.parent / "data" / "rate_cache.json"


@dataclass(frozen=True)
class Rate:
    pkr_per_cny: Decimal
    as_of: str
    next_update: str
    source: str
    from_cache: bool


def parse_response(d: dict) -> Rate:
    if d.get("result") != "success":
        raise ValueError("rate API did not return success")
    pkr = Decimal(str(d["rates"]["PKR"]))
    if pkr <= 0:
        raise ValueError("rate must be positive")
    return Rate(pkr, d["time_last_update_utc"], d["time_next_update_utc"],
                URL, False)


def get_rate() -> Rate:
    try:
        r = requests.get(URL, timeout=15)
        r.raise_for_status()
        rate = parse_response(r.json())
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps({
            "pkr_per_cny": str(rate.pkr_per_cny),
            "as_of": rate.as_of,
            "next_update": rate.next_update,
        }), encoding="utf-8")
        return rate
    except (requests.RequestException, ValueError, KeyError) as e:
        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as cache_error:
            raise FileNotFoundError(
                "No saved exchange rate and the live rate could not be "
                f"fetched ({type(e).__name__}). Connect to the internet once "
                f"so the rate can be saved to {CACHE}, then start again."
            ) from cache_error
        return Rate(Decimal(cached["pkr_per_cny"]), cached["as_of"],
                    cached["next_update"], URL, True)