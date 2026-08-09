"""Variant experiments: quantify rule changes before adopting them.
This is the manual version of what the evolution loop will do nightly.
"""
import pandas as pd

from engine import stats
from engine.backtest import run
from engine.data import load_universe
from engine.regime import DOWN, classify
from engine.strategies import mean_reversion_rsi2, momentum_donchian

UNIVERSE = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "TSLA"]
START_EQUITY = 100_000


def build_signals(bars, regime, no_shorts=False, no_chop=False, mr_shorts_only=False):
    out = {}
    for sym in UNIVERSE:
        sigs = [momentum_donchian(bars[sym], regime), mean_reversion_rsi2(bars[sym], regime)]
        for sig in sigs:
            f = sig.frame
            if no_shorts:
                f["entry_short"] = False
            elif mr_shorts_only and sig.strategy == "momentum":
                f["entry_short"] = False
            if no_chop:
                reg = regime.reindex(f.index)
                f.loc[reg == "CHOP", ["entry_long", "entry_short"]] = False
        out[sym] = sigs
    return out


def main():
    bars = load_universe(UNIVERSE)
    regime = classify(bars["SPY"])
    dates = bars["SPY"].index

    variants = {
        "A baseline (long+short everywhere)": {},
        "B no shorts (DOWN regime -> cash)": {"no_shorts": True},
        "C no shorts, no CHOP entries": {"no_shorts": True, "no_chop": True},
        "D shorts via mean-reversion only": {"mr_shorts_only": True},
    }

    rows = []
    for label, kw in variants.items():
        res = run(bars, build_signals(bars, regime, **kw), dates, regime, START_EQUITY)
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
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
