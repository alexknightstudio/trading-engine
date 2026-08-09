"""Analyzer toolbox: reusable market-structure measures strategies can draw on.

Each analyzer is a candidate input, not a belief — a strategy built on one
must beat the incumbent in experiments.py before it ships. Fibonacci in
particular has weak academic support; it earns its keep in the lab or not at all.
"""
import numpy as np
import pandas as pd

from .indicators import atr, sma


def fib_retracement(bars: pd.DataFrame, lookback: int = 60) -> pd.DataFrame:
    """Swing structure over `lookback` days and how far price has retraced
    the swing-low -> swing-high leg (0 = at the high, 1 = at the low).
    Also returns the classic level prices for stop/target placement."""
    hi = bars["high"].rolling(lookback).max()
    lo = bars["low"].rolling(lookback).min()
    rng = (hi - lo).replace(0, np.nan)
    out = pd.DataFrame(index=bars.index)
    out["swing_high"] = hi
    out["swing_low"] = lo
    out["retraced"] = (hi - bars["close"]) / rng
    for lvl in (0.236, 0.382, 0.5, 0.618, 0.786):
        out[f"fib_{lvl}"] = hi - lvl * rng
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n).std()
    return pd.DataFrame({
        "mid": mid, "upper": mid + k * sd, "lower": mid - k * sd,
        "pct_b": (close - (mid - k * sd)) / (2 * k * sd),
        "bandwidth": 2 * k * sd / mid,
    })


def adx(bars: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Trend-strength: ADX > ~25 means a real trend, < ~20 means chop."""
    up = bars["high"].diff()
    down = -bars["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=bars.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=bars.index)
    tr = atr(bars, n)
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / tr
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame({
        "adx": dx.ewm(alpha=1 / n, adjust=False).mean(),
        "plus_di": plus_di, "minus_di": minus_di,
    })
