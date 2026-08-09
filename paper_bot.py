"""Paper-trading runner: drives the validated ENGINE system on the Alpaca
paper account (ENGINE-BOT), mirroring the backtest's timing model.

Usage:
  paper_bot.py plan [--no-fetch]   evening run (after close / before 9:28 ET):
                                   refresh data, compute signals, submit
                                   market-on-open (OPG) orders for tomorrow
  paper_bot.py arm                 morning run (after 9:35 ET): place GTC stop
                                   orders for newly filled entries, reconcile
  paper_bot.py status              account + positions + state overview
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
    from engine.regime import classify
    from engine.risk import RiskConfig, position_size
    from engine.strategies import mean_reversion_rsi2, momentum_donchian

    trading = clients()
    st = load_state()
    cfg = RiskConfig()

    if st["halted"]:
        log("HALTED — run `paper_bot.py rearm` after review to resume")
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
        for i, sym in enumerate(fetch.ETFS + open(UNIVERSE_FILE).read().split()):
            try:
                fetch.fetch(sym)
            except Exception as e:
                log(f"  fetch {sym} failed: {e}")
        log("data refreshed")

    universe = UNIVERSE_FILE.read_text().split()
    bars = load_universe(universe)
    regime = classify(bars[INDEX])
    today = bars[INDEX].index[-1]
    reg_today = regime.iloc[-1]
    log(f"last bar {today.date()}  regime={reg_today}")

    held = {p.symbol: p for p in trading.get_all_positions()}
    factories = [momentum_donchian, mean_reversion_rsi2]

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
            if sym in held or sym in exits or sym not in bars:
                continue
            for make in factories:
                sig = make(bars[sym], regime)
                if today not in sig.frame.index:
                    continue
                row = sig.frame.loc[today]
                if row["entry_long"] and row["stop_dist"] > 0:
                    by_strat.setdefault(sig.strategy, []).append(
                        (row["score_long"], sym, row["stop_dist"], row["size_scale"], sig.strategy))
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

    save_state(st)
    log("plan complete")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "plan":
        cmd_plan(no_fetch="--no-fetch" in sys.argv)
    elif cmd == "arm":
        cmd_arm()
    elif cmd == "status":
        cmd_status()
    elif cmd == "rearm":
        cmd_rearm()
    else:
        sys.exit(__doc__)
