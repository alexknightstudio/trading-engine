"""Run the full system backtest over the downloaded history.

Usage: .venv/bin/python run_backtest.py [start_date] [end_date] [--shorts]

Shorts are OFF by default: 10y experiments (experiments.py) showed short
entries lose money net (-$81k over 243 trades) while cash-in-downtrend wins
on CAGR, Sharpe, and drawdown. Re-test with --shorts before re-enabling.
"""
import sys

import numpy as np
import pandas as pd

from engine import stats
from engine.backtest import run
from engine.data import load_universe
from engine.regime import classify
from engine.strategies import mean_reversion_rsi2, momentum_donchian

UNIVERSE = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "TSLA"]
INDEX = "SPY"
START_EQUITY = 100_000


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    allow_shorts = "--shorts" in sys.argv
    start = args[0] if len(args) > 0 else None
    end = args[1] if len(args) > 1 else None

    bars = load_universe(UNIVERSE)
    regime = classify(bars[INDEX])
    dates = bars[INDEX].index
    if start:
        dates = dates[dates >= start]
    if end:
        dates = dates[dates <= end]

    signals = {}
    for sym in UNIVERSE:
        sigs = [
            momentum_donchian(bars[sym], regime),
            mean_reversion_rsi2(bars[sym], regime),
        ]
        if not allow_shorts:
            for sig in sigs:
                sig.frame["entry_short"] = False
        signals[sym] = sigs

    res = run(bars, signals, dates, regime, START_EQUITY)
    s = stats.summarize(res, f"SYSTEM  {dates[0].date()} -> {dates[-1].date()}")
    stats.print_summary(s)

    bh = stats.buy_hold(bars[INDEX], dates, START_EQUITY)
    bh_res_like = type(res)(equity=bh, trades=[], halts=[], standdown_days=0)
    stats.print_summary(stats.summarize(bh_res_like, "SPY buy & hold (benchmark)"))

    print("\n--- by strategy ---")
    print(stats.breakdown(res, lambda t: t.strategy).to_string())
    print("\n--- by direction ---")
    print(stats.breakdown(res, lambda t: t.direction).to_string())
    print("\n--- by regime at entry ---")
    print(stats.breakdown(res, lambda t: t.entry_regime).to_string())
    print("\n--- by symbol ---")
    print(stats.breakdown(res, lambda t: t.symbol).to_string())

    print("\n--- regime days ---")
    print(regime.reindex(dates).value_counts().to_string())

    # yearly returns
    yr = res.equity.resample("YE").last().pct_change().dropna()
    first_year = res.equity.resample("YE").last().iloc[0] / START_EQUITY - 1
    print("\n--- yearly returns (system) ---")
    print(f"  {res.equity.index[0].year}: {first_year*100:6.1f}%  (partial)")
    for d, r in yr.items():
        print(f"  {d.year}: {r*100:6.1f}%")


if __name__ == "__main__":
    main()
