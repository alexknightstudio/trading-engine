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
from engine.focus import load_focus
from engine.regime import classify
from engine.risk import RiskConfig
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

    base = [momentum_donchian, mean_reversion_rsi2]

    # --- cycle-research wrappers (research/cycles.py, 98y of index data) ---
    def seasonal(factory, months, scale):
        """Scale entry size in the given calendar months."""
        def make(b, r):
            sig = factory(b, r)
            mask = sig.frame.index.month.isin(months)
            sig.frame.loc[mask, "size_scale"] = sig.frame.loc[mask, "size_scale"] * scale
            return sig
        return make

    def mania_guard(regime, guard_close, mult):
        """Index stretched > mult x its 200d MA = mania -> treat as CHOP.
        Basis: Nasdaq >30% above 200dma -> median forward 12m return -35.6%."""
        ma = guard_close.rolling(200).mean()
        mania = (guard_close > mult * ma).reindex(regime.index).fillna(False)
        return regime.where(~mania, "CHOP")

    qqq = bars["QQQ"]["close"]
    sep_half = [seasonal(f, {9}, 0.5) for f in base]
    summer = [seasonal(f, {5, 6, 7, 8, 9, 10}, 0.75) for f in base]
    variants = {
        "incumbent":                    (base, regime_plain),
        "September half-size":          (sep_half, regime_plain),
        "May-Oct 0.75x size":           (summer, regime_plain),
        "mania guard QQQ>1.30x ma200":  (base, mania_guard(regime_plain, qqq, 1.30)),
        "mania guard QQQ>1.25x ma200":  (base, mania_guard(regime_plain, qqq, 1.25)),
        "Sep half + mania 1.30":        (sep_half, mania_guard(regime_plain, qqq, 1.30)),
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
