"""Performance stats and breakdowns for a backtest Result."""
import numpy as np
import pandas as pd

from .backtest import Result

TRADING_DAYS = 252


def summarize(res: Result, label: str = "strategy") -> dict:
    eq = res.equity
    rets = eq.pct_change().dropna()
    years = len(eq) / TRADING_DAYS
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    sharpe = rets.mean() / rets.std() * np.sqrt(TRADING_DAYS) if rets.std() > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()

    wins = [t for t in res.trades if t.pnl > 0]
    losses = [t for t in res.trades if t.pnl <= 0]
    return {
        "label": label,
        "total_return": total,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": dd,
        "trades": len(res.trades),
        "win_rate": len(wins) / len(res.trades) if res.trades else np.nan,
        "avg_win": np.mean([t.pnl for t in wins]) if wins else 0.0,
        "avg_loss": np.mean([t.pnl for t in losses]) if losses else 0.0,
        "halts": res.halts,
        "standdowns": res.standdown_days,
    }


def print_summary(s: dict):
    print(f"\n=== {s['label']} ===")
    print(f"  total return   {s['total_return']*100:8.1f}%")
    print(f"  CAGR           {s['cagr']*100:8.1f}%")
    print(f"  Sharpe         {s['sharpe']:8.2f}")
    print(f"  max drawdown   {s['max_drawdown']*100:8.1f}%")
    print(f"  trades         {s['trades']:8d}   win rate {s['win_rate']*100:.0f}%")
    print(f"  avg win ${s['avg_win']:,.0f}   avg loss ${s['avg_loss']:,.0f}")
    if s["halts"]:
        print(f"  !! DRAWDOWN HALTS: {', '.join(str(h.date()) for h in s['halts'])}")
    if s["standdowns"]:
        print(f"  daily-loss stand-downs: {s['standdowns']}")


def breakdown(res: Result, key) -> pd.DataFrame:
    if not res.trades:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "group": key(t), "pnl": t.pnl, "win": t.pnl > 0,
    } for t in res.trades])
    g = df.groupby("group").agg(trades=("pnl", "size"), total_pnl=("pnl", "sum"),
                                win_rate=("win", "mean"))
    return g.sort_values("total_pnl", ascending=False)


def buy_hold(bars: pd.DataFrame, dates: pd.DatetimeIndex, start_equity: float = 100_000) -> pd.Series:
    close = bars["close"].reindex(dates).ffill()
    return start_equity * close / close.iloc[0]
