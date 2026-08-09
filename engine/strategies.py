"""Strategy layer. Each strategy emits boolean entry/exit signal columns per
symbol, evaluated on day T's close and executed by the backtester at T+1's
open. The regime gate is applied here: momentum trades with the trend, mean
reversion fades pullbacks with the trend (both directions at half size in CHOP).
"""
from dataclasses import dataclass

import pandas as pd

from .indicators import atr, rsi, sma
from .regime import CHOP, DOWN, UP


@dataclass
class Signals:
    """Per-symbol signal frame with columns:
    entry_long, entry_short, exit_long, exit_short, size_scale, stop_dist."""
    frame: pd.DataFrame
    strategy: str
    max_hold_days: int | None = None  # time stop, enforced by the backtester


def momentum_donchian(bars: pd.DataFrame, regime: pd.Series,
                      entry_n: int = 20, exit_n: int = 10) -> Signals:
    """Breakout trend-following: enter on a 20d channel break in the regime
    direction, exit on a 10d break the other way. No new entries in CHOP."""
    reg = regime.reindex(bars.index).fillna(CHOP)
    hi_entry = bars["high"].rolling(entry_n).max().shift(1)
    lo_entry = bars["low"].rolling(entry_n).min().shift(1)
    hi_exit = bars["high"].rolling(exit_n).max().shift(1)
    lo_exit = bars["low"].rolling(exit_n).min().shift(1)
    close = bars["close"]

    f = pd.DataFrame(index=bars.index)
    f["entry_long"] = (close > hi_entry) & (reg == UP)
    f["entry_short"] = (close < lo_entry) & (reg == DOWN)
    f["exit_long"] = close < lo_exit
    f["exit_short"] = close > hi_exit
    f["size_scale"] = 1.0
    f["stop_dist"] = atr(bars, 14) * 2.0
    return Signals(f, "momentum")


def mean_reversion_rsi2(bars: pd.DataFrame, regime: pd.Series,
                        buy_below: float = 10, sell_above: float = 90) -> Signals:
    """RSI(2) pullback: buy deep dips in uptrends, short spikes in downtrends.
    In CHOP both sides are allowed at half size. Time stop of 7 bars."""
    reg = regime.reindex(bars.index).fillna(CHOP)
    r2 = rsi(bars["close"], 2)
    ma200 = sma(bars["close"], 200)
    close = bars["close"]

    long_ok = (reg == UP) | ((reg == CHOP) & (close > ma200))
    short_ok = (reg == DOWN) | ((reg == CHOP) & (close < ma200))

    f = pd.DataFrame(index=bars.index)
    f["entry_long"] = (r2 < buy_below) & long_ok & ma200.notna()
    f["entry_short"] = (r2 > sell_above) & short_ok & ma200.notna()
    f["exit_long"] = r2 > 60
    f["exit_short"] = r2 < 40
    f["size_scale"] = pd.Series(1.0, index=bars.index).where(reg != CHOP, 0.5)
    f["stop_dist"] = atr(bars, 14) * 2.0
    return Signals(f, "meanrev", max_hold_days=7)
