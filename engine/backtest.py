"""Event-driven daily backtester.

Timing model (no lookahead):
  - Signals are computed on day T's close.
  - Entries/exits fill at day T+1's open, +/- slippage.
  - Stops are checked against each day's high/low; a gap through the stop
    fills at the open (the realistic, worse price).

Risk rules from engine.risk are enforced here: per-trade sizing, max
positions, daily loss stand-down, and a portfolio drawdown halt.
"""
from dataclasses import dataclass, field

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


def run(bars: dict[str, pd.DataFrame], signals: dict[str, list[Signals]],
        dates: pd.DatetimeIndex, regime: pd.Series,
        start_equity: float = 100_000, cfg: RiskConfig = RiskConfig()) -> Result:
    slip = cfg.slippage_bps / 10_000
    cash = start_equity
    positions: dict[str, Position] = {}
    trades: list[Trade] = []
    equity_curve: dict[pd.Timestamp, float] = {}
    queued: list[_Order] = []
    peak = start_equity
    prev_equity = start_equity
    stand_down = 0  # days remaining with entries blocked
    halts: list[pd.Timestamp] = []
    halt_cooldown = 0  # days remaining flat after a drawdown halt
    standdown_events = 0

    def mark(day) -> float:
        val = cash
        for p in positions.values():
            df = bars[p.symbol]
            if day in df.index:
                val += p.qty * df.loc[day, "close"]
            else:
                val += p.qty * df["close"].asof(day)
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

    for day in dates:
        if halt_cooldown > 0:
            halt_cooldown -= 1
            equity_curve[day] = mark(day)
            if halt_cooldown == 0:
                peak = mark(day)  # re-arm with a fresh high-water mark
                prev_equity = peak
            continue

        # ---- 1) fill queued orders at today's open ----
        for od in queued:
            df = bars.get(od.symbol)
            if df is None or day not in df.index:
                continue
            o = df.loc[day, "open"]
            if od.side == 0:
                if od.symbol in positions:
                    close_position(positions[od.symbol], o, day, od.reason)
            elif od.symbol not in positions and stand_down == 0 and len(positions) < cfg.max_positions:
                fill = o * (1 + slip) if od.side > 0 else o * (1 - slip)
                stop = fill - od.side * od.stop_dist
                qty = position_size(prev_equity, fill, stop, cfg, od.size_scale)
                if qty > 0:
                    positions[od.symbol] = Position(
                        od.symbol, od.strategy, od.side * qty, fill, stop, day,
                        entry_regime=str(regime.asof(day)),
                    )
                    cash -= od.side * qty * fill
        queued = []

        # ---- 2) intraday stop checks ----
        for p in list(positions.values()):
            df = bars[p.symbol]
            if day not in df.index:
                continue
            row = df.loc[day]
            if p.qty > 0 and row["low"] <= p.stop:
                close_position(p, min(row["open"], p.stop), day, "stop")
            elif p.qty < 0 and row["high"] >= p.stop:
                close_position(p, max(row["open"], p.stop), day, "stop")

        # ---- 3) mark to market, portfolio-level risk ----
        equity = mark(day)
        equity_curve[day] = equity
        for p in positions.values():
            p.bars_held += 1

        if stand_down > 0:
            stand_down -= 1

        day_ret = equity / prev_equity - 1
        peak = max(peak, equity)
        if equity / peak - 1 <= -cfg.max_drawdown_halt:
            for p in list(positions.values()):
                df = bars[p.symbol]
                px = df.loc[day, "close"] if day in df.index else df["close"].asof(day)
                close_position(p, px, day, "halt")
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
        for sym, sig_list in signals.items():
            df = bars[sym]
            if day not in df.index:
                continue
            pos = positions.get(sym)
            for sig in sig_list:
                if day not in sig.frame.index:
                    continue
                row = sig.frame.loc[day]
                if pos is not None and pos.strategy == sig.strategy:
                    time_up = sig.max_hold_days and pos.bars_held >= sig.max_hold_days
                    if pos.qty > 0 and (row["exit_long"] or time_up):
                        queued.append(_Order(sym, sig.strategy, 0, "time" if time_up and not row["exit_long"] else "signal"))
                    elif pos.qty < 0 and (row["exit_short"] or time_up):
                        queued.append(_Order(sym, sig.strategy, 0, "time" if time_up and not row["exit_short"] else "signal"))
                elif pos is None and stand_down == 0:
                    if row["entry_long"]:
                        queued.append(_Order(sym, sig.strategy, +1, "", row["size_scale"], row["stop_dist"]))
                    elif row["entry_short"]:
                        queued.append(_Order(sym, sig.strategy, -1, "", row["size_scale"], row["stop_dist"]))

    return Result(pd.Series(equity_curve), trades, halts, standdown_events)
