"use strict";

const TIER_HELP = {
  0: "Read-only: the assistant can analyse but cannot request transfers.",
  1: "Draft only: every transfer waits for your Confirm click.",
  2: "Limited autonomy: transfers up to your auto-execute limit run on their own; larger ones wait for you."
};

const STARTERS = [
  "What is the cheapest way to send 8000 CNY?",
  "I need 5000 yuan for rent next week",
  "Add expense: lunch 25 CNY today, food"
];

let STATE = null;
let BUSY = false;
let LAST_MANDATE_KEY = "";
let SUMMARY_SHOWN = false;
let FORECAST_SHOWN = false;

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

function toast(msg, isError) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { t.className = "toast"; }, 4500);
}

async function api(path, method, body, timeoutMs) {
  const opt = { method: method || "GET", headers: {} };
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
  let r;
  try {
    r = await fetch(path, opt);
  } catch (e) {
    if (e && e.name === "AbortError") {
      throw new Error("No answer after " + Math.round(timeoutMs / 1000) +
        " seconds. The AI model may be slow or unreachable. Check the terminal window for details.");
    }
    throw new Error("Cannot reach the server. Is it still running?");
  } finally {
    if (timer) clearTimeout(timer);
  }
  let data = null;
  try { data = await r.json(); } catch (e) { /* no body */ }
  if (!r.ok) {
    const d = data && data.detail;
    if (typeof d === "string") throw new Error(d);
    throw new Error(r.status === 422
      ? "Invalid input. Check the values you entered."
      : "Server error (" + r.status + "). See the terminal window.");
  }
  return data;
}

