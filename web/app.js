"use strict";

const TIER_HELP = {
  0: "Read-only: the assistant can analyse but cannot request transfers.",
  1: "Draft only: every transfer waits for your Confirm click.",
  2: "Limited autonomy: transfers up to your auto-execute limit run on their own; larger ones wait for you."
};

const STARTERS = [
  "What is the cheapest way to send 8000 CNY?",
  "I need 5000 CNY for rent next week",
  "Add expense: lunch 25 CNY today, food"
];

let STATE = null;
let BUSY = false;
let LAST_MANDATE_KEY = "";

const $ = (sel) => document.querySelector(sel);

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

/* ---------- formatting only (no money arithmetic here) ---------- */
function fmt(s) {
  if (s === null || s === undefined) return "-";
  const m = String(s).match(/^(-?)(\d+)(\.\d+)?$/);
  if (!m) return String(s);
  return m[1] + m[2].replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (m[3] || "");
}

function amt(v, unit) {
  if (v === null || v === undefined || v === "") return "-";
  return fmt(v) + (unit ? " " + unit : "");
}

// "0.0145" -> "1.45%"  (moves the decimal point in the text)
function pct(s) {
  const m = String(s).match(/^(-?)(\d+)\.?(\d*)$/);
  if (!m) return String(s);
  const frac = (m[3] + "00").slice(0, Math.max(2, m[3].length));
  const whole = (m[2] + frac.slice(0, 2)).replace(/^0+(?=\d)/, "");
  const rest = frac.slice(2);
  return m[1] + whole + (rest ? "." + rest : "") + "%";
}

function localDate(offsetDays) {
  const d = new Date(Date.now() + offsetDays * 86400000);
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}

function getCookie(name) {
  const prefix = name + "=";
  return document.cookie.split(";")
    .map((c) => c.trim())
    .filter((c) => c.startsWith(prefix))
    .map((c) => decodeURIComponent(c.slice(prefix.length)))[0] || "";
}

/* ---------- toasts ---------- */
const TOAST_COLORS = {
  success: "bg-green-600", warning: "bg-yellow-600",
  danger: "bg-red-600", info: "bg-blue-600"
};

function toast(message, type) {
  const kinds = { error: "danger", warn: "warning" };
  const kind = kinds[type] || type || "info";
  const container = $("#toast-container");
  const t = el("div", `${TOAST_COLORS[kind] || TOAST_COLORS.info} text-white px-4 py-3 rounded-lg shadow-lg`);
  t.textContent = message;
  container.appendChild(t);
  setTimeout(() => t.remove(), 4500);
}

function isErrorType(t) { return t === "error" || t === "warn" || t === "danger"; }

/* ---------- login overlay ---------- */
function showLogin(message) {
  const ov = $("#login-overlay");
  if (!ov) return;
  ov.classList.remove("hidden");
  const err = $("#login-error");
  if (err) err.textContent = message || "";
  const input = $("#login-token");
  if (input) setTimeout(() => input.focus(), 30);
}

function hideLogin() {
  const ov = $("#login-overlay");
  if (ov) ov.classList.add("hidden");
}

/* ---------- fetch wrapper (adds CSRF, handles 401) ---------- */
function isWrite(method) { return method && method !== "GET" && method !== "HEAD"; }

async function api(path, method, body, timeoutMs) {
  const opt = { method: method || "GET", headers: {}, credentials: "same-origin" };
  let timer = null;
  if (timeoutMs) {
    const ctl = new AbortController();
    opt.signal = ctl.signal;
    timer = setTimeout(() => ctl.abort(), timeoutMs);
  }
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  if (isWrite(opt.method)) {
    const csrf = getCookie("sfx_csrf");
    if (csrf) opt.headers["X-CSRF-Token"] = csrf;
  }
  let r;
  try {
    r = await fetch(path, opt);
  } catch (e) {
    if (e && e.name === "AbortError") {
      throw new Error("No answer after " + Math.round(timeoutMs / 1000) +
        " seconds. The AI model may be slow or unreachable. Check the terminal window.");
    }
    throw new Error("Cannot reach the server. Is it still running?");
  } finally {
    if (timer) clearTimeout(timer);
  }
  let data = null;
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) {
    const d = data && data.detail;
    let msg;
    if (typeof d === "string") msg = d;
    else if (r.status === 422) msg = "Invalid input. Check the values you entered.";
    else if (r.status === 401) msg = "Please sign in.";
    else msg = "Server error (" + r.status + "). See the terminal window.";
    const err = new Error(msg);
    err.status = r.status;
    if (r.status === 401 && path !== "/api/login") showLogin();
    throw err;
  }
  return data;
}

