"""Long-horizon trend leg (Turtle System-2 style), added *beside* the incumbent
fast breakout rather than modifying it.

Motivation: the incumbent momentum leg (20d entry / 10d exit / 2xATR stop) is a
fast, defensive breakout system. It carries nearly all of the portfolio's PnL,
and five separate attempts to widen its exits, filter its entries or re-rank its
candidates all reduced returns. The structural gap it leaves is duration: a 10d
channel exit mechanically ejects positions from multi-month trends. Instead of
loosening the fast leg (which removes protection from every trade), this module
adds an independent slow leg that only ever takes high-quality long-horizon
breakouts and is built to hold them:

  * entry: close above the 55-day high, UP regime, price above its own 200d SMA
    and positive trailing 12-month return (trend-quality gate appropriate for a
    system that intends to hold for months)
  * exit: close below the 20-day low (no time stop)
  * stop: 3xATR - wider so ordinary trend noise does not eject the position.
    Because risk sizing is inverse to stop distance, the wider stop buys holding
    power at a *smaller* position size, not more risk.
  * ranking: 252-day return (cross-sectional momentum, the best-documented
    anomaly available to us) so the strongest long-horizon trend wins the slot.

Because it is registered as its own strategy name, the round-robin slot ranker
gives it a separate queue: capital is now allocated across three independent
edges (fast breakout, 2-day mean reversion, slow trend) instead of two.
"""
import pandas as pd

from .indicators import atr, sma
from .regime import CHOP, UP
from .strategies import Signals


def slow_breakout(bars: pd.DataFrame, regime: pd.Series,
                  entry_n: int = 55, exit_n: int = 20,
                  stop_atr: float = 3.0) -> Signals:
    reg = regime.reindex(bars.index).fillna(CHOP)
    close = bars["close"]

    hi_entry = bars["high"].rolling(entry_n).max().shift(1)
    lo_exit = bars["low"].rolling(exit_n).min().shift(1)
    ma200 = sma(close, 200)
    ret252 = close.pct_change(252)

    quality = (close > ma200) & (ret252 > 0)

    f = pd.DataFrame(index=bars.index)
    f["entry_long"] = (
        (close > hi_entry) & (reg == UP) & quality
    ).fillna(False)
    f["entry_short"] = pd.Series(False, index=bars.index)
    f["exit_long"] = (close < lo_exit).fillna(False)
    f["exit_short"] = pd.Series(False, index=bars.index)
    f["size_scale"] = 1.0
    f["stop_dist"] = atr(bars, 14) * stop_atr
    f["score_long"] = ret252
    f["score_short"] = -ret252
    # no max_hold_days: the point of this leg is to hold trends until the
    # 20-day channel or the 3xATR stop says otherwise.
    return Signals(f, "slowtrend")
