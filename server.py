import json
import threading
import time
import traceback
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.llm import GeminiModel, LLMError
from agent.loop import Agent
from agent.mock import MockModel
from agent.tools import ToolBox, _d
from core.mandate import Mandate
from core.rates import get_rate
from core.routes import load_routes
from core.user_quote import analyze_quote
from sandbox.ledger import Sandbox

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"


def is_illustrative(route) -> bool:
    text = (route.source + " " + route.as_of).lower()
    return any(w in text for w in ("placeholder", "illustrative", "not checked"))


def completed_actions_note(exc) -> str:
    tools = [t["tool"] for t in getattr(exc, "trace", [])]
    if not tools:
        return ""
    return (" Actions already completed before the failure: "
            + ", ".join(tools) + ". Check the lists before repeating them.")


class AppState:
    def __init__(self, sandbox, toolbox, agent, rate, routes, llm_mode):
        self.sandbox = sandbox
        self.toolbox = toolbox
        self.agent = agent
        self.rate = rate
        self.routes = routes
        self.llm_mode = llm_mode
        self.my_quotes = []
        # lock: short state operations. chat_lock: one chat turn at a time.
        # The slow AI call must never hold `lock`, or the whole page freezes.
        self.lock = threading.RLock()
        self.chat_lock = threading.Lock()
        toolbox.lock = self.lock


def build_default_state() -> AppState:
    rate = get_rate()
    routes = load_routes(ROOT / "data" / "fees.json")
    mandate = Mandate(D("50000"), D("5000"), D("60"))
    sandbox = Sandbox(D("2000000"), mandate, 1,
                      ROOT / "logs" / "audit.jsonl")
    toolbox = ToolBox(sandbox, routes, rate)
    try:
        model = GeminiModel.from_env()
        mode = f"gemini:{model.model}"
    except LLMError:
        model = MockModel()
        mode = "demo"
    return AppState(sandbox, toolbox, Agent(model, toolbox), rate, routes,
                    mode)


def snapshot(s: AppState) -> dict:
    sb = s.sandbox
    m = sb.mandate
    return {
        "llm_mode": s.llm_mode,
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


def create_app(state=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        if state is None:
            app.state.st = build_default_state()
        yield

    app = FastAPI(title="StudentFX", lifespan=lifespan)
    if state is not None:
        app.state.st = state

    def st() -> AppState:
        return app.state.st

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
            return snapshot(s)

    @app.post("/api/chat")
    def chat(req: ChatReq):
        s = st()
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
                detail=f"Unexpected server error: {type(e).__name__}. "
                       "See the terminal window for details."
                       + completed_actions_note(e))
        finally:
            s.chat_lock.release()
        seconds = round(time.time() - started, 1)
        print(f"[chat] answered in {seconds}s with "
              f"{len(out['trace'])} tool calls", flush=True)
        return {"text": out["text"], "trace": out["trace"],
                "stopped": out["stopped"], "seconds": seconds}

    @app.post("/api/tool")
    def tool(req: ToolReq):
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
            s.sandbox.tier = req.tier
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
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        return {"ok": True}

    @app.post("/api/quotes")
    def add_quote(req: QuoteReq):
        s = st()
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