"""Paper-trading runner: drives the validated ENGINE system on the Alpaca
paper account (ENGINE-BOT), mirroring the backtest's timing model.

Usage:
  paper_bot.py plan [--no-fetch]   evening run (after close / before 9:28 ET):
                                   refresh data, compute signals, submit
                                   market-on-open (OPG) orders for tomorrow
  paper_bot.py news                pre-open run (~9:00 ET): read overnight news
                                   via Claude; VETO-ONLY — may cancel pending
                                   entry orders, never creates trades or touches
                                   held positions. Every verdict is logged so
                                   the news layer builds its own track record.
                                   Skips gracefully without ANTHROPIC_API_KEY.
  paper_bot.py arm                 morning run (after 9:35 ET): place GTC stop
                                   orders for newly filled entries, reconcile
  paper_bot.py status              account + positions + state overview
  paper_bot.py brief               weekly performance email (Fridays post-close
                                   in the cloud): equity vs SPY, positions,
                                   the week's trades and news vetoes. Needs
                                   BRIEF_EMAIL + GMAIL_APP_PASSWORD; skips
                                   gracefully without them.
  paper_bot.py rearm               clear a drawdown halt after human review

Risk rules mirror engine/risk.py and are enforced against live account equity:
-3% day -> flatten + one-day stand-down; -20% from peak -> flatten + halt
(requires manual `rearm`). PAPER ONLY: TradingClient is pinned to paper=True.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import os

HERE = Path(__file__).parent
STATE_FILE = HERE / "state" / "paper_state.json"
TRADE_LOG = HERE / "state" / "trade_log.csv"
ENV_FILE = Path(os.environ.get("ALPACA_ENV_FILE", HERE.parent / "ALPACA" / "apikeys.env"))

UNIVERSE_FILE = HERE / "data" / "universe.txt"
INDEX = "SPY"


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def clients():
    load_dotenv(ENV_FILE)
    from alpaca.trading.client import TradingClient
    key = os.getenv("ALPACA_API_KEY_ID")
    sec = os.getenv("ALPACA_API_SECRET_KEY")
    if not key or not sec:
        sys.exit(f"missing keys in {ENV_FILE}")
    return TradingClient(key, sec, paper=True)  # paper=True is non-negotiable


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"positions": {}, "pending": {}, "equity_peak": None,
            "last_equity": None, "standdown": False, "halted": False}


def save_state(st):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, indent=2))


def log_trade(row: dict):
    TRADE_LOG.parent.mkdir(exist_ok=True)
    new = not TRADE_LOG.exists()
    with open(TRADE_LOG, "a") as f:
        if new:
            f.write(",".join(row.keys()) + "\n")
        f.write(",".join(str(v) for v in row.values()) + "\n")


def flatten_all(trading, st, reason):
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    trading.cancel_orders()
    for p in trading.get_all_positions():
        side = OrderSide.SELL if float(p.qty) > 0 else OrderSide.BUY
        trading.submit_order(MarketOrderRequest(
            symbol=p.symbol, qty=str(abs(float(p.qty))), side=side,
            time_in_force=TimeInForce.OPG))
        log(f"FLATTEN {p.symbol} qty={p.qty} ({reason})")
        log_trade({"ts": datetime.now(timezone.utc).isoformat(), "action": "flatten",
                   "symbol": p.symbol, "qty": p.qty, "reason": reason})
    st["positions"] = {}
    st["pending"] = {}


def cmd_status():
    trading = clients()
    st = load_state()
    a = trading.get_account()
    print(f"account {a.account_number}  equity ${a.equity}  cash ${a.cash}")
    print(f"halted={st['halted']} standdown={st['standdown']} peak={st['equity_peak']}")
    for p in trading.get_all_positions():
        meta = st["positions"].get(p.symbol, {})
        print(f"  {p.symbol:6s} qty={p.qty:>10s} pnl=${float(p.unrealized_pl):,.0f} "
              f"strategy={meta.get('strategy', '?')} stop={meta.get('stop', '?')}")
    open_orders = trading.get_orders()
    for o in open_orders:
        print(f"  order: {o.side} {o.qty or o.notional} {o.symbol} {o.type} {o.time_in_force} [{o.status}]")


def _fetch_alpaca_news(symbols: list[str], hours: int = 18) -> list[dict]:
    """Overnight news from Alpaca's news API (works with paper keys)."""
    import json as _json
    import ssl
    import urllib.parse
    import urllib.request

    import certifi
    from datetime import timedelta

    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    params = urllib.parse.urlencode({
        "symbols": ",".join(symbols), "start": start, "limit": "50",
        "include_content": "false", "sort": "desc",
    })
    req = urllib.request.Request(
        f"https://data.alpaca.markets/v1beta1/news?{params}",
        headers={
            "APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY_ID"),
            "APCA-API-SECRET-KEY": os.getenv("ALPACA_API_SECRET_KEY"),
        },
    )
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return _json.loads(r.read()).get("news", [])


