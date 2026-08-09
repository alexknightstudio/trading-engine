"""Market regime engine. Classifies each day from the index (SPY) as:

  UP    - price above 200d SMA and 50d SMA rising  -> long setups only
  DOWN  - price below 200d SMA and 50d SMA falling -> short setups only
  CHOP  - anything else                            -> half size, mean-reversion only

The regime is computed on data available at that day's close, so a strategy
acting on day T's regime trades at T+1's open with no lookahead.
"""
import pandas as pd

from .indicators import sma

UP, DOWN, CHOP = "UP", "DOWN", "CHOP"


def classify(index_bars: pd.DataFrame, slope_days: int = 10,
             band: float = 0.0) -> pd.Series:
    """band > 0 adds hysteresis: once in a regime, stay there until price
    leaves a +/-band zone around the 200d SMA, so the filter doesn't whipsaw
    when the index hovers at the line (2011 / 2015-16 style chop)."""
    close = index_bars["close"]
    ma50 = sma(close, 50)
    ma200 = sma(close, 200)
    slope = ma50 - ma50.shift(slope_days)

    if band <= 0:
        regime = pd.Series(CHOP, index=close.index)
        regime[(close > ma200) & (slope > 0)] = UP
        regime[(close < ma200) & (slope < 0)] = DOWN
        regime[ma200.isna()] = CHOP
        return regime

    up_raw = (close > ma200 * (1 + band)) & (slope > 0)
    down_raw = (close < ma200 * (1 - band)) & (slope < 0)
    in_band = (close >= ma200 * (1 - band)) & (close <= ma200 * (1 + band))
    valid = ma200.notna()

    out, cur = [], CHOP
    for u, d, ib, ok in zip(up_raw.to_numpy(), down_raw.to_numpy(),
                            in_band.to_numpy(), valid.to_numpy()):
        if not ok:
            cur = CHOP
        elif u:
            cur = UP
        elif d:
            cur = DOWN
        elif not ib:
            cur = CHOP
        # inside the band: keep the previous regime (hysteresis)
        out.append(cur)
    return pd.Series(out, index=close.index)
