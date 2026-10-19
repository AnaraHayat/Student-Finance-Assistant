# StudentFX — Student Finance Assistant

A finance assistant for a Pakistani student studying in China. It helps you:

- **Compare ways to send money** from PKR to CNY and see the real cost, including the hidden markup over the mid-market rate.
- **Track spending** in both CNY and PKR, grouped by category and month.
- **Forecast cash flow** to see when your CNY balance will run short and when you must transfer by.
- **Ask an AI assistant** to do these things for you, with strict limits on what it is allowed to do.

> **Sandbox only.** No real money moves. Balances are fake and every action is written to an audit log.

> **Fee data is partly sourced.** `data/fees.json` lists real providers (ICBC Pakistan, Bank Alfalah) with published fixed/percentage fees and a source link and date. Banks do not publish the exchange-rate markup over mid-market, so `markup` is set to 0 and every route is flagged **illustrative**. Fix the markup from your own quote before trusting the ranking (see below).

## How it works

| Folder | What it does |
|---|---|
| `core/` | The maths: quotes, live rate (with cache), spending, forecast, safety limits (mandate). Uses `Decimal`, never floats. |
| `sandbox/` | Fake PKR/CNY wallets, pending transfers, and the audit log. |
| `agent/` | The AI agent (Gemini) and its 8 tools, plus a mock model for demo mode. |
| `store.py` | SQLite persistence for wallets, pending transfers, expenses, events and quotes. |
| `server.py` | FastAPI backend. |
| `web/` | The web page (HTML + JS). |
| `tests/` | 143 automated tests. |

### Permission tiers

| Tier | What the AI can do |
|---|---|
| 0 | Read-only. No actions. |
| 1 | Drafts transfers. You must press **Confirm**. |
| 2 | Can send alone, but only up to your auto-execute limit. Bigger transfers need your confirmation. |

Whatever the tier, a transfer is **blocked** if it breaks your limits (max transfer, worst acceptable rate, allowed routes). The AI has no tool to confirm, reject, or change limits. Only you can, through the UI.

## Run it

You need Python 3.10+.

```bash
# 1. Get the code
git clone https://github.com/AnaraHayat/Student-Finance-Assistant.git
cd Student-Finance-Assistant

# 2. (Optional) virtual environment
python -m venv .venv

# 3. Activate it — pick the line for your shell:
.venv\Scripts\Activate.ps1        # Windows PowerShell
# .venv\Scripts\activate.bat      # Windows cmd.exe
# source .venv/bin/activate       # macOS / Linux

# 4. Install
pip install -r requirements.txt

# 5. Start the app
uvicorn server:app --reload
```

Open **http://127.0.0.1:8000**.

**Windows notes.** Use the Windows `py` launcher if plain `python` is not found: `py -3.12 -m venv .venv`. If PowerShell refuses `Activate.ps1` ("running scripts is disabled on this system"), run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` once in that window, or use `cmd.exe` and `.venv\Scripts\activate.bat`. You can also skip activation entirely and call the venv's Python directly, e.g. `.venv\Scripts\python.exe -m uvicorn server:app --reload`.

**First run needs internet.** The app fetches the PKR/CNY rate from open.er-api.com and saves it to `data/rate_cache.json`. After that it falls back to the cached rate if you're offline. With no internet and no cache, startup fails with a plain message telling you to connect once.

### Turn on the AI (optional)

Without an API key the app runs in **demo mode** using a simple mock assistant. To use Gemini:

1. Copy `.env.example` to `.env`.
2. Fill in your key:
   ```
   LLM_PROVIDER=gemini
   LLM_API_KEY=your_key_here
   ```
3. Optional settings: `LLM_MODEL` (default `gemini-3.8-flash`), `LLM_FALLBACK_MODEL`, `LLM_TIMEOUT`, `LLM_FALLBACK_TIMEOUT`.
4. Restart the app. The page shows which mode is active.

Optional safety: set `LOCAL_ONLY=1` in `.env` to make the server refuse any request that does not come from this machine. It is off by default.

## Security

This is a sandbox, but the finance controls are enforced in code, not by the AI. What is in place:

- **Access token login.** Set `ACCESS_TOKEN` in `.env` to a long random value to require sign-in before any `/api` call. With it set, the browser shows a lock screen, the session is an HMAC-signed `HttpOnly` cookie, and writes require a CSRF token. Leave it unset only for a throwaway local demo. Generate one with:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```
- **Idle timeout.** `SESSION_TTL` (default 1800 seconds) signs you out after inactivity, and every session has an 8-hour absolute cap. **Lock** in the header ends the session immediately.
- **Rate limits.** Chat has its own limit, and every state-changing API call is limited per client.
- **Input caps.** Request bodies, tool arguments, tool names and free-text fields are length-limited.
- **Headers and hosts.** The app sends a strict Content-Security-Policy plus `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` and cross-origin isolation headers. Only the hosts in `ALLOWED_HOSTS` are answered (default `127.0.0.1,localhost`).
- **Tamper-evident audit log.** Every audit entry carries a SHA-256 hash of the previous entry. On startup the chain is checked and the result appears under *Data & trust*; an edit to the file is detected.
- **Safer failures.** Unexpected errors no longer expose exception types to the browser, and security events (failed logins, bad CSRF tokens, rate-limit hits) go to `logs/security.jsonl`.
- **Dependency auditing.** CI runs `pip-audit` against `requirements.txt` on every push.
- **Persistence.** SQLite queries are parameterized, money is stored as text and kept in `Decimal`, `.env` and the database are git-ignored, and file permissions are tightened to owner-only on POSIX.

**HTTPS.** The app itself speaks HTTP. When you put it behind TLS, either terminate TLS in a reverse proxy (Caddy, nginx) or run:

```bash
uvicorn server:app --ssl-keyfile key.pem --ssl-certfile cert.pem
```

When the request is HTTPS, the session cookie is marked `Secure` and HSTS is sent. Do not expose the app to the internet without TLS and a real `ACCESS_TOKEN`.

## Use it

The page has a sidebar with your wallets, permission tier and limits, plus these tabs:

- **Compare**: cost of each route for an amount of CNY.
- **Spending**: add expenses and see the monthly summary.
- **Forecast**: add upcoming bills or income, then run a forecast.
- **My Quotes**: type in what a provider actually charged you to see the hidden cost.
- **Activity**: the audit log.

Try asking the assistant: *"I need 8000 yuan for tuition. What's the cheapest way and what will it cost in PKR?"*

## Add real fee data

Edit `data/fees.json`. One entry per route:

```json
{
  "name": "Example Bank wire",
  "markup": "0.015",
  "fixed_fee_pkr": "1500",
  "pct_fee": "0",
  "recipient_fee_cny": "0",
  "source": "https://link-to-the-page-with-these-fees",
  "as_of": "2026-10-16",
  "route_type": "bank_transfer",
  "typical_days": 3,
  "supports_tuition": true
}
```

- `markup`: spread over the mid-market rate (0.015 = 1.5%).
- `pct_fee`: fee as a fraction of the converted PKR amount.
- `source` and `as_of`: where and when you checked. Keep these honest. The app flags any route whose source or date says "placeholder", "illustrative" or "not checked".
- Use strings for the numbers.

## Run the tests

```bash
pytest
```

The `check_*.py` files in the root are manual scripts for trying the rate API, the Gemini connection, speed, and the agent. Most need a valid API key.

## Known limits

- Only the PKR → CNY corridor.
- Transfers are simulated.
- Wallets, pending transfers, spending, events and quotes are saved to `data/studentfx.db` (SQLite) and survive a restart. The rate cache and audit log are files.