/* ---------- small UI builders ---------- */
function badge(text, kind) {
  const map = {
    ok: "bg-green-100 text-green-800",
    warn: "bg-yellow-100 text-yellow-800",
    danger: "bg-red-100 text-red-800",
    info: "bg-blue-100 text-blue-800",
    neutral: "bg-gray-100 text-gray-700"
  };
  return el("span", `chip ${map[kind] || map.neutral} text-xs`, text);
}

function table(headers, rows, bestFirst) {
  const wrap = el("div", "overflow-x-auto");
  const t = el("table", "w-full text-sm border-collapse");
  const thead = el("thead");
  const hr = el("tr", "text-left text-neutral border-b border-gray-200");
  headers.forEach((h) => hr.appendChild(el("th", "py-2 px-2 font-medium", h)));
  thead.appendChild(hr);
  t.appendChild(thead);
  const tbody = el("tbody");
  rows.forEach((r, i) => {
    const tr = el("tr", "border-b border-gray-100" +
      (bestFirst && i === 0 ? " bg-green-50" : ""));
    r.forEach((c) => {
      const td = el("td", "py-2 px-2 align-top");
      if (c instanceof Node) td.appendChild(c); else td.textContent = String(c);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  t.appendChild(tbody);
  wrap.appendChild(t);
  return wrap;
}

function muted(text) { return el("p", "text-neutral text-sm", text); }

/* ---------- state ---------- */
async function refreshState() {
  STATE = await api("/api/state");
  hideLogin();
  renderState();
}

async function refreshAll() {
  try {
    await refreshState();
    if ($("#tab-activity") && !$("#tab-activity").classList.contains("hidden")) await loadLog();
  } catch (e) {
    if (e.status !== 401) toast(e.message, "error");
  }
}

function modeText(s) {
  return s.llm_mode.startsWith("gemini")
    ? s.llm_mode.replace("gemini:", "")
    : "demo (no AI key)";
}

function renderState() {
  const s = STATE;
  $("#rate-chip").textContent = s.rate.pkr_per_cny;
  const rateDate = $("#rate-date");
  if (rateDate) rateDate.textContent = "(" + s.rate.as_of + (s.rate.from_cache ? ", saved copy" : "") + ")";
  $("#mode-chip").textContent = "AI: " + modeText(s);
  $("#tier-chip").textContent = "Tier " + s.tier;
  const audit = $("#audit-chip");
  const ok = s.audit_ok !== false;
  audit.className = "chip " + (ok ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800");
  audit.innerHTML = '<i class="fa ' + (ok ? "fa-check-circle" : "fa-exclamation-triangle") +
    ' mr-1"></i> Audit ' + (ok ? "verified" : "NOT verified");
  $("#bal-pkr").textContent = "Rs. " + fmt(s.balances.pkr);
  $("#bal-cny").textContent = "¥ " + fmt(s.balances.cny);
  $("#mid-rate-label").textContent = "Mid-market rate: " + s.rate.pkr_per_cny +
    " PKR/CNY" + (s.rate.from_cache ? " (saved copy)" : "");
  $("#banner-note").innerHTML =
    "<strong>Sandbox:</strong> All values are simulated — no real money moves. " +
    "Not financial advice. Fee data is partly illustrative; check each route's source.";

  const lock = $("#lock-btn");
  if (lock) lock.classList.toggle("hidden", !s.auth_required);

  document.querySelectorAll('#tier-group input[data-tier]').forEach((b) => {
    b.checked = Number(b.dataset.tier) === s.tier;
  });
  $("#tier-help").textContent = TIER_HELP[s.tier] || "";

  const m = s.mandate;
  const key = [m.max_transfer_cny, m.auto_execute_limit_cny, m.max_pkr_per_cny].join("|");
  if (key !== LAST_MANDATE_KEY) {
    $("#md-max").value = m.max_transfer_cny;
    $("#md-auto").value = m.auto_execute_limit_cny;
    $("#md-rate").value = m.max_pkr_per_cny;
    LAST_MANDATE_KEY = key;
  }

  renderPending();
  renderTrust();
  renderRoutes();
  renderQuotes();
  renderLists();
}

function renderTrust() {
  const s = STATE;
  const total = s.routes.length;
  const illus = s.routes.filter((r) => r.illustrative).length;
  const dl = $("#trust-info");
  dl.textContent = "";
  const row = (k, v) => {
    dl.appendChild(el("dt", "text-neutral", k));
    dl.appendChild(el("dd", "font-medium", v));
  };
  row("Exchange rate", "1 CNY = " + s.rate.pkr_per_cny + " PKR");
  row("Rate date", s.rate.as_of + (s.rate.from_cache ? " (saved copy)" : ""));
  row("Rate source", s.rate.source);
  row("Fee data", illus + " of " + total + " routes illustrative");
  row("Today", s.today);
  row("Audit log", s.audit_ok === false ? "NOT verified" : "hash chain verified");
  row("AI", modeText(s));

  let note = illus > 0
    ? "Illustrative routes are not fully sourced (for example the exchange-rate markup may be missing). Do not use them to choose a real transfer."
    : "";
  if (s.audit_ok === false) {
    note += " The audit log failed its integrity check, so recorded activity may have been altered.";
  }
  $("#trust-note").textContent = note;
}

function renderPending() {
  const box = $("#pending-list");
  box.textContent = "";
  $("#pending-count").textContent = String(STATE.pending.length);
  if (!STATE.pending.length) {
    box.appendChild(muted("Nothing is waiting for you."));
    return;
  }
  STATE.pending.forEach((p) => {
    const card = el("div", "p-3 border border-gray-100 rounded-lg");
    card.appendChild(el("p", "font-medium", p.transfer_id + " · " + p.route));
    card.appendChild(el("p", "text-sm text-neutral mb-2",
      fmt(p.cny_to_deliver) + " CNY delivered · " + fmt(p.total_pkr) + " PKR"));
    const row = el("div", "flex gap-2");
    const ok = el("button", "flex-1 bg-success text-white py-1.5 rounded text-sm", "Confirm");
    ok.addEventListener("click", () => decide(p.transfer_id, "confirm"));
    const no = el("button", "flex-1 bg-danger text-white py-1.5 rounded text-sm", "Reject");
    no.addEventListener("click", () => decide(p.transfer_id, "reject"));
    row.appendChild(ok);
    row.appendChild(no);
    card.appendChild(row);
    box.appendChild(card);
  });
}

async function decide(id, kind) {
  try {
    const r = await api("/api/" + kind + "/" + encodeURIComponent(id), "POST");
    const isErr = r.status === "insufficient_funds";
    toast(id + ": " + r.status.replace(/_/g, " ") +
      (r.reasons && r.reasons.length ? " (" + r.reasons.join("; ") + ")" : ""),
      isErr ? "error" : "success");
  } catch (e) {
    toast(e.message, "error");
  }
  await refreshAll();
}

function renderRoutes() {
  const sel = $("#cmp-type");
  const current = sel.value;
  const types = Array.from(new Set(STATE.routes.map((r) => r.route_type))).sort();
  sel.textContent = "";
  const all = el("option", "", "All types");
  all.value = "";
  sel.appendChild(all);
  types.forEach((t) => { const o = el("option", "", t); o.value = t; sel.appendChild(o); });
  sel.value = types.includes(current) ? current : "";
  $("#cmp-type-wrap").classList.toggle("hidden", types.length <= 1);
}

function renderQuotes() {
  const box = $("#quotes-table");
  box.textContent = "";
  if (!STATE.my_quotes.length) {
    box.appendChild(muted("No quotes yet."));
    return;
  }
  const rows = STATE.my_quotes.map((q) => [
    q.provider, q.on, fmt(q.pkr_paid) + " PKR", fmt(q.cny_received) + " CNY",
    q.effective_rate, fmt(q.hidden_cost_pkr) + " PKR (" + pct(q.hidden_cost_pct) + ")"
  ]);
  box.appendChild(table(
    ["Provider", "Date", "You pay", "You get", "Effective rate", "Hidden cost vs mid-market"], rows));
}

function renderLists() {
  const eb = $("#expense-list");
  eb.textContent = "";
  eb.appendChild(el("h3", "font-semibold text-lg mb-3", "Recorded expenses"));
  if (!STATE.expenses.length) {
    eb.appendChild(muted("No expenses recorded yet."));
  } else {
    eb.appendChild(table(["Date", "Name", "Category", "Amount"],
      STATE.expenses.slice().reverse().map((e) =>
        [e.on, e.name, e.category, fmt(e.amount_cny) + " CNY"])));
  }

  const vb = $("#event-list");
  vb.textContent = "";
  vb.appendChild(el("h3", "font-semibold text-lg mb-3", "Recorded bills and money in"));
  if (!STATE.events.length) {
    vb.appendChild(muted("Nothing recorded yet."));
  } else {
    vb.appendChild(table(["Date", "Name", "Type", "Amount"],
      STATE.events.slice().sort((a, b) => (a.on < b.on ? -1 : 1)).map((e) =>
        [e.on, e.name, e.kind === "inflow" ? "Money in" : "Bill",
          fmt(e.amount_cny) + " CNY"])));
  }
}

/* ---------- chat ---------- */
function addBubble(role) {
  const wrap = el("div", role === "user" ? "text-right" : "");
  const b = el("div", "inline-block text-left px-3 py-2 rounded-lg max-w-[90%] " +
    (role === "user" ? "bg-primary text-white" : "bg-gray-100 text-gray-800"),
    "");
  wrap.appendChild(b);
  $("#chat-log").appendChild(wrap);
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  return b;
}

function richText(container, text) {
  String(text).split("**").forEach((chunk, i) => {
    if (!chunk) return;
    container.appendChild(i % 2 ? el("strong", "", chunk) : document.createTextNode(chunk));
  });
}

// Built from the real tool results, not from the AI's words.
function toolStatus(trace) {
  const box = el("div", "mt-2 text-xs text-neutral border-t border-gray-200 pt-2");
  box.appendChild(el("div", "", "What the system actually did:"));
  trace.forEach((t) => {
    const r = t.result || {};
    let line;
    let bad = false;
    if (r.error) {
      line = "✗ " + t.tool + " failed: " + r.error;
      bad = true;
    } else if (r.status) {
      line = "• " + t.tool + ": " + String(r.status).replace(/_/g, " ");
      bad = r.status === "blocked" || r.status === "insufficient_funds";
    } else {
      line = "✓ " + t.tool;
    }
    const d = el("div", bad ? "text-red-700 font-semibold" : "", line);
    box.appendChild(d);
  });
  return box;
}

function traceView(trace) {
  const d = el("details", "mt-1 text-xs");
  d.appendChild(el("summary", "", "How I got this (" + trace.length + " tool call" +
    (trace.length === 1 ? "" : "s") + ")"));
  trace.forEach((t) => {
    d.appendChild(el("strong", "", t.tool));
    d.appendChild(el("pre", "whitespace-pre-wrap break-all", "args: " + JSON.stringify(t.args, null, 2)));
    d.appendChild(el("pre", "whitespace-pre-wrap break-all", "result: " + JSON.stringify(t.result, null, 2)));
  });
  return d;
}

function setBusy(b) {
  BUSY = b;
  $("#chat-send").disabled = b;
  $("#chat-input").disabled = b;
}

async function sendMessage(text) {
  text = text.trim();
  if (!text || BUSY) return;
  addBubble("user").textContent = text;
  const a = addBubble("agent");
  a.textContent = "Thinking… 0s";
  setBusy(true);
  const started = Date.now();
  const timer = setInterval(() => {
    a.textContent = "Thinking… " + Math.round((Date.now() - started) / 1000) + "s";
  }, 1000);
  try {
    const out = await api("/api/chat", "POST", { message: text }, 150000);
    clearInterval(timer);
    a.textContent = "";
    richText(a, out.text || "(no reply)");
    if (out.trace && out.trace.length) {
      a.appendChild(toolStatus(out.trace));
      a.appendChild(traceView(out.trace));
    }
    if (out.seconds !== undefined) a.appendChild(el("div", "mt-1 text-xs text-neutral", "answered in " + out.seconds + "s"));
  } catch (e) {
    clearInterval(timer);
    a.className = a.className.replace(/bg-gray-100/, "") + " bg-red-100 text-red-800";
    a.textContent = e.message;
  } finally {
    clearInterval(timer);
    setBusy(false);
    $("#chat-input").focus();
  }
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  await refreshAll();
}

/* ---------- tabs ---------- */
const TAB_BTNS = Array.from(document.querySelectorAll("#tabs [data-tab]"));

function activateTab(btn) {
  TAB_BTNS.forEach((b) => {
    const on = b === btn;
    b.classList.toggle("tab-active", on);
    b.classList.toggle("text-neutral", !on);
    b.classList.toggle("hover:text-gray-800", !on);
    b.setAttribute("aria-selected", on ? "true" : "false");
    b.tabIndex = on ? 0 : -1;
  });
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.add("hidden"));
  $("#tab-" + btn.dataset.tab).classList.remove("hidden");
  if (btn.dataset.tab === "activity") loadLog();
}

TAB_BTNS.forEach((btn, i) => {
  btn.addEventListener("click", () => activateTab(btn));
  btn.addEventListener("keydown", (ev) => {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(ev.key)) return;
    ev.preventDefault();
    let next = i;
    if (ev.key === "ArrowRight") next = (i + 1) % TAB_BTNS.length;
    else if (ev.key === "ArrowLeft") next = (i - 1 + TAB_BTNS.length) % TAB_BTNS.length;
    else if (ev.key === "Home") next = 0;
    else if (ev.key === "End") next = TAB_BTNS.length - 1;
    TAB_BTNS[next].focus();
    activateTab(TAB_BTNS[next]);
  });
});

/* ---------- compare routes ---------- */
async function requestTransfer(route, amount) {
  try {
    const r = await api("/api/tool", "POST", {
      name: "request_transfer",
      args: { route_name: route, cny_to_deliver: amount }
    });
    if (r.error) {
      toast(r.error, "error");
    } else {
      const bad = r.status === "blocked" || r.status === "insufficient_funds";
      toast("Transfer " + r.transfer_id + ": " + r.status.replace(/_/g, " ") +
        (r.reasons && r.reasons.length ? " (" + r.reasons.join("; ") + ")" : ""),
        bad ? "error" : "success");
    }
  } catch (e) {
    toast(e.message, "error");
  }
  await refreshAll();
}

$("#compare-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const amount = $("#cmp-amount").value.trim();
  const type = $("#cmp-type").value;
  if (!amount) { toast("Enter an amount first", "error"); return; }
  const args = { cny_to_deliver: amount };
  if (type) args.route_type = type;
  const box = $("#compare-result");
  box.textContent = "";
  try {
    const out = await api("/api/tool", "POST", { name: "compare_routes", args: args });
    if (out.error) { box.appendChild(el("p", "text-red-700", out.error)); return; }
    if (!out.quotes.length) { box.appendChild(muted("No routes match.")); return; }
    box.appendChild(muted("Mid-market rate: " + out.mid_rate +
      " PKR per CNY. The first route is the cheapest in this list."));
    out.quotes.forEach((q, i) => {
      const info = STATE.routes.find((r) => r.name === q.route);
      const cheapest = i === 0;
      const card = el("div", "p-4 border rounded-lg " +
        (cheapest ? "border-green-200 bg-green-50" : "border-gray-200"));
      const head = el("div", "flex flex-wrap items-center justify-between gap-2 mb-2");
      head.appendChild(el("h4", "font-semibold", q.route));
      const tags = el("div", "flex gap-2");
      if (cheapest) tags.appendChild(badge("Cheapest", "ok"));
      if (info && info.illustrative) tags.appendChild(badge("illustrative", "warn"));
      head.appendChild(tags);
      card.appendChild(head);
      const grid = el("div", "grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm mb-3");
      const cell = (label, value, cls) => {
        const d = el("div");
        d.appendChild(el("p", "text-neutral", label));
        d.appendChild(el("p", "font-medium " + (cls || ""), value));
        return d;
      };
      grid.appendChild(cell("You pay", fmt(q.total_pkr) + " PKR"));
      grid.appendChild(cell("Effective rate", q.effective_rate));
      grid.appendChild(cell("Hidden cost", fmt(q.hidden_cost_pkr) + " PKR (" + pct(q.hidden_cost_pct) + ")"));
      grid.appendChild(cell("Typical time", q.typical_days == null ? "n/a" : q.typical_days + " days"));
      card.appendChild(grid);
      card.appendChild(el("p", "text-xs text-neutral mb-3",
        "Fee source: " + q.fee_source + " (as of " + q.fees_as_of + ")"));
      const btn = el("button", "w-full btn-primary", "Request Transfer");
      btn.addEventListener("click", () => requestTransfer(q.route, amount));
      card.appendChild(btn);
      box.appendChild(card);
    });
  } catch (e) {
    toast(e.message, "error");
  }
});

/* ---------- spending ---------- */
$("#expense-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const r = await api("/api/tool", "POST", {
      name: "add_expense",
      args: {
        name: $("#exp-name").value,
        on: $("#exp-date").value,
        amount_cny: $("#exp-amount").value.trim(),
        category: $("#exp-cat").value
      }
    });
    if (r.error) toast(r.error, "error"); else toast("Expense added (" + r.expense_count + " total)", "success");
  } catch (e) {
    toast(e.message, "error");
  }
  await refreshAll();
});

