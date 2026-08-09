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


def classify(index_bars: pd.DataFrame, slope_days: int = 10) -> pd.Series:
    close = index_bars["close"]
    ma50 = sma(close, 50)
    ma200 = sma(close, 200)
    slope = ma50 - ma50.shift(slope_days)

    regime = pd.Series(CHOP, index=close.index)
    regime[(close > ma200) & (slope > 0)] = UP
    regime[(close < ma200) & (slope < 0)] = DOWN
    regime[ma200.isna()] = CHOP
    return regime
