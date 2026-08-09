"""Monte Carlo drawdown analysis: block-bootstrap the system's daily returns
to see the distribution of max drawdowns a healthy run can produce, then check
whether the kill-switch level sits outside normal variance (a halt below the
p95 drawdown will fire on an ordinary bad streak, not just a broken system).

Usage: .venv/bin/python monte_carlo.py [n_paths]
Seeded RNG so results are reproducible run to run.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from engine.backtest import run
from engine.data import load_universe
from engine.regime import classify
from engine.strategies import mean_reversion_rsi2, momentum_donchian

_UNIVERSE_FILE = Path(__file__).parent / "data" / "universe.txt"
UNIVERSE = _UNIVERSE_FILE.read_text().split()
BLOCK = 20  # trading days per bootstrap block, preserves short-range clustering


def max_drawdown(equity: np.ndarray) -> float:
    peaks = np.maximum.accumulate(equity)
    return float(((equity - peaks) / peaks).min())


def main():
    n_paths = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
    bars = load_universe(UNIVERSE)
    regime = classify(bars["SPY"])
    dates = bars["SPY"].index
    signals = {s: [momentum_donchian(bars[s], regime),
                   mean_reversion_rsi2(bars[s], regime)] for s in UNIVERSE}
    for sigs in signals.values():
        for sig in sigs:
            sig.frame["entry_short"] = False

    res = run(bars, signals, dates, regime)
    rets = res.equity.pct_change().dropna().to_numpy()
    print(f"historical: maxDD={max_drawdown(res.equity.to_numpy())*100:.1f}%  "
          f"days={len(rets)}")

    rng = np.random.default_rng(42)
    n = len(rets)
    dds = np.empty(n_paths)
    for p in range(n_paths):
        idx = rng.integers(0, n - BLOCK, size=n // BLOCK + 1)
        path = np.concatenate([rets[i:i + BLOCK] for i in idx])[:n]
        dds[p] = max_drawdown(np.cumprod(1 + path))

    print(f"\nbootstrap maxDD over {n_paths:,} paths (block={BLOCK}d):")
    for q in (50, 75, 90, 95, 99):
        print(f"  p{q}: {np.percentile(dds, 100 - q)*100:6.1f}%")
    p95 = np.percentile(dds, 5)
    print(f"\nA healthy run of this exact system produces a drawdown worse than "
          f"{p95*100:.0f}% in 5% of alternate histories.")
    print(f"Kill switch should sit OUTSIDE that: recommended halt "
          f"~{(p95 - 0.05)*100:.0f}% (p95 + 5pt buffer).")


if __name__ == "__main__":
    main()