async function loadSummary() {
  const parts = $("#sum-month").value.split("-");
  if (parts.length !== 2) { toast("Pick a month first", "error"); return; }
  const box = $("#summary-result");
  try {
    const s = await api("/api/tool", "POST", {
      name: "spending_summary",
      args: { year: Number(parts[0]), month: Number(parts[1]) }
    });
    box.textContent = "";
    if (s.error) { box.appendChild(el("p", "text-red-700", s.error)); return; }
    const head = el("div", "p-3 bg-gray-50 rounded-lg text-sm space-y-1");
    head.appendChild(el("div", "", s.count + " expenses · total " + fmt(s.total_cny) + " CNY"));
    head.appendChild(el("div", "", "= " + fmt(s.total_pkr_mid) + " PKR at the mid-market rate"));
    head.appendChild(el("div", "", "= " + fmt(s.total_pkr_effective) + " PKR at the rate you actually paid"));
    head.appendChild(el("div", "", "Hidden cost: " + fmt(s.hidden_cost_pkr) + " PKR"));
    box.appendChild(head);
    if (s.categories.length) {
      box.appendChild(table(
        ["Category", "CNY", "PKR (mid)", "PKR (paid)", "Share"],
        s.categories.map((c) => [c.category, fmt(c.cny), fmt(c.pkr_mid),
          fmt(c.pkr_effective), pct(c.share)])));
    }
  } catch (e) {
    toast(e.message, "error");
  }
}

