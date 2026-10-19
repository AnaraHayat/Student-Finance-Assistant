# Security Self-Assessment Report — StudentFX

This is my own review of the project's security: what each actor is allowed to
do and how that is enforced, followed by an honest list of known weaknesses.
The scope is a **local, single-user sandbox** — no real money and no personal
data beyond a fake wallet — but the controls are designed as if it mattered,
because the point of the project is safe handling of money and access.

Cross-reference: the mechanics are explained in
[01-technical-documentation.md](01-technical-documentation.md#3-security-design).

---

## 1. Permission tiers

### 1.1 The human user

There is one human role (the student). There is no admin role and no
multi-user account system; access is gated by a single shared `ACCESS_TOKEN`
that the user sets in `.env`. When the token is set, every `/api/*` route
except `/api/login` needs a valid signed session, and every state-changing
route additionally needs the CSRF header.

The user, once signed in, can:

| Action | Endpoint | Notes |
|---|---|---|
| See balances, limits, pending drafts, routes | `GET /api/state` | read-only |
| Chat with the assistant | `POST /api/chat` | rate limited 30/min |
| Call any whitelisted tool directly | `POST /api/tool` | rate limited 120/min |
| **Confirm** a pending transfer | `POST /api/confirm/{id}` | the only way funds move |
| **Reject** a pending transfer | `POST /api/reject/{id}` | |
| Change the **tier** | `POST /api/tier` | 0 / 1 / 2 |
| Change the **mandate limits** | `POST /api/mandate` | audited as `mandate_change` |
| Analyse a received quote | `POST /api/quotes` | |

Because it is a single-user app, "admin" actions (changing tiers and limits)
are held by the same human. That is an accepted limitation, listed in §2.

### 1.2 The AI assistant

The assistant is treated as an **untrusted, potentially compromised client**.
It may only call the eight tools in `agent/tools.py`'s whitelist. There is
deliberately **no tool** for confirming, rejecting, changing the tier, or
changing the mandate, so a jailbroken or prompt-injected model still cannot
move funds or widen its own limits. Its most powerful action is
`request_transfer`, which can only ever produce a *pending* draft (or a
blocked/insufficient-funds result), never a completed transfer.

Enforcement points:

- Whitelist lookup in `ToolBox.call` — unknown tool names return an error, they
  are not dispatched.
- The tool methods themselves validate and type-check arguments.
- The system prompt forbids inventing numbers and forbids retrying a blocked
  action with a changed amount.
- Prompt-injection defence: the model is told to treat message and tool text as
  data, and in any case **all numbers and decisions are computed in code**,
  not by the model.

### 1.3 The tier system (fund authority)

The acting tier is enforced by `core/mandate.evaluate`, not by the caller:

| Tier | Allowed |
|---|---|
| 0 | Read-only. Every transfer request is `BLOCKED`. |
| 1 | Draft-only. Valid requests become `needs_confirmation`; the human confirms. |
| 2 | Auto-executes only up to `auto_execute_limit_cny`; above that, needs confirmation. |

In every tier the hard `max_transfer_cny` and `max_pkr_per_cny` limits apply
first: over the cap, or a rate worse than allowed, is `BLOCKED`. The AI cannot
change the tier or the limits.

---

## 2. Known-risk list

Legend: **Mitigated** = addressed in code and covered by a test; **Accepted** =
a deliberate trade-off for this project's scope; **Open** = known gap I did not
close.

| # | Risk | Status | What I did / did not do |
|---|---|---|---|
| 1 | FX **markup** is not published by banks, so it is set to `0` and every route is flagged `illustrative`. The ranking could understate a real markup. | Accepted (documented) | The README and UI state this loudly; the user is told to fix markup from their own quote. I did not invent numbers. |
| 2 | Fee schedules have **min/max caps** (e.g. USD5–USD40) that the model does **not** apply; it uses a flat effective percentage. | Open | Documented in each route's `source` text. Applying caps would change the cost model and needs more test coverage; deferred. |
| 3 | Authentication is **opt-in**. If `ACCESS_TOKEN` is unset the API is open. | Accepted (documented) | Keeps the demo/tests frictionless. Startup prints a loud warning; `.env.example` documents it. Real deployment must set it. |
| 4 | The CSP allows `'unsafe-inline'` and `'unsafe-eval'` scripts plus Tailwind/jsDelivr CDNs, because the current UI is a static Tailwind mockup with inline handlers. | Accepted (trade-off) | Needed for that UI to render at all. A production build with bundled CSS/JS would let me drop `unsafe-inline`/`unsafe-eval`. |
| 5 | The web UI is currently a **static mockup**: it does not call the API and shows hardcoded sample numbers, which conflicts with the "no invented numbers" rule. | Open | The functional system is the API + sandbox + tests. Rewiring the page to live data is the recommended next step. |
| 6 | No **encryption at rest**; the SQLite DB and audit log are plaintext. | Accepted | Same-machine, fake-money data. Full-disk or container encryption is the deployment-level fix. |
| 7 | The audit chain uses a **plain hash, not a keyed HMAC** with an externally held key. Someone with write access to the file could recompute the whole chain. | Accepted | It detects accidental corruption and casual tampering, and it is re-checked while running. True non-repudiation would need a secret key held outside the working directory. |
| 8 | Rate limiters are **in-memory and per-process**. Restarting resets them; multiple workers each get their own budget. | Accepted | Fine for a local single-worker app. A shared store (Redis) would be needed to scale. |
| 9 | Session signing key is random **per process**, so a restart logs everyone out. | Accepted (by design) | Good for security, mildly inconvenient. Persistent sessions would need a stored key. |
| 10 | **No real TLS by default**; HSTS is only sent when the request is already HTTPS. | Accepted | TLS termination is a deployment concern (uvicorn `--ssl-*` or a reverse proxy). Documented in the README. |
| 11 | `X-Forwarded-Proto` is **trusted** to decide "secure". If a proxy is misconfigured, cookies could be marked secure incorrectly. | Accepted | Only matters behind a proxy. A trusted-proxy config would tighten this. |
| 12 | **Prompt injection** could still make the model say wrong things. | Mitigated (residual) | The model can't change numbers (they come from tools) and can't move funds. It could still produce misleading prose, so every figure shows its source and "as of" date. |
| 13 | A signed-in user can call `POST /api/tool` with `request_transfer` directly, bypassing the chat review. | Accepted (by design) | The user *is* the authority; the mandate and confirmation still apply. |
| 14 | SQLite uses one shared connection with `check_same_thread=False`, relying on the app lock. | Accepted (assumption) | Documented in `store.py`; every write holds the state lock. A misuse elsewhere could race. |
| 15 | Security events and audit entries are written to **plaintext files** on disk with no retention policy (the security log rotates at 1 MB). | Accepted | Local app; acceptable. Centralised/structured logging would be the production step. |
| 16 | **No backups** of `data/studentfx.db`. | Open | It is a sandbox; noted as an operational gap. |
| 17 | Login is protected only by an access token (no per-user accounts, no MFA, no lockout beyond a 10/min limiter). | Accepted | Single-user local app. MFA/accounts are out of scope. |

---

## 3. What is tested

`tests/test_security.py` (part of the 148-test suite) verifies, among others:

- unauthenticated `/api/state` → 401;
- rejected responses still carry the security headers;
- CSRF: a state-changing request without the header → 403;
- login throttle: repeated bad logins → 429;
- access token compared correctly, and short/absent token behaves as documented;
- `_d` rejects over-long and out-of-range numbers;
- audit chain detects tampering (and the live 30-second re-check flips
  `audit_ok`).

Run them with:

```
.venv\Scripts\python.exe -m pytest -q
```

---

## 4. Recommendations before any real-money use

1. Set a long random `ACCESS_TOKEN` and serve only over TLS.
2. Replace the static mockup UI with a build wired to the live API.
3. Model the fee min/max caps from the real schedules.
4. Move the audit chain to a keyed HMAC with a key stored outside the repo.
5. Move rate limiting and sessions to a shared store if running more than one
   worker.
