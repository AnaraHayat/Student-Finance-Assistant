"""Generate execution evidence for the submission (requirement 06).

It drives a scripted session through the real ASGI app -- real middleware,
real sandbox ledger, real hash-chained audit log -- and writes the results to
``docs/evidence/``:

- ``session-transcript.txt``  every HTTP request and response
- ``audit-log.jsonl``        the tamper-evident audit entries produced
- ``audit-verification.txt`` result of verifying the hash chain
- ``security-events.txt``    security log lines produced during the run
- ``environment.txt``        interpreter / platform / time of the run

Run it with the project virtual environment:

    .venv\\Scripts\\python.exe scripts\\capture_evidence.py
"""

import json
import platform
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from agent.loop import Agent  # noqa: E402
from agent.mock import MockModel  # noqa: E402
from agent.tools import ToolBox  # noqa: E402
from audit import verify as verify_audit  # noqa: E402
from core.mandate import Mandate  # noqa: E402
from core.rates import get_rate  # noqa: E402
from core.routes import load_routes  # noqa: E402
from sandbox.ledger import Sandbox  # noqa: E402
from server import AppState, create_app  # noqa: E402

EVIDENCE = ROOT / "docs" / "evidence"
SECURITY_LOG = ROOT / "logs" / "security.jsonl"
TOKEN = "demo-token-not-a-real-secret"


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    audit_path = EVIDENCE / "audit-log.jsonl"
    if audit_path.exists():
        audit_path.unlink()

    security_before = ""
    if SECURITY_LOG.exists():
        security_before = SECURITY_LOG.read_text(encoding="utf-8")

    rate = get_rate()
    routes = load_routes(ROOT / "data" / "fees.json")
    mandate = Mandate(D("50000"), D("0"), D("60"))
    sandbox = Sandbox(D("2000000"), mandate, 1, audit_path)
    toolbox = ToolBox(sandbox, routes, rate)
    st = AppState(sandbox, toolbox, Agent(MockModel(), toolbox),
                  rate, routes, "demo (mock model)")
    app = create_app(st, access_token=TOKEN, local_only=False,
                     allowed_hosts=["testserver"])
    client = TestClient(app)

    lines = []
    counter = {"n": 0}

    def record(title, response):
        counter["n"] += 1
        try:
            body = json.dumps(response.json(), indent=2, ensure_ascii=False)
        except Exception:
            body = response.text
        lines.append(f"[{counter['n']:02d}] {title}")
        lines.append(f"     HTTP {response.status_code}")
        lines.append("     " + body.replace("\n", "\n     "))
        lines.append("")

    def csrf():
        return client.cookies.get("sfx_csrf")

    def post(path, payload):
        return client.post(path, json=payload,
                           headers={"X-CSRF-Token": csrf()})

    today = date.today()
    route = routes[0].name

    lines.append("StudentFX -- sandbox execution evidence")
    lines.append(f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Python: {platform.python_version()}  Platform: {platform.platform()}")
    lines.append(f"Mid-market rate: 1 CNY = {rate.pkr_per_cny} PKR "
                 f"(source: {rate.source}, as of {rate.as_of})")
    lines.append(f"Routes loaded from data/fees.json: {[r.name for r in routes]}")
    lines.append("=" * 72)
    lines.append("")

    record("GET /api/state (no session -- must be rejected)",
           client.get("/api/state"))
    record("POST /api/login with the access token",
           client.post("/api/login", json={"token": TOKEN}))
    record("GET /api/state (authenticated)",
           client.get("/api/state"))
    record("POST /api/tool compare_routes 8000 CNY",
           post("/api/tool", {"name": "compare_routes",
                              "args": {"cny_to_deliver": "8000"}}))
    record(f"POST /api/tool request_transfer {route} 3000 CNY (tier 1 -> pending)",
           post("/api/tool", {"name": "request_transfer",
                              "args": {"route_name": route,
                                       "cny_to_deliver": "3000"}}))
    record("POST /api/confirm/T0001 (user confirms)",
           post("/api/confirm/T0001", {}))
    record(f"POST /api/tool request_transfer {route} 5000 CNY (-> pending)",
           post("/api/tool", {"name": "request_transfer",
                              "args": {"route_name": route,
                                       "cny_to_deliver": "5000"}}))
    record("POST /api/reject/T0002 (user rejects)",
           post("/api/reject/T0002", {}))
    record("POST /api/tool add_expense lunch today",
           post("/api/tool", {"name": "add_expense",
                              "args": {"name": "lunch",
                                       "on": today.isoformat(),
                                       "amount_cny": "25",
                                       "category": "food"}}))
    record(f"POST /api/tool spending_summary {today.year}-{today.month:02d}",
           post("/api/tool", {"name": "spending_summary",
                              "args": {"year": today.year,
                                       "month": today.month}}))
    record("POST /api/tool add_event rent in 5 days",
           post("/api/tool", {"name": "add_event",
                              "args": {"name": "rent",
                                       "on": (today + timedelta(days=5)).isoformat(),
                                       "amount_cny": "1800",
                                       "kind": "obligation"}}))
    record("POST /api/tool forecast_cashflow (next 30 days)",
           post("/api/tool", {"name": "forecast_cashflow",
                              "args": {"start": today.isoformat(),
                                       "end": (today + timedelta(days=30)).isoformat(),
                                       "buffer_cny": "0",
                                       "lead_days": 3}}))
    record("POST /api/quotes (analyse a real quote: 100000 PKR -> 2350 CNY)",
           post("/api/quotes", {"provider": "Bank X",
                                "pkr_paid": "100000",
                                "cny_received": "2350"}))
    record("POST /api/chat 'cheapest way to send 9000 CNY'",
           post("/api/chat", {"message": "cheapest way to send 9000 CNY"}))
    record("GET /api/log (the audit trail)",
           client.get("/api/log"))
    record("POST /api/logout",
           post("/api/logout", {}))
    record("GET /api/state after logout (must be rejected again)",
           client.get("/api/state"))

    (EVIDENCE / "session-transcript.txt").write_text(
        "\n".join(lines), encoding="utf-8")

    ok, bad = verify_audit(audit_path)
    entries = sum(1 for ln in audit_path.read_text(encoding="utf-8").splitlines()
                  if ln.strip())
    (EVIDENCE / "audit-verification.txt").write_text(
        "audit-log.jsonl hash-chain verification\n"
        f"entries: {entries}\n"
        f"result: {'OK (chain intact)' if ok else f'BROKEN at line {bad}'}\n",
        encoding="utf-8")

    after = SECURITY_LOG.read_text(encoding="utf-8") if SECURITY_LOG.exists() else ""
    new_security = after[len(security_before):].strip()
    (EVIDENCE / "security-events.txt").write_text(
        new_security or "(no security events during this run)\n", encoding="utf-8")

    (EVIDENCE / "environment.txt").write_text(
        f"Python: {platform.python_version()}\n"
        f"Platform: {platform.platform()}\n"
        f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}\n",
        encoding="utf-8")

    print(f"wrote evidence to {EVIDENCE}")
    print(f"audit chain: {'OK' if ok else 'BROKEN'} ({entries} entries)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
