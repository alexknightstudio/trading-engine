"""Variant experiments: quantify rule changes before adopting them.
This is the manual version of what the evolution loop will do nightly.

Current incumbent: momentum + mean-reversion, longs only, round-robin slots.
Every variant is also scored on 2-year walk-forward folds: a change must win
overall AND in a majority of folds — full-sample wins alone can be one lucky
stretch (anti-overfitting guardrail from the 2026-08-09 research pass).
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
FOLD_YEARS = 2


def build_signals(bars, regime, factories, allow_shorts=False):
    out = {}
    for sym in bars:
        sigs = [make(bars[sym], regime) for make in factories]
        if not allow_shorts:
            for sig in sigs:
                sig.frame["entry_short"] = False
        out[sym] = sigs
    return out


def fold_sharpes(equity: pd.Series) -> list[float]:
    out = []
    for _, chunk in equity.groupby(equity.index.year // FOLD_YEARS):
        r = chunk.pct_change().dropna()
        if len(r) > 60 and r.std() > 0:
            out.append(float(r.mean() / r.std() * 252 ** 0.5))
    return out


def main():
    bars = load_universe(UNIVERSE)
    regime_plain = classify(bars["SPY"])
    regime_hyst = classify(bars["SPY"], band=0.01)
    dates = bars["SPY"].index

    mr_nostop = lambda b, r: mean_reversion_rsi2(b, r, tight_stop=False)
    variants = {
        "incumbent (mom + MR, 2xATR stops)": ([momentum_donchian, mean_reversion_rsi2], regime_plain),
        "MR no tight stop (Alvarez)":        ([momentum_donchian, mr_nostop], regime_plain),
        "regime hysteresis 1% band":         ([momentum_donchian, mean_reversion_rsi2], regime_hyst),
        "MR no stop + hysteresis":           ([momentum_donchian, mr_nostop], regime_hyst),
    }

    rows = []
    folds = {}
    for label, (factories, regime) in variants.items():
        res = run(bars, build_signals(bars, regime, factories), dates, regime, START_EQUITY)
        s = stats.summarize(res, label)
        fs = fold_sharpes(res.equity)
        folds[label] = fs
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

    print("--- walk-forward Sharpe by ~2y fold ---")
    base = folds[next(iter(folds))]
    for label, fs in folds.items():
        wins = sum(a > b for a, b in zip(fs, base))
        marks = " ".join(f"{v:5.2f}" for v in fs)
        print(f"{label:38s} {marks}   folds beating incumbent: {wins}/{len(base)}")


if __name__ == "__main__":
    main()
