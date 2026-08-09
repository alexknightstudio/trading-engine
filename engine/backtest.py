"""Event-driven daily backtester, array-based so a 500-symbol universe stays fast.

Timing model (no lookahead):
  - Signals are computed on day T's close.
  - Entries/exits fill at day T+1's open, +/- slippage.
  - Stops are checked against each day's high/low; a gap through the stop
    fills at the open (the realistic, worse price).

When more entry signals fire than there are free position slots, candidates
are ranked by their strategy's score column (strongest momentum / deepest
oversold first).

Risk rules from engine.risk are enforced here: per-trade sizing, max
positions, daily loss stand-down, and a portfolio drawdown halt with cooldown.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .risk import RiskConfig, position_size
from .strategies import Signals


@dataclass
class Position:
    symbol: str
    strategy: str
    qty: int  # signed: >0 long, <0 short
    entry_price: float
    stop: float
    entry_date: pd.Timestamp
    bars_held: int = 0
    entry_regime: str = ""


@dataclass
class Trade:
    symbol: str
    strategy: str
    direction: str
    qty: int
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    pnl: float
    reason: str
    entry_regime: str


@dataclass
class Result:
    equity: pd.Series
    trades: list[Trade]
    halts: list[pd.Timestamp]
    standdown_days: int


@dataclass
class _Order:
    symbol: str
    strategy: str
    side: int  # +1 open long, -1 open short, 0 close
    reason: str = ""
    size_scale: float = 1.0
    stop_dist: float = 0.0
    score: float = 0.0


class _Sym:
    """Per-symbol market + signal data as numpy arrays over the master dates."""

    def __init__(self, df: pd.DataFrame, sigs: list[Signals], dates: pd.DatetimeIndex):
        r = df.reindex(dates)
        self.open = r["open"].to_numpy()
        self.high = r["high"].to_numpy()
        self.low = r["low"].to_numpy()
        self.close_ff = r["close"].ffill().to_numpy()
        self.sigs = []
        for s in sigs:
            f = s.frame.reindex(dates)
            self.sigs.append({
                "strategy": s.strategy,
                "max_hold": s.max_hold_days,
                "el": f["entry_long"].fillna(False).to_numpy(dtype=bool),
                "es": f["entry_short"].fillna(False).to_numpy(dtype=bool),
                "xl": f["exit_long"].fillna(False).to_numpy(dtype=bool),
                "xs": f["exit_short"].fillna(False).to_numpy(dtype=bool),
                "scale": f["size_scale"].to_numpy(),
                "stopd": f["stop_dist"].to_numpy(),
                "scl": f["score_long"].to_numpy(),
                "ssc": f["score_short"].to_numpy(),
            })


def run(bars: dict[str, pd.DataFrame], signals: dict[str, list[Signals]],
        dates: pd.DatetimeIndex, regime: pd.Series,
        start_equity: float = 100_000, cfg: RiskConfig = RiskConfig()) -> Result:
    slip = cfg.slippage_bps / 10_000
    syms = {s: _Sym(bars[s], signals.get(s, []), dates) for s in bars}
    reg_arr = regime.reindex(dates).fillna("CHOP").to_numpy()

    cash = start_equity
    positions: dict[str, Position] = {}
    trades: list[Trade] = []
    equity_arr = np.empty(len(dates))
    queued: list[_Order] = []
    peak = start_equity
    prev_equity = start_equity
    stand_down = 0
    halts: list[pd.Timestamp] = []
    halt_cooldown = 0
    standdown_events = 0

    def mark(i: int) -> float:
        val = cash
        for p in positions.values():
            px = syms[p.symbol].close_ff[i]
            val += p.qty * (px if np.isfinite(px) else p.entry_price)
        return val

    def close_position(p: Position, price: float, day, reason: str):
        nonlocal cash
        fill = price * (1 - slip) if p.qty > 0 else price * (1 + slip)
        cash += p.qty * fill
        trades.append(Trade(
            p.symbol, p.strategy, "long" if p.qty > 0 else "short", abs(p.qty),
            p.entry_date, p.entry_price, day, fill,
            p.qty * (fill - p.entry_price), reason, p.entry_regime,
        ))
        del positions[p.symbol]

    for i, day in enumerate(dates):
        if halt_cooldown > 0:
            halt_cooldown -= 1
            equity_arr[i] = mark(i)
            if halt_cooldown == 0:
                peak = equity_arr[i]
                prev_equity = peak
            continue

        # ---- 1) fill queued orders at today's open: exits first, then
        #         entries ranked by score while slots remain ----
        exits = [o for o in queued if o.side == 0]
        # scores are only comparable within a strategy, so allocate slots
        # round-robin: each strategy's best candidate in turn
        by_strat: dict[str, list[_Order]] = {}
        for o in queued:
            if o.side != 0:
                by_strat.setdefault(o.strategy, []).append(o)
        for lst in by_strat.values():
            lst.sort(key=lambda o: o.score, reverse=True)
        entries = []
        while any(by_strat.values()):
            for lst in by_strat.values():
                if lst:
                    entries.append(lst.pop(0))
        for od in exits:
            o = syms[od.symbol].open[i]
            if np.isfinite(o) and od.symbol in positions:
                close_position(positions[od.symbol], o, day, od.reason)
        for od in entries:
            if stand_down > 0 or len(positions) >= cfg.max_positions:
                break
            o = syms[od.symbol].open[i]
            if not np.isfinite(o) or od.symbol in positions:
                continue
            fill = o * (1 + slip) if od.side > 0 else o * (1 - slip)
            stop = fill - od.side * od.stop_dist
            qty = position_size(prev_equity, fill, stop, cfg, od.size_scale)
            if qty > 0:
                positions[od.symbol] = Position(
                    od.symbol, od.strategy, od.side * qty, fill, stop, day,
                    entry_regime=str(reg_arr[i]),
                )
                cash -= od.side * qty * fill
        queued = []

        # ---- 2) intraday stop checks ----
        for p in list(positions.values()):
            sd = syms[p.symbol]
            lo, hi, op = sd.low[i], sd.high[i], sd.open[i]
            if not np.isfinite(lo):
                continue
            if p.qty > 0 and lo <= p.stop:
                close_position(p, min(op, p.stop), day, "stop")
            elif p.qty < 0 and hi >= p.stop:
                close_position(p, max(op, p.stop), day, "stop")

        # ---- 3) mark to market, portfolio-level risk ----
        equity = mark(i)
        equity_arr[i] = equity
        for p in positions.values():
            p.bars_held += 1
        if stand_down > 0:
            stand_down -= 1

        day_ret = equity / prev_equity - 1
        peak = max(peak, equity)
        if equity / peak - 1 <= -cfg.max_drawdown_halt:
            for p in list(positions.values()):
                px = syms[p.symbol].close_ff[i]
                close_position(p, px if np.isfinite(px) else p.entry_price, day, "halt")
            halts.append(day)
            halt_cooldown = cfg.halt_cooldown_days
            queued = []
            prev_equity = equity
            continue
        if day_ret <= -cfg.daily_loss_limit:
            queued = [_Order(s, positions[s].strategy, 0, "daily-loss-flatten") for s in positions]
            stand_down = 1
            standdown_events += 1
            prev_equity = equity
            continue
        prev_equity = equity

        # ---- 4) evaluate signals on today's close, queue for tomorrow ----
        for sym, sd in syms.items():
            pos = positions.get(sym)
            for sg in sd.sigs:
                if pos is not None and pos.strategy == sg["strategy"]:
                    time_up = sg["max_hold"] and pos.bars_held >= sg["max_hold"]
                    if pos.qty > 0 and (sg["xl"][i] or time_up):
                        queued.append(_Order(sym, sg["strategy"], 0,
                                             "time" if time_up and not sg["xl"][i] else "signal"))
                    elif pos.qty < 0 and (sg["xs"][i] or time_up):
                        queued.append(_Order(sym, sg["strategy"], 0,
                                             "time" if time_up and not sg["xs"][i] else "signal"))
                elif pos is None and stand_down == 0:
                    if sg["el"][i] and np.isfinite(sg["stopd"][i]):
                        queued.append(_Order(sym, sg["strategy"], +1, "",
                                             sg["scale"][i], sg["stopd"][i], sg["scl"][i]))
                    elif sg["es"][i] and np.isfinite(sg["stopd"][i]):
                        queued.append(_Order(sym, sg["strategy"], -1, "",
                                             sg["scale"][i], sg["stopd"][i], sg["ssc"][i]))

    return Result(pd.Series(equity_arr, index=dates), trades, halts, standdown_events)
