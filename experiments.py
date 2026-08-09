"""Variant experiments: quantify rule changes before adopting them.
This is the manual version of what the evolution loop will do nightly.

Current incumbent: momentum + mean-reversion, longs only (shorts lost -$81k
over 10y in the 2026-08-09 experiment), CHOP at half size.
"""
from pathlib import Path

import pandas as pd

from engine import stats
from engine.backtest import run
from engine.data import load_universe
from engine.regime import classify
from engine.strategies import fib_pullback, mean_reversion_rsi2, momentum_donchian

_UNIVERSE_FILE = Path(__file__).parent / "data" / "universe.txt"
UNIVERSE = (_UNIVERSE_FILE.read_text().split() if _UNIVERSE_FILE.exists()
            else ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "TSLA"])
START_EQUITY = 100_000


def build_signals(bars, regime, factories, allow_shorts=False):
    out = {}
    for sym in bars:
        sigs = [make(bars[sym], regime) for make in factories]
        if not allow_shorts:
            for sig in sigs:
                sig.frame["entry_short"] = False
        out[sym] = sigs
    return out


def main():
    bars = load_universe(UNIVERSE)
    regime = classify(bars["SPY"])
    dates = bars["SPY"].index

    mr_quality = lambda b, r: mean_reversion_rsi2(b, r, require_ma200=True)
    variants = {
        "incumbent (momentum + meanrev)": [momentum_donchian, mean_reversion_rsi2],
        "incumbent + fib pullback": [momentum_donchian, mean_reversion_rsi2, fib_pullback],
        "fib pullback only": [fib_pullback],
        "momentum only": [momentum_donchian],
        "meanrev only": [mean_reversion_rsi2],
        "mom + MR(>200sma)": [momentum_donchian, mr_quality],
        "mom + MR(>200sma) + fib": [momentum_donchian, mr_quality, fib_pullback],
    }

    rows = []
    for label, factories in variants.items():
        res = run(bars, build_signals(bars, regime, factories), dates, regime, START_EQUITY)
        s = stats.summarize(res, label)
        rows.append({
            "variant": label,
            "CAGR%": round(s["cagr"] * 100, 1),
            "Sharpe": round(s["sharpe"], 2),
            "maxDD%": round(s["max_drawdown"] * 100, 1),
            "trades": s["trades"],
            "win%": round(s["win_rate"] * 100),
            "halts": len(s["halts"]),
        })
        print(pd.DataFrame(rows).to_string(index=False), "\n")


if __name__ == "__main__":
    main()
