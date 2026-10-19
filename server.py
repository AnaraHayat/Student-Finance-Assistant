import json
import os
import threading
import time
import traceback
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from agent.llm import GeminiModel, LLMError
from agent.loop import Agent
from agent.mock import MockModel
from agent.tools import ToolBox, _d
from audit import append as append_audit
from audit import verify as verify_audit
from core.mandate import Mandate
from core.rates import get_rate
from core.routes import load_routes
from core.user_quote import analyze_quote
from sandbox.ledger import Sandbox
from security import (COOKIE_CSRF, COOKIE_SESSION, SessionSigner, log_security,
                      restrict_file, token_matches)
from store import Store

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"

# Hosts that count as "this machine". Used by the optional LOCAL_ONLY guard.
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def is_local_host(host) -> bool:
    return host in LOCAL_HOSTS



def is_illustrative(route) -> bool:
    text = (route.source + " " + route.as_of).lower()
    return any(w in text for w in ("placeholder", "illustrative", "not checked"))


def completed_actions_note(exc) -> str:
    tools = [t["tool"] for t in getattr(exc, "trace", [])]
    if not tools:
        return ""
    return (" Actions already completed before the failure: "
            + ", ".join(tools) + ". Check the lists before repeating them.")


class RateLimiter:
    """Fixed-window limiter: at most ``max_calls`` per ``window_seconds``
    for each key (the client host). Guards the slow, costly chat endpoint.
    In memory, no extra dependency."""

    def __init__(self, max_calls, window_seconds):
        self.max_calls = max_calls
        self.window = window_seconds
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key) -> bool:
        now = time.monotonic()
        with self._lock:
            start, count = self._hits.get(key, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            count += 1
            self._hits[key] = (start, count)
            return count <= self.max_calls


class AppState:
    def __init__(self, sandbox, toolbox, agent, rate, routes, llm_mode,
                 store=None):
        self.sandbox = sandbox
        self.toolbox = toolbox
        self.agent = agent
        self.rate = rate
        self.routes = routes
        self.llm_mode = llm_mode
        self.store = store
        self.my_quotes = []
        self.audit_ok = True
        self.audit_checked_at = 0.0
        # lock: short state operations. chat_lock: one chat turn at a time.
        # The slow AI call must never hold `lock`, or the whole page freezes.
        self.lock = threading.RLock()
        self.chat_lock = threading.Lock()
        toolbox.lock = self.lock


def build_default_state() -> AppState:
    rate = get_rate()
    routes = load_routes(ROOT / "data" / "fees.json")
    mandate = Mandate(D("50000"), D("5000"), D("60"))
    store = Store(ROOT / "data" / "studentfx.db")
    restrict_file(ROOT / "data" / "studentfx.db")
    sandbox = Sandbox(D("2000000"), mandate, 1,
                      ROOT / "logs" / "audit.jsonl", store=store)
    toolbox = ToolBox(sandbox, routes, rate, store=store)
    quotes = store.restore(sandbox, toolbox)
    try:
        model = GeminiModel.from_env()
        mode = f"gemini:{model.model}"
    except LLMError:
        model = MockModel()
        mode = "demo"
    st = AppState(sandbox, toolbox, Agent(model, toolbox), rate, routes, mode,
                  store=store)
    st.my_quotes = quotes
    env_file = ROOT / ".env"
    if env_file.exists():
        restrict_file(env_file)
    ok, bad_line = verify_audit(ROOT / "logs" / "audit.jsonl")
    st.audit_ok = ok
    st.audit_checked_at = time.monotonic()
    if not ok:
        log_security("audit_tamper", detail=f"audit log broken at line {bad_line}")
    return st


def snapshot(s: AppState) -> dict:
    sb = s.sandbox
    m = sb.mandate
    return {
        "llm_mode": s.llm_mode,
        "audit_ok": s.audit_ok,
        "today": date.today().isoformat(),
        "rate": {"pkr_per_cny": str(s.rate.pkr_per_cny),
                 "as_of": s.rate.as_of,
                 "from_cache": s.rate.from_cache,
                 "source": s.rate.source},
        "balances": {"pkr": str(sb.pkr_balance), "cny": str(sb.cny_balance)},
        "tier": sb.tier,
        "mandate": {"max_transfer_cny": str(m.max_transfer_cny),
                    "auto_execute_limit_cny": str(m.auto_execute_limit_cny),
                    "max_pkr_per_cny": str(m.max_pkr_per_cny)},
        "pending": [{"transfer_id": tid, "route": q.route,
                     "cny_to_deliver": str(q.cny_to_deliver),
                     "total_pkr": str(q.total_pkr)}
                    for tid, q in sb.pending.items()],
        "routes": [{"name": r.name, "route_type": r.route_type,
                    "typical_days": r.typical_days,
                    "supports_tuition": r.supports_tuition,
                    "source": r.source, "as_of": r.as_of,
                    "illustrative": is_illustrative(r)}
                   for r in s.routes],
        "expenses": [{"name": e.name, "on": e.on.isoformat(),
                      "amount_cny": str(e.amount_cny),
                      "category": e.category}
                     for e in s.toolbox.expenses],
        "events": [{"name": e.name, "on": e.on.isoformat(),
                    "amount_cny": str(e.amount_cny), "kind": e.kind}
                   for e in s.toolbox.events],
        "my_quotes": list(s.my_quotes),
    }


class ChatReq(BaseModel):
    message: str


class TierReq(BaseModel):
    tier: int


class ToolReq(BaseModel):
    name: str
    args: dict = {}


class QuoteReq(BaseModel):
    provider: str
    pkr_paid: str | int | float
    cny_received: str | int | float


class MandateReq(BaseModel):
    max_transfer_cny: str | int | float
    auto_execute_limit_cny: str | int | float
    max_pkr_per_cny: str | int | float


class LoginReq(BaseModel):
    token: str = Field(min_length=1, max_length=512)


def create_app(state=None, chat_rate_limit=30, chat_rate_window=60,
               local_only=None, access_token=None, session_ttl=None,
               mutation_rate_limit=120, mutation_rate_window=60,
               login_rate_limit=10, login_rate_window=60,
               max_body_bytes=65536, allowed_hosts=None) -> FastAPI:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass

    chat_limiter = RateLimiter(chat_rate_limit, chat_rate_window)
    mutation_limiter = RateLimiter(mutation_rate_limit, mutation_rate_window)
    login_limiter = RateLimiter(login_rate_limit, login_rate_window)

    if local_only is None:
        local_only = os.getenv("LOCAL_ONLY", "").strip().lower() in (
            "1", "true", "yes")

    if access_token is None:
        access_token = os.getenv("ACCESS_TOKEN", "").strip()
    auth_enabled = bool(access_token)

    if session_ttl is None:
        try:
            session_ttl = int(os.getenv("SESSION_TTL", "1800"))
        except ValueError:
            session_ttl = 1800
    session_ttl = max(60, min(int(session_ttl), 86400))
    # Random per-process key: a restart invalidates all sessions.
    signer = SessionSigner(os.urandom(32), session_ttl)

    if allowed_hosts is None:
        env_hosts = [h.strip() for h in
                     os.getenv("ALLOWED_HOSTS", "").split(",") if h.strip()]
        allowed_hosts = env_hosts or ["127.0.0.1", "localhost"]

    @asynccontextmanager
    async def lifespan(app):
        if state is None:
            app.state.st = build_default_state()
            if not auth_enabled:
                print("[security] ACCESS_TOKEN is not set: the API is "
                      "unauthenticated. Set it in .env before exposing this "
                      "app beyond this machine.", flush=True)
            elif len(access_token) < 16:
                print("[security] ACCESS_TOKEN is short. Use a random value "
                      "of at least 32 characters.", flush=True)
        yield

    app = FastAPI(title="StudentFX", lifespan=lifespan)
    if state is not None:
        app.state.st = state

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    def harden(response, *, secure: bool, path: str):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=()")
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "object-src 'none'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
            "https://fonts.googleapis.com; "
            "font-src 'self' data: https://cdn.jsdelivr.net "
            "https://fonts.gstatic.com; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
            "connect-src 'self' https://cdn.tailwindcss.com; "
            "form-action 'self'")
        if secure:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains")
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def err(status: int, detail: str, *, secure: bool, path: str):
        return harden(JSONResponse({"detail": detail}, status_code=status),
                      secure=secure, path=path)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        host = request.client.host if request.client else "unknown"
        secure = (request.url.scheme == "https" or
                  request.headers.get("x-forwarded-proto", "").lower() == "https")
        is_api = path.startswith("/api/")

        if local_only and not is_local_host(host):
            log_security("local_only_block", host, path)
            return err(403, "Forbidden: this server only accepts local connections.",
                       secure=secure, path=path)

        if is_api and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            try:
                length = int(request.headers.get("content-length") or 0)
            except ValueError:
                length = 0
            if length > max_body_bytes:
                log_security("body_too_large", host, f"{path} {length}")
                return err(413, "Request body too large.",
                           secure=secure, path=path)
            if not mutation_limiter.allow(host):
                log_security("rate_limit", host, path)
                return err(429, "Too many requests. Please slow down.",
                           secure=secure, path=path)

        session = None
        if auth_enabled and is_api and path != "/api/login":
            session = signer.decode(request.cookies.get(COOKIE_SESSION))
            if session is None:
                log_security("auth_denied", host, path)
                return err(401, "Please sign in.", secure=secure, path=path)
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                if not token_matches(request.headers.get("x-csrf-token", ""),
                                     session.get("csrf", "")):
                    log_security("csrf_denied", host, path)
                    return err(403, "Invalid CSRF token.",
                               secure=secure, path=path)

        response = await call_next(request)
        harden(response, secure=secure, path=path)

        # Sliding idle timeout: refresh the signed session on each request.
        if session is not None and path != "/api/logout":
            response.set_cookie(
                COOKIE_SESSION, signer.encode(signer.touch(session)),
                max_age=signer.ttl, httponly=True, samesite="strict",
                secure=secure, path="/")
        return response

    def st() -> AppState:
        return app.state.st

    @app.post("/api/login")
    def login(req: LoginReq, request: Request):
        if not auth_enabled:
            return {"ok": True, "auth": False}
        host = request.client.host if request.client else "unknown"
        if not login_limiter.allow(host):
            log_security("login_rate_limited", host)
            raise HTTPException(
                status_code=429,
                detail="Too many sign-in attempts. Try again later.")
        if not token_matches(req.token, access_token):
            log_security("login_failed", host)
            raise HTTPException(status_code=401, detail="Invalid access token.")
        log_security("login_ok", host)
        secure = (request.url.scheme == "https" or
                  request.headers.get("x-forwarded-proto", "").lower() == "https")
        payload = signer.issue()
        resp = JSONResponse({"ok": True, "auth": True, "csrf": payload["csrf"]})
        resp.set_cookie(COOKIE_SESSION, signer.encode(payload),
                        max_age=signer.ttl, httponly=True, samesite="strict",
                        secure=secure, path="/")
        resp.set_cookie(COOKIE_CSRF, payload["csrf"], max_age=signer.ttl,
                        httponly=False, samesite="strict", secure=secure,
                        path="/")
        return resp

    @app.post("/api/logout")
    def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE_SESSION, path="/")
        resp.delete_cookie(COOKIE_CSRF, path="/")
        return resp

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.get("/api/state")
    def get_state():
        s = st()
        with s.lock:
            now = time.monotonic()
            if now - s.audit_checked_at > 30:
                ok, bad_line = verify_audit(s.sandbox.log_path)
                s.audit_ok = ok
                s.audit_checked_at = now
                if not ok:
                    log_security(
                        "audit_tamper",
                        detail=f"audit log broken at line {bad_line}")
            snap = snapshot(s)
        snap["auth_required"] = auth_enabled
        return snap

    @app.post("/api/chat")
    def chat(req: ChatReq, request: Request):
        s = st()
        host = request.client.host if request.client else "unknown"
        if not chat_limiter.allow(host):
            raise HTTPException(
                status_code=429,
                detail="Too many messages too quickly. Please wait a minute "
                       "and try again.")
        msg = req.message.strip()
        if not msg:
            raise HTTPException(status_code=400, detail="empty message")
        if len(msg) > 2000:
            raise HTTPException(status_code=400, detail="message too long")
        if not s.chat_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail="Still working on your previous message. Please wait.")
        started = time.time()
        mark = len(s.agent.contents)
        try:
            out = s.agent.ask(msg)
        except LLMError as e:
            del s.agent.contents[mark:]
            print(f"[chat] AI unavailable: {e}", flush=True)
            raise HTTPException(
                status_code=502,
                detail=f"The AI model is unavailable: {e}."
                       + completed_actions_note(e))
        except Exception as e:
            del s.agent.contents[mark:]
            traceback.print_exc()
            raise HTTPException(
                status_code=500,
                detail="Unexpected server error. See the terminal window for "
                       "details." + completed_actions_note(e))
        finally:
            s.chat_lock.release()
        seconds = round(time.time() - started, 1)
        print(f"[chat] answered in {seconds}s with "
              f"{len(out['trace'])} tool calls", flush=True)
        return {"text": out["text"], "trace": out["trace"],
                "stopped": out["stopped"], "seconds": seconds}

    @app.post("/api/tool")
    def tool(req: ToolReq):
        if len(req.name) > 64:
            raise HTTPException(status_code=400, detail="Tool name is too long.")
        args_json = json.dumps(req.args, separators=(",", ":"))
        if len(args_json) > 8192:
            raise HTTPException(status_code=413,
                                detail="Tool arguments are too large.")
        return st().toolbox.call(req.name, req.args)

    @app.post("/api/confirm/{tid}")
    def confirm(tid: str):
        s = st()
        with s.lock:
            try:
                return s.sandbox.confirm(tid)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/reject/{tid}")
    def reject(tid: str):
        s = st()
        with s.lock:
            try:
                return s.sandbox.reject(tid)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/tier")
    def set_tier(req: TierReq):
        s = st()
        if req.tier not in (0, 1, 2):
            raise HTTPException(status_code=400, detail="tier must be 0, 1 or 2")
        with s.lock:
            s.sandbox.set_tier(req.tier)
        return {"tier": req.tier}

    @app.post("/api/mandate")
    def set_mandate(req: MandateReq):
        s = st()
        with s.lock:
            try:
                new = Mandate(_d(req.max_transfer_cny),
                              _d(req.auto_execute_limit_cny),
                              _d(req.max_pkr_per_cny),
                              s.sandbox.mandate.allowed_routes)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            s.sandbox.mandate = new
            path = s.sandbox.log_path
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "mandate_change",
                "transfer_id": "-",
                "route": "-",
                "status": "mandate_updated",
                "cny_to_deliver": "",
                "total_pkr": "",
                "reasons": [
                    f"max_transfer_cny={new.max_transfer_cny}",
                    f"auto_execute_limit_cny={new.auto_execute_limit_cny}",
                    f"max_pkr_per_cny={new.max_pkr_per_cny}"],
            }
            append_audit(path, entry)
            s.sandbox.save()
        return {"ok": True}

    @app.post("/api/quotes")
    def add_quote(req: QuoteReq):
        s = st()
        if len(req.provider) > 120:
            raise HTTPException(status_code=400,
                                detail="Provider name is too long.")
        try:
            uq = analyze_quote(req.provider, _d(req.pkr_paid),
                               _d(req.cny_received), s.rate.pkr_per_cny)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        entry = {
            "provider": uq.provider,
            "on": date.today().isoformat(),
            "pkr_paid": str(uq.pkr_paid),
            "cny_received": str(uq.cny_received),
            "mid_rate": str(uq.mid_rate),
            "effective_rate": str(uq.effective_rate),
            "hidden_cost_pkr": str(uq.hidden_cost_pkr),
            "hidden_cost_pct": str(uq.hidden_cost_pct),
        }
        with s.lock:
            s.my_quotes.append(entry)
            if s.store is not None:
                s.store.save_quotes(s.my_quotes)
        return entry

    @app.get("/api/log")
    def get_log():
        s = st()
        with s.lock:
            path = s.sandbox.log_path
            if not path.exists():
                return {"entries": []}
            lines = path.read_text(encoding="utf-8").splitlines()[-50:]
        return {"entries": [json.loads(x) for x in lines if x.strip()]}

    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
    return app


app = create_app()