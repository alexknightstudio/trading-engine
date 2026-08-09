"""Load daily bar CSVs. All prices are split/dividend-adjusted so signals and
fills are consistent across the whole history (OHLC scaled by adjclose/close)."""
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent.parent / "data"


def load_bars(symbol: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / f"{symbol.lower()}_1d.csv", parse_dates=["date"], index_col="date")
    ratio = df["adjclose"] / df["close"]
    for col in ("open", "high", "low"):
        df[col] = df[col] * ratio
    df["close"] = df["adjclose"]
    return df[["open", "high", "low", "close", "volume"]]


def load_universe(symbols: list[str]) -> dict[str, pd.DataFrame]:
    return {s: load_bars(s) for s in symbols}