NEWS_SCHEMA = {
    "type": "object",
    "properties": {
        "market_risk_off": {"type": "boolean"},
        "market_note": {"type": "string"},
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "action": {"type": "string", "enum": ["OK", "VETO"]},
                    "reason": {"type": "string"},
                },
                "required": ["symbol", "action", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["market_risk_off", "market_note", "verdicts"],
    "additionalProperties": False,
}


def cmd_news():
    """Pre-open news check. VETO-ONLY by design (research finding: an
    unbacktestable layer must never create trades — it may only block pending
    entries, and every verdict is logged to build an auditable track record)."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        load_dotenv(ENV_FILE)
    if not os.getenv("ANTHROPIC_API_KEY"):
        log("no ANTHROPIC_API_KEY — news layer skipped (bot trades on signals alone)")
        return

    trading = clients()
    st = load_state()
    pending = list(st["pending"].keys())
    held = list(st["positions"].keys())
    if not pending:
        log("no pending entries — nothing the news layer is allowed to act on")
        return

    news = _fetch_alpaca_news(sorted(set(pending + held + ["SPY"])))
    log(f"pending={pending} held={held} news_items={len(news)}")
    headlines = "\n".join(
        f"- [{n.get('created_at', '')[:16]}] ({', '.join(n.get('symbols', []))}) "
        f"{n.get('headline', '')} — {n.get('summary', '')[:200]}"
        for n in news
    ) or "(no overnight news found)"

    import anthropic
    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=16000,
            system=(
                "You are the pre-market news screen for an automated paper-trading "
                "system. Your ONLY power is to VETO planned entry orders before the "
                "open; you can never create trades or touch held positions. Veto a "
                "symbol only for material adverse company-specific news: earnings "
                "surprises or earnings due today, guidance cuts, SEC/DOJ "
                "investigations, fraud allegations, M&A that gaps the price, "
                "analyst-moving downgrades on the news itself. Set market_risk_off "
                "true only for genuine macro shocks (surprise rate action, major "
                "geopolitical escalation overnight, credit event) — not routine "
                "volatility or scheduled data. Default to OK: the system's edge is "
                "its signals; you are a narrow safety screen and false vetoes cost "
                "real performance. Give one verdict per PENDING symbol."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"PENDING entry orders for today's open (the only symbols you may veto): {pending}\n"
                    f"HELD positions (context only, no action possible): {held}\n\n"
                    f"Overnight news:\n{headlines}"
                ),
            }],
            output_config={"format": {"type": "json_schema", "schema": NEWS_SCHEMA}},
        )
    except Exception as e:
        # the news screen is optional: billing issues, outages, or API errors
        # must never block the trading pipeline — fail open, keep the orders
        log(f"news layer error ({e}); failing open — pending orders unchanged")
        return
    if response.stop_reason == "refusal":
        log("news model declined; failing open (no vetoes)")
        return
    import json as _json
    verdict = _json.loads(next(b.text for b in response.content if b.type == "text"))

    NEWS_LOG = STATE_FILE.parent / "news_log.jsonl"
    NEWS_LOG.parent.mkdir(exist_ok=True)
    with open(NEWS_LOG, "a") as f:
        f.write(_json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "pending": pending, "held": held, "news_items": len(news),
            **verdict,
        }) + "\n")

    vetoed = {v["symbol"] for v in verdict["verdicts"] if v["action"] == "VETO"}
    if verdict["market_risk_off"]:
        log(f"RISK-OFF: {verdict['market_note']} — vetoing all pending entries")
        vetoed = set(pending)
    if not vetoed:
        log("news screen: all pending entries OK")
        return

    open_orders = {o.symbol: o for o in trading.get_orders()}
    for sym in vetoed & set(pending):
        reason = next((v["reason"] for v in verdict["verdicts"] if v["symbol"] == sym),
                      verdict["market_note"])
        if sym in open_orders:
            trading.cancel_order_by_id(open_orders[sym].id)
        del st["pending"][sym]
        log(f"VETO {sym}: {reason}")
        log_trade({"ts": datetime.now(timezone.utc).isoformat(), "action": "news-veto",
                   "symbol": sym, "qty": "", "reason": reason.replace(",", ";")})
    save_state(st)


def cmd_brief():
    """Weekly performance email. Read-only: reports, never trades."""
    import smtplib
    from datetime import timedelta
    from email.mime.text import MIMEText

    load_dotenv(ENV_FILE)
    to_addr = (os.getenv("BRIEF_EMAIL") or "").strip()
    # Google displays app passwords with spaces; SMTP wants them without
    app_pw = (os.getenv("GMAIL_APP_PASSWORD") or "").replace(" ", "").strip()
    sender = os.getenv("GMAIL_USER") or to_addr  # SMTP login account; defaults to recipient
    if not to_addr or not app_pw:
        log("BRIEF_EMAIL / GMAIL_APP_PASSWORD not set — brief skipped")
        return

    from alpaca.trading.requests import GetOrdersRequest, GetPortfolioHistoryRequest
    from alpaca.trading.enums import QueryOrderStatus

    trading = clients()
    st = load_state()
    acct = trading.get_account()
    equity = float(acct.equity)
    now = datetime.now(timezone.utc)

    # --- week-over-week equity ---
    hist = trading.get_portfolio_history(GetPortfolioHistoryRequest(period="1M", timeframe="1D"))
    eq_series = [e for e in (hist.equity or []) if e]
    week_ago_eq = eq_series[-6] if len(eq_series) >= 6 else (eq_series[0] if eq_series else equity)
    week_ret = equity / week_ago_eq - 1 if week_ago_eq else 0.0

    # --- SPY same-week comparison (best effort) ---
    spy_line = ""
    try:
        import fetch
        fetch.fetch("SPY")
        closes = [float(r.split(",")[4]) for r in
                  (HERE / "data" / "spy_1d.csv").read_text().splitlines()[1:]]
        if len(closes) >= 6:
            spy_line = f"  (SPY same period: {(closes[-1] / closes[-6] - 1) * 100:+.2f}%)"
    except Exception:
        pass

    # --- open positions ---
    positions = trading.get_all_positions()
    pos_lines = [
        f"  {p.symbol:6s} {float(p.qty):>8.0f} @ ${float(p.avg_entry_price):,.2f}"
        f"  now ${float(p.current_price):,.2f}  P/L ${float(p.unrealized_pl):+,.0f}"
        f" ({float(p.unrealized_plpc) * 100:+.1f}%)  [{st['positions'].get(p.symbol, {}).get('strategy', '?')}]"
        for p in positions
    ] or ["  (none — in cash)"]

    # --- orders filled this week ---
    week_ago = now - timedelta(days=7)
    orders = trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED,
                                                 after=week_ago, limit=200))
    fills = [o for o in orders if o.filled_at]
    fill_lines = [
        f"  {str(o.filled_at)[:10]}  {str(o.side).split('.')[-1]:4s} "
        f"{float(o.filled_qty):>8.0f} {o.symbol:6s} @ ${float(o.filled_avg_price):,.2f}"
        for o in sorted(fills, key=lambda o: str(o.filled_at))
    ] or ["  (no fills this week)"]

    # --- news screen verdicts this week ---
    import json as _json
    news_lines = []
    news_log = STATE_FILE.parent / "news_log.jsonl"
    if news_log.exists():
        for line in news_log.read_text().splitlines():
            entry = _json.loads(line)
            if entry.get("ts", "") >= week_ago.isoformat():
                vetoes = [v for v in entry.get("verdicts", []) if v["action"] == "VETO"]
                for v in vetoes:
                    news_lines.append(f"  VETO {v['symbol']}: {v['reason'][:120]}")
                if not vetoes:
                    news_lines.append(f"  {entry['ts'][:10]}: all {len(entry.get('pending', []))} pending entries cleared")
    if not news_lines:
        news_lines = ["  (no news-screen runs this week)"]

    peak = st.get("equity_peak") or equity
    dd = equity / peak - 1 if peak else 0.0
    flags = []
    if st.get("halted"):
        flags.append("HALTED — needs manual rearm")
    if st.get("standdown"):
        flags.append("standing down (daily loss limit)")

    # --- evolution activity this week ---
    evo_lines = []
    evo_log = STATE_FILE.parent / "evolution_log.jsonl"
    if evo_log.exists():
        for line in evo_log.read_text().splitlines():
            e = _json.loads(line)
            if e.get("ts", "") >= week_ago.isoformat():
                c = e.get("candidate") or {}
                delta = (f" (candidate CAGR {c.get('cagr', 0) * 100:.1f}% vs incumbent "
                         f"{e.get('incumbent', {}).get('cagr', 0) * 100:.1f}%)") if c else ""
                evo_lines.append(f"  [{e['verdict']}] {e['name']}: {e['rationale'][:110]}{delta}")
    if not evo_lines:
        evo_lines = ["  (no evolution runs this week)"]

    body = "\n".join([
        f"KNIGHTTRADER WEEKLY BRIEF — {now.date().isoformat()}",
        "=" * 46,
        "",
        f"Equity:    ${equity:,.2f}",
        f"Week:      {week_ret * 100:+.2f}%{spy_line}",
        f"Drawdown:  {dd * 100:.1f}% from peak (${peak:,.0f})",
        f"Status:    {'; '.join(flags) if flags else 'normal operation'}",
        "",
        f"OPEN POSITIONS ({len(positions)})",
        *pos_lines,
        "",
        f"FILLS THIS WEEK ({len(fills)})",
        *fill_lines,
        "",
        "NEWS SCREEN",
        *news_lines,
        "",
        "EVOLUTION LAB (self-modification is live; adoptions auto-ship)",
        *evo_lines,
        "",
        "-" * 46,
        "Paper money. Not financial advice. Full audit trail:",
        "https://github.com/alexknightstudio/trading-engine",
        "https://alexknightprojects.com/bot/",
    ])

    msg = MIMEText(body)
    msg["Subject"] = f"KnightTrader weekly: ${equity:,.0f} ({week_ret * 100:+.1f}%)"
    msg["From"] = sender
    msg["To"] = to_addr
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(sender, app_pw)
        smtp.send_message(msg)
    log(f"brief sent to {to_addr}: equity ${equity:,.0f}, week {week_ret * 100:+.2f}%")


def cmd_rearm():
    st = load_state()
    st["halted"] = False
    st["equity_peak"] = None  # re-arm with a fresh high-water mark
    save_state(st)
    log("halt cleared; peak reset. Next `plan` run resumes trading.")


def cmd_arm():
    """Morning: place GTC stops for filled entries, reconcile stopped-out positions."""
    from alpaca.trading.requests import StopOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    trading = clients()
    st = load_state()
    held = {p.symbol: p for p in trading.get_all_positions()}

    # entries submitted last night that filled this open
    for sym, meta in list(st["pending"].items()):
        if sym in held:
            fill = float(held[sym].avg_entry_price)
            stop_price = round(fill - meta["stop_dist"], 2)
            o = trading.submit_order(StopOrderRequest(
                symbol=sym, qty=str(abs(float(held[sym].qty))), side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC, stop_price=stop_price))
            st["positions"][sym] = {**meta, "entry_price": fill, "stop": stop_price,
                                    "bars_held": 0, "stop_order_id": str(o.id)}
            log(f"ARMED {sym}: entry={fill} stop={stop_price}")
            log_trade({"ts": datetime.now(timezone.utc).isoformat(), "action": "entry",
                       "symbol": sym, "qty": held[sym].qty, "reason": meta["strategy"]})
        else:
            log(f"pending {sym} did not fill; dropping")
        del st["pending"][sym]

    # positions gone from the broker = stopped out or exited
    for sym in list(st["positions"]):
        if sym not in held:
            log(f"{sym} no longer held (stop or exit filled)")
            log_trade({"ts": datetime.now(timezone.utc).isoformat(), "action": "exit",
                       "symbol": sym, "qty": "", "reason": "stop-or-signal"})
            del st["positions"][sym]
    save_state(st)


def cmd_plan(no_fetch=False):
    """Evening: refresh data, compute signals, submit OPG orders for tomorrow."""
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    sys.path.insert(0, str(HERE))
    from engine.data import load_universe
    from engine.lineup import FACTORIES
    from engine.regime import apply_mania_guard, classify
    from engine.risk import RiskConfig, position_size

    trading = clients()
    st = load_state()
    cfg = RiskConfig()

    if st["halted"]:
        log("HALTED — run `paper_bot.py rearm` after review to resume")
        return
    # idempotency: a second plan run the same day would duplicate OPG orders
    today_utc = datetime.now(timezone.utc).date().isoformat()
    if st.get("last_plan_run") == today_utc and "--force" not in sys.argv:
        log(f"plan already ran {today_utc}; skipping (use --force to override)")
        return

    # ---- account-level risk checks against live equity ----
    equity = float(trading.get_account().equity)
    peak = st["equity_peak"] or equity
    peak = max(peak, equity)
    st["equity_peak"] = peak
    day_ret = equity / st["last_equity"] - 1 if st["last_equity"] else 0.0
    st["last_equity"] = equity
    log(f"equity=${equity:,.0f} peak=${peak:,.0f} day_ret={day_ret*100:.2f}%")

    if equity / peak - 1 <= -cfg.max_drawdown_halt:
        flatten_all(trading, st, "drawdown-halt")
        st["halted"] = True
        save_state(st)
        log("DRAWDOWN HALT — flattened. Human review + `rearm` required.")
        return
    if day_ret <= -cfg.daily_loss_limit:
        flatten_all(trading, st, "daily-loss")
        st["standdown"] = True
        save_state(st)
        log("daily loss limit hit — flattened, standing down one day")
        return

    # ---- data + signals (same code path as the backtest) ----
    if not no_fetch:
        log("refreshing data (~5 min for full universe)...")
        import fetch
        for sym in dict.fromkeys(fetch.ETFS + UNIVERSE_FILE.read_text().split()):
            try:
                fetch.fetch(sym)
            except Exception as e:
                log(f"  fetch {sym} failed: {e}")
        log("data refreshed")

    universe = UNIVERSE_FILE.read_text().split()
    bars = load_universe(universe)
    regime = classify(bars[INDEX])
    regime = apply_mania_guard(regime, bars["QQQ"]["close"])
    today = bars[INDEX].index[-1]
    reg_today = regime.iloc[-1]
    qqq = bars["QQQ"]["close"]
    ext = qqq.iloc[-1] / qqq.rolling(200).mean().iloc[-1] - 1
    log(f"last bar {today.date()}  regime={reg_today}  QQQ ext={ext * 100:+.1f}% (mania at +30%)")

    held = {p.symbol: p for p in trading.get_all_positions()}
    factories = FACTORIES

    # exits for held positions (signal or time stop)
    exits = []
    for sym, meta in st["positions"].items():
        if sym not in held or sym not in bars:
            continue
        for make in factories:
            sig = make(bars[sym], regime)
            if sig.strategy != meta["strategy"] or today not in sig.frame.index:
                continue
            row = sig.frame.loc[today]
            meta["bars_held"] = meta.get("bars_held", 0) + 1
            time_up = sig.max_hold_days and meta["bars_held"] >= sig.max_hold_days
            if row["exit_long"] or time_up:
                exits.append(sym)
    for sym in exits:
        try:
            if st["positions"][sym].get("stop_order_id"):
                trading.cancel_order_by_id(st["positions"][sym]["stop_order_id"])
        except Exception:
            pass
        trading.submit_order(MarketOrderRequest(
            symbol=sym, qty=str(abs(float(held[sym].qty))), side=OrderSide.SELL,
            time_in_force=TimeInForce.OPG))
        log(f"EXIT queued for open: {sym}")

    # entries: rank candidates round-robin, fill free slots
    if st["standdown"]:
        st["standdown"] = False
        log("stand-down day: no new entries")
    else:
        by_strat = {}
        for sym in universe:
            if sym in held or sym in exits or sym in st["pending"] or sym not in bars:
                continue
            for make in factories:
                sig = make(bars[sym], regime)
                if today not in sig.frame.index:
                    continue
                row = sig.frame.loc[today]
                if row["entry_long"] and row["stop_dist"] > 0:
                    by_strat.setdefault(sig.strategy, []).append(
                        (row["score_long"], sym, row["stop_dist"], row["size_scale"], sig.strategy))
        # NOTE: a focus-priority sort was tested 2026-08-09 and REJECTED — it
        # halved backtest CAGR (see README findings). Plain score ranking stays.
        for lst in by_strat.values():
            lst.sort(reverse=True)
        slots = cfg.max_positions - (len(held) - len(exits)) - len(st["pending"])
        picks = []
        while slots > len(picks) and any(by_strat.values()):
            for lst in by_strat.values():
                if lst and slots > len(picks):
                    picks.append(lst.pop(0))
        for score, sym, stop_dist, size_scale, strategy in picks:
            price = float(bars[sym]["close"].iloc[-1])
            qty = position_size(equity, price, price - stop_dist, cfg, size_scale)
            if qty <= 0:
                continue
            trading.submit_order(MarketOrderRequest(
                symbol=sym, qty=str(qty), side=OrderSide.BUY, time_in_force=TimeInForce.OPG))
            st["pending"][sym] = {"strategy": strategy, "stop_dist": float(stop_dist),
                                  "queued": str(today.date())}
            log(f"ENTRY queued for open: BUY {qty} {sym} (strategy={strategy}, score={score:.2f})")

    st["last_plan_run"] = today_utc
    save_state(st)
    log("plan complete")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "plan":
        cmd_plan(no_fetch="--no-fetch" in sys.argv)
    elif cmd == "news":
        cmd_news()
    elif cmd == "arm":
        cmd_arm()
    elif cmd == "brief":
        cmd_brief()
    elif cmd == "status":
        cmd_status()
    elif cmd == "rearm":
        cmd_rearm()
    else:
        sys.exit(__doc__)