$("#summary-btn").addEventListener("click", loadSummary);

/* ---------- forecast ---------- */
$("#event-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const r = await api("/api/tool", "POST", {
      name: "add_event",
      args: {
        name: $("#ev-name").value,
        on: $("#ev-date").value,
        amount_cny: $("#ev-amount").value.trim(),
        kind: $("#ev-kind").value
      }
    });
    if (r.error) toast(r.error, "error"); else toast("Added (" + r.event_count + " events total)", "success");
  } catch (e) {
    toast(e.message, "error");
  }
  await refreshAll();
});

$("#forecast-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const start = $("#fc-start").value;
  const end = $("#fc-end").value;
  if (!start || !end) { toast("Pick both dates first", "error"); return; }
  const box = $("#forecast-result");
  const args = {
    start: start,
    end: end,
    buffer_cny: $("#fc-buffer").value.trim() || "0",
    lead_days: Number($("#fc-lead").value || 0)
  };
  try {
    const f = await api("/api/tool", "POST", { name: "forecast_cashflow", args: args });
    box.textContent = "";
    if (f.error) { box.appendChild(el("p", "text-red-700", f.error)); return; }
    const short = f.first_shortfall_date !== null;
    const b = el("div", "p-3 rounded-lg text-sm space-y-1 " +
      (short ? "bg-red-50 border border-red-200 text-red-800" : "bg-gray-50"));
    b.appendChild(el("div", "", "Lowest balance: " + fmt(f.min_balance_cny) + " CNY on " + f.min_balance_date));
    if (short) {
      b.appendChild(el("div", "", "First shortfall: " + f.first_shortfall_date));
      b.appendChild(el("div", "", "Top-up needed: " + fmt(f.topup_needed_cny) + " CNY"));
      b.appendChild(el("div", "", "Start the transfer by: " + f.transfer_by));
      if (f.is_late) {
        b.appendChild(el("div", "font-semibold", "This is already too late for the assumed transfer time."));
      }
    } else {
      b.appendChild(el("div", "", "No shortfall in this period."));
    }
    box.appendChild(b);
  } catch (e) {
    toast(e.message, "error");
  }
});