function table(headers, rows, bestFirst) {
  const wrap = el("div", "table-wrap");
  const t = el("table");
  const thead = el("thead");
  const hr = el("tr");
  headers.forEach((h) => hr.appendChild(el("th", "", h)));
  thead.appendChild(hr);
  t.appendChild(thead);
  const tbody = el("tbody");
  rows.forEach((r, i) => {
    const tr = el("tr", bestFirst && i === 0 ? "best" : "");
    r.forEach((c) => {
      const td = el("td");
      if (c instanceof Node) td.appendChild(c); else td.textContent = String(c);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  t.appendChild(tbody);
  wrap.appendChild(t);
  return wrap;
}

function ensureBox(panelSel, id, title) {
  let box = $("#" + id);
  if (!box) {
    const panel = $(panelSel);
    panel.appendChild(el("h3", "section-title", title));
    box = el("div");
    box.id = id;
    panel.appendChild(box);
  }
  return box;
}

/* ---------- state rendering ---------- */
async function refreshState() {
  STATE = await api("/api/state");
  renderState();
}

async function refreshAll() {
  try {
    await refreshState();
    if (SUMMARY_SHOWN) await loadSummary();
    if (FORECAST_SHOWN) await loadForecast();
    if (!$("#tab-activity").classList.contains("hidden")) await loadLog();
  } catch (e) {
    toast(e.message, true);
  }
}

function modeText(s) {
  return s.llm_mode.startsWith("gemini") ? s.llm_mode.replace("gemini:", "") : "demo (no AI key)";
}

function renderState() {
  const s = STATE;
  $("#rate-chip").textContent =
    "1 CNY = " + s.rate.pkr_per_cny + " PKR · reference rate as of " +
    s.rate.as_of + (s.rate.from_cache ? " (saved copy)" : "");
  $("#mode-chip").textContent = "AI: " + modeText(s);
  $("#bal-pkr").textContent = fmt(s.balances.pkr);
  $("#bal-cny").textContent = fmt(s.balances.cny);

  document.querySelectorAll(".tier-pill").forEach((b) => {
    const on = Number(b.dataset.tier) === s.tier;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
  $("#tier-help").textContent = TIER_HELP[s.tier];

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

function renderLists() {
  const eb = ensureBox("#tab-spending", "expense-list", "Recorded expenses");
  eb.textContent = "";
  if (!STATE.expenses.length) {
    eb.appendChild(el("p", "muted", "No expenses recorded yet."));
  } else {
    eb.appendChild(table(["Date", "Name", "Category", "Amount"],
      STATE.expenses.slice().reverse().map((e) =>
        [e.on, e.name, e.category, fmt(e.amount_cny) + " CNY"])));
  }

  const vb = ensureBox("#tab-forecast", "event-list", "Recorded bills and money in");
  vb.textContent = "";
  if (!STATE.events.length) {
    vb.appendChild(el("p", "muted", "Nothing recorded yet."));
  } else {
    vb.appendChild(table(["Date", "Name", "Type", "Amount"],
      STATE.events.slice().sort((a, b) => (a.on < b.on ? -1 : 1)).map((e) =>
        [e.on, e.name, e.kind === "inflow" ? "Money in" : "Bill",
          fmt(e.amount_cny) + " CNY"])));
  }
}

function renderTrust() {
  const s = STATE;
  const total = s.routes.length;
  const illus = s.routes.filter((r) => r.illustrative).length;
  const dl = $("#trust-info");
  dl.textContent = "";
  [
    ["Exchange rate", "1 CNY = " + s.rate.pkr_per_cny + " PKR"],
    ["Rate date", s.rate.as_of + (s.rate.from_cache ? " (saved copy)" : "")],
    ["Rate source", s.rate.source],
    ["Fee data", illus + " of " + total + " routes illustrative"],
    ["Assistant's date", s.today],
    ["AI", modeText(s)]
  ].forEach(([k, v]) => { dl.appendChild(el("dt", "", k)); dl.appendChild(el("dd", "", v)); });
  $("#trust-note").textContent = illus > 0
    ? "Illustrative routes are not fully sourced (for example the exchange-rate markup may be missing). Do not use them to choose a real transfer."
    : "";
}

function renderPending() {
  const box = $("#pending-list");
  box.textContent = "";
  $("#pending-count").textContent = String(STATE.pending.length);
  if (!STATE.pending.length) {
    box.appendChild(el("p", "muted", "Nothing is waiting for you."));
    return;
  }
  STATE.pending.forEach((p) => {
    const card = el("div", "pending-card");
    card.appendChild(el("div", "pending-title", p.transfer_id + " · " + p.route));
    card.appendChild(el("div", "muted",
      fmt(p.cny_to_deliver) + " CNY delivered · " + fmt(p.total_pkr) + " PKR"));
    const row = el("div", "btn-row");
    const ok = el("button", "btn primary small", "Confirm");
    ok.addEventListener("click", () => decide(p.transfer_id, "confirm"));
    const no = el("button", "btn ghost small", "Reject");
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
      (r.reasons && r.reasons.length ? " (" + r.reasons.join("; ") + ")" : ""), isErr);
  } catch (e) {
    toast(e.message, true);
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

  const rows = STATE.routes.map((r) => {
    const badge = el("span", "badge " + (r.illustrative ? "warn" : "ok"),
      r.illustrative ? "illustrative" : "sourced");
    return [r.name, r.route_type, r.typical_days == null ? "n/a" : r.typical_days + " days",
      r.supports_tuition ? "yes" : "no", badge, r.source + " (as of " + r.as_of + ")"];
  });
  const box = $("#routes-table");
  box.textContent = "";
  box.appendChild(table(["Route", "Type", "Typical time", "Tuition", "Data", "Source"], rows));
}

function renderQuotes() {
  const box = $("#quotes-table");
  box.textContent = "";
  if (!STATE.my_quotes.length) {
    box.appendChild(el("p", "muted", "No quotes yet."));
    return;
  }
  const rows = STATE.my_quotes.map((q) => [
    q.provider, q.on, fmt(q.pkr_paid) + " PKR", fmt(q.cny_received) + " CNY",
    q.effective_rate, fmt(q.hidden_cost_pkr) + " PKR (" + pct(q.hidden_cost_pct) + ")"
  ]);
  box.appendChild(table(
    ["Provider", "Date", "You pay", "You get", "Effective rate", "Hidden cost vs mid-market"], rows));
}

/* ---------- chat ---------- */
function addBubble(role) {
  const b = el("div", "bubble " + role);
  const log = $("#chat-log");
  log.appendChild(b);
  log.scrollTop = log.scrollHeight;
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
  const box = el("div", "meta");
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
    const d = el("div", "", line);
    if (bad) { d.style.color = "#b91c1c"; d.style.fontWeight = "600"; }
    box.appendChild(d);
  });
  return box;
}

function traceView(trace) {
  const d = el("details");
  d.appendChild(el("summary", "", "How I got this (" + trace.length + " tool call" +
    (trace.length === 1 ? "" : "s") + ")"));
  trace.forEach((t) => {
    d.appendChild(el("strong", "", t.tool));
    d.appendChild(el("pre", "", "args: " + JSON.stringify(t.args, null, 2)));
    d.appendChild(el("pre", "", "result: " + JSON.stringify(t.result, null, 2)));
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
  a.classList.add("pending-bubble");
  setBusy(true);
  const started = Date.now();
  const timer = setInterval(() => {
    a.textContent = "Thinking… " + Math.round((Date.now() - started) / 1000) + "s";
  }, 1000);
  try {
    const out = await api("/api/chat", "POST", { message: text }, 150000);
    clearInterval(timer);
    a.classList.remove("pending-bubble");
    a.textContent = "";
    richText(a, out.text || "(no reply)");
    if (out.trace && out.trace.length) {
      a.appendChild(toolStatus(out.trace));
      a.appendChild(traceView(out.trace));
    }
    if (out.seconds !== undefined) a.appendChild(el("div", "meta", "answered in " + out.seconds + "s"));
  } catch (e) {
    clearInterval(timer);
    a.classList.remove("pending-bubble");
    a.classList.add("error");
    a.textContent = e.message;
  } finally {
    clearInterval(timer);
    setBusy(false);
    $("#chat-input").focus();
  }
  $("#chat-log").scrollTop = $("#chat-log").scrollHeight;
  await refreshAll();
}

$("#chat-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const input = $("#chat-input");
  const text = input.value;
  input.value = "";
  sendMessage(text);
});

STARTERS.forEach((s) => {
  const b = el("button", "starter", s);
  b.type = "button";
  b.addEventListener("click", () => sendMessage(s));
  $("#starters").appendChild(b);
});

document.querySelectorAll(".tier-pill").forEach((btn) => {
  btn.addEventListener("click", async () => {
    try {
      await api("/api/tier", "POST", { tier: Number(btn.dataset.tier) });
      toast("Permission tier updated");
    } catch (e) {
      toast(e.message, true);
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
    toast("Limits saved");
  } catch (e) {
    toast(e.message, true);
  }
  await refreshAll();
});

/* ---------- tabs ---------- */
const TAB_BTNS = Array.from(document.querySelectorAll("#tabs .tab"));

function activateTab(btn) {
  TAB_BTNS.forEach((b) => {
    const on = b === btn;
    b.classList.toggle("active", on);
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
      toast(r.error, true);
    } else {
      const bad = r.status === "blocked" || r.status === "insufficient_funds";
      toast("Transfer " + r.transfer_id + ": " + r.status.replace(/_/g, " ") +
        (r.reasons && r.reasons.length ? " (" + r.reasons.join("; ") + ")" : ""), bad);
    }
  } catch (e) {
    toast(e.message, true);
  }
  await refreshAll();
}

$("#compare-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const amount = $("#cmp-amount").value.trim();
  const type = $("#cmp-type").value;
  const args = { cny_to_deliver: amount };
  if (type) args.route_type = type;
  const box = $("#compare-result");
  box.textContent = "";
  try {
    const out = await api("/api/tool", "POST", { name: "compare_routes", args: args });
    if (out.error) {
      box.appendChild(el("p", "error-text", out.error));
      return;
    }
    if (!out.quotes.length) {
      box.appendChild(el("p", "muted", "No routes match."));
      return;
    }
    let anyIllustrative = false;
    const rows = out.quotes.map((q, i) => {
      const info = STATE.routes.find((r) => r.name === q.route);
      const nameCell = el("span", "", (i === 0 ? "★ " : "") + q.route);
      if (info && info.illustrative) {
        anyIllustrative = true;
        nameCell.appendChild(document.createTextNode(" "));
        nameCell.appendChild(el("span", "badge warn", "illustrative"));
      }
      const btn = el("button", "btn small", "Request transfer");
      btn.addEventListener("click", () => requestTransfer(q.route, amount));
      return [
        nameCell,
        q.route_type,
        q.typical_days == null ? "n/a" : q.typical_days + " days",
        fmt(q.total_pkr) + " PKR",
        q.effective_rate,
        fmt(q.hidden_cost_pkr) + " PKR (" + pct(q.hidden_cost_pct) + ")",
        q.fee_source + " (as of " + q.fees_as_of + ")",
        btn
      ];
    });
    box.appendChild(el("p", "muted",
      "Mid-market rate: " + out.mid_rate + " PKR per CNY. ★ = cheapest in this list."));
    if (anyIllustrative) {
      box.appendChild(el("p", "warn-text",
        "Some routes use illustrative fees, so this ranking is a demonstration, not advice."));
    }
    box.appendChild(table(
      ["Route", "Type", "Time", "You pay", "Effective rate", "Hidden cost", "Fee source", ""],
      rows, true));
  } catch (e) {
    toast(e.message, true);
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
    if (r.error) toast(r.error, true); else toast("Expense added (" + r.expense_count + " total)");
  } catch (e) {
    toast(e.message, true);
  }
  await refreshAll();
});

async function loadSummary() {
  const parts = $("#sum-month").value.split("-");
  if (parts.length !== 2) return false;
  const box = $("#summary-result");
  const s = await api("/api/tool", "POST", {
    name: "spending_summary",
    args: { year: Number(parts[0]), month: Number(parts[1]) }
  });
  box.textContent = "";
  if (s.error) { box.appendChild(el("p", "error-text", s.error)); return true; }
  const head = el("div", "result-box");
  head.appendChild(el("div", "", s.count + " expenses · total " + fmt(s.total_cny) + " CNY"));
  head.appendChild(el("div", "", "= " + fmt(s.total_pkr_mid) + " PKR at the mid-market rate"));
  head.appendChild(el("div", "", "= " + fmt(s.total_pkr_effective) +
    " PKR at the rate you actually paid"));
  head.appendChild(el("div", "", "Hidden cost: " + fmt(s.hidden_cost_pkr) + " PKR"));
  box.appendChild(head);
  if (s.categories.length) {
    box.appendChild(table(
      ["Category", "CNY", "PKR (mid-market)", "PKR (paid rate)", "Share"],
      s.categories.map((c) => [c.category, fmt(c.cny), fmt(c.pkr_mid),
        fmt(c.pkr_effective), pct(c.share)])));
  }
  return true;
}

$("#summary-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const shown = await loadSummary();
    if (shown) SUMMARY_SHOWN = true; else toast("Pick a month first", true);
  } catch (e) {
    toast(e.message, true);
  }
});

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
    if (r.error) toast(r.error, true); else toast("Added (" + r.event_count + " events total)");
  } catch (e) {
    toast(e.message, true);
  }
  await refreshAll();
});

async function loadForecast() {
  const start = $("#fc-start").value;
  const end = $("#fc-end").value;
  if (!start || !end) return false;
  const box = $("#forecast-result");
  const args = {
    start: start,
    end: end,
    buffer_cny: $("#fc-buffer").value.trim() || "0",
    lead_days: Number($("#fc-lead").value || 0)
  };
  const bal = $("#fc-balance").value.trim();
  if (bal) args.start_balance_cny = bal;
  const f = await api("/api/tool", "POST", { name: "forecast_cashflow", args: args });
  box.textContent = "";
  if (f.error) { box.appendChild(el("p", "error-text", f.error)); return true; }
  const short = f.first_shortfall_date !== null;
  const b = el("div", "result-box" + (short ? " alert" : ""));
  b.appendChild(el("div", "", "Lowest balance: " + fmt(f.min_balance_cny) +
    " CNY on " + f.min_balance_date));
  if (short) {
    b.appendChild(el("div", "", "First shortfall: " + f.first_shortfall_date));
    b.appendChild(el("div", "", "Top-up needed: " + fmt(f.topup_needed_cny) + " CNY"));
    b.appendChild(el("div", "", "Start the transfer by: " + f.transfer_by));
    if (f.is_late) {
      b.appendChild(el("div", "error-text",
        "This is already too late for the assumed transfer time."));
    }
  } else {
    b.appendChild(el("div", "", "No shortfall in this period."));
  }
  box.appendChild(b);
  return true;
}

$("#forecast-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const shown = await loadForecast();
    if (shown) FORECAST_SHOWN = true; else toast("Pick both dates first", true);
  } catch (e) {
    toast(e.message, true);
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
    toast("Quote analysed");
    await refreshAll();
  } catch (e) {
    toast(e.message, true);
  }
});

/* ---------- activity log ---------- */
async function loadLog() {
  const box = $("#log-table");
  try {
    const out = await api("/api/log");
    box.textContent = "";
    if (!out.entries.length) {
      box.appendChild(el("p", "muted", "Nothing recorded yet."));
      return;
    }
    const rows = out.entries.slice().reverse().map((e) => [
      e.ts.replace("T", " ").slice(0, 19), e.event, e.transfer_id, e.route,
      String(e.status).replace(/_/g, " "), amt(e.cny_to_deliver, "CNY"),
      amt(e.total_pkr, "PKR"), (e.reasons || []).join("; ")
    ]);
    box.appendChild(table(
      ["Time (UTC)", "Event", "ID", "Route", "Status", "CNY", "PKR", "Reasons"], rows));
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------- show unexpected errors instead of failing silently ---------- */
window.addEventListener("unhandledrejection", (ev) => {
  toast("Unexpected error: " + (ev.reason && ev.reason.message ? ev.reason.message : ev.reason), true);
});

/* ---------- start ---------- */
$("#exp-date").value = localDate(0);
$("#ev-date").value = localDate(5);
$("#fc-start").value = localDate(0);
$("#fc-end").value = localDate(30);
$("#sum-month").value = localDate(0).slice(0, 7);
refreshAll();