/* ---------- my quotes ---------- */
$("#quote-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("/api/quotes", "POST", {
      provider: $("#q-provider").value,
      pkr_paid: $("#q-pkr").value.trim(),
      cny_received: $("#q-cny").value.trim()
    });
    toast("Quote analysed", "success");
    await refreshAll();
  } catch (e) {
    toast(e.message, "error");
  }
});

/* ---------- activity log ---------- */
async function loadLog() {
  const box = $("#log-table");
  try {
    const out = await api("/api/log");
    box.textContent = "";
    if (!out.entries.length) { box.appendChild(muted("Nothing recorded yet.")); return; }
    const rows = out.entries.slice().reverse().map((e) => [
      e.ts.replace("T", " ").slice(0, 19), e.event, e.transfer_id, e.route,
      String(e.status).replace(/_/g, " "), amt(e.cny_to_deliver, "CNY"),
      amt(e.total_pkr, "PKR"), (e.reasons || []).join("; ")
    ]);
    box.appendChild(table(
      ["Time (UTC)", "Event", "ID", "Route", "Status", "CNY", "PKR", "Reasons"], rows));
  } catch (e) {
    toast(e.message, "error");
  }
}

$("#log-refresh").addEventListener("click", loadLog);

/* ---------- tier + mandate ---------- */
document.querySelectorAll('#tier-group input[data-tier]').forEach((r) => {
  r.addEventListener("change", async () => {
    try {
      await api("/api/tier", "POST", { tier: Number(r.dataset.tier) });
      toast("Permission tier updated", "success");
    } catch (e) {
      toast(e.message, "error");
    }
    await refreshAll();
  });
});

$("#mandate-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    await api("/api/mandate", "POST", {
      max_transfer_cny: $("#md-max").value.trim(),
      auto_execute_limit_cny: $("#md-auto").value.trim(),
      max_pkr_per_cny: $("#md-rate").value.trim()
    });
    toast("Limits saved", "success");
  } catch (e) {
    toast(e.message, "error");
  }
  await refreshAll();
});

/* ---------- authentication ---------- */
$("#login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const btn = $("#login-submit");
  const errEl = $("#login-error");
  if (errEl) errEl.textContent = "";
  if (btn) btn.disabled = true;
  try {
    await api("/api/login", "POST", { token: $("#login-token").value }, 15000);
    $("#login-token").value = "";
    hideLogin();
    await refreshAll();
  } catch (e) {
    if (errEl) errEl.textContent = e.message;
  } finally {
    if (btn) btn.disabled = false;
  }
});

$("#lock-btn").addEventListener("click", async () => {
  try { await api("/api/logout", "POST", {}); } catch (e) { /* ignore */ }
  showLogin("Locked. Enter your access token to continue.");
});

/* ---------- show unexpected errors instead of failing silently ---------- */
window.addEventListener("unhandledrejection", (ev) => {
  toast("Unexpected error: " + (ev.reason && ev.reason.message ? ev.reason.message : ev.reason), "error");
});

/* ---------- live clock ---------- */
function pad2(n) { return String(n).padStart(2, "0"); }
function tickClock() {
  const now = new Date();
  const dateStr = now.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
  const timeStr = pad2(now.getHours()) + ":" + pad2(now.getMinutes()) + ":" + pad2(now.getSeconds());
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  const clock = $("#live-clock");
  if (clock) clock.textContent = dateStr + "  " + timeStr + (tz ? "  " + tz : "");
}
tickClock();
setInterval(tickClock, 1000);

/* ---------- starters + defaults + start ---------- */
STARTERS.forEach((s) => {
  const b = el("button", "chip bg-gray-100 hover:bg-gray-200 transition", s);
  b.type = "button";
  b.addEventListener("click", () => sendMessage(s));
  $("#starters").appendChild(b);
});

$("#chat-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const input = $("#chat-input");
  const text = input.value;
  input.value = "";
  sendMessage(text);
});

$("#exp-date").value = localDate(0);
$("#ev-date").value = localDate(5);
$("#fc-start").value = localDate(0);
$("#fc-end").value = localDate(30);
$("#sum-month").value = localDate(0).slice(0, 7);

refreshAll();
