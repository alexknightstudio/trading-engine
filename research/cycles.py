"""Century-scale cycle research on index data (data/gspc_long.csv etc.).

Questions this answers, honestly, with ~98 years of S&P 500 daily closes:
  1. Monthly seasonality — does the calendar carry signal, and does it persist?
  2. Halloween effect — Nov-Apr vs May-Oct.
  3. Presidential cycle — four-year pattern.
  4. Bear-market anatomy — frequency, depth, duration, crash speed.
  5. Bubble signatures — how far above its 200d MA an index stretches at
     manic peaks (1929, 2000, 2021...), and what forward returns follow
     given extension. This is the empirical basis for any "AI bubble" logic.
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent.parent / "data"


def load(name):
    df = pd.read_csv(DATA / f"{name}_long.csv", parse_dates=["date"], index_col="date")
    return df["close"]


def monthly_seasonality(close, label, start=None):
    c = close[close.index >= start] if start else close
    r = c.pct_change().dropna()
    g = r.groupby(r.index.month)
    print(f"\n--- monthly seasonality: {label} ({c.index[0].year}-{c.index[-1].year}) ---")
    print("month  avg_daily_bp  ann_return%  win_rate%")
    for m in range(1, 13):
        rm = g.get_group(m)
        print(f"{m:5d}  {rm.mean() * 1e4:11.2f}  {rm.mean() * 252 * 100:10.1f}  "
              f"{(rm > 0).mean() * 100:8.1f}")


def halloween(close, label, start=None):
    c = close[close.index >= start] if start else close
    r = c.pct_change().dropna()
    winter = r[(r.index.month >= 11) | (r.index.month <= 4)]
    summer = r[(r.index.month >= 5) & (r.index.month <= 10)]
    print(f"\n--- Halloween effect: {label} ({c.index[0].year}-{c.index[-1].year}) ---")
    print(f"Nov-Apr: {winter.mean() * 252 * 100:6.1f}%/yr ann.  |  "
          f"May-Oct: {summer.mean() * 252 * 100:6.1f}%/yr ann.")


def presidential(close, label):
    r = close.pct_change().dropna()
    print(f"\n--- presidential cycle: {label} ---")
    for phase, tag in [(1, "post-election"), (2, "midterm"), (3, "pre-election"), (0, "election")]:
        rp = r[r.index.year % 4 == phase]
        print(f"  year {tag:13s}: {rp.mean() * 252 * 100:6.1f}%/yr ann. "
              f"(n={rp.index.year.nunique()} years)")


def bear_markets(close, label):
    peak = close.cummax()
    dd = close / peak - 1
    print(f"\n--- bear markets (>20% off peak): {label} ---")
    in_bear, start, trough, trough_d = False, None, 0.0, None
    peak_date = close.index[0]
    bears = []
    for d in close.index:
        if close[d] >= peak[d]:
            if in_bear:
                bears.append((start, trough_d, d, trough))
                in_bear = False
            peak_date = d
        if dd[d] <= -0.20 and not in_bear:
            in_bear, start, trough, trough_d = True, peak_date, dd[d], d
        if in_bear and dd[d] < trough:
            trough, trough_d = dd[d], d
    for s, t, e, depth in bears:
        to_trough = np.busday_count(s.date(), t.date())
        print(f"  peak {s.date()}  trough {t.date()} ({depth * 100:5.1f}%, {to_trough:4d} bdays)"
              f"  recovered {e.date()}")
    if bears:
        depths = [b[3] for b in bears]
        speeds = [np.busday_count(b[0].date(), b[1].date()) for b in bears]
        print(f"  n={len(bears)}  avg depth {np.mean(depths) * 100:.1f}%  "
              f"avg peak->trough {np.mean(speeds):.0f} bdays  "
              f"one roughly every {(close.index[-1].year - close.index[0].year) / len(bears):.1f} years")


def bubble_extension(close, label):
    """Extension above the 200d MA: what's normal, what's manic, and what
    forward returns follow. The 'AI bubble' detector question."""
    ma = close.rolling(200).mean()
    ext = (close / ma - 1).dropna()
    fwd12 = close.shift(-252) / close - 1
    print(f"\n--- 200d-MA extension: {label} ---")
    print(f"  p50 {ext.median() * 100:5.1f}%  p90 {ext.quantile(0.9) * 100:5.1f}%  "
          f"p99 {ext.quantile(0.99) * 100:5.1f}%  max {ext.max() * 100:5.1f}% "
          f"on {ext.idxmax().date()}")
    print("  forward 12m return by extension bucket:")
    for lo, hi in [(-1, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 10)]:
        mask = (ext >= lo) & (ext < hi)
        f = fwd12.reindex(ext.index)[mask].dropna()
        if len(f):
            print(f"    ext {lo * 100:4.0f}%..{hi * 100:4.0f}%: median fwd12m "
                  f"{f.median() * 100:6.1f}%  p10 {f.quantile(0.1) * 100:6.1f}%  (n={len(f)})")
    print("  peaks of record extension (manic tops):")
    top = ext[ext > ext.quantile(0.995)]
    years = sorted(set(top.index.year))
    print(f"    years with 99.5th-percentile extension: {years}")


if __name__ == "__main__":
    gspc = load("gspc")
    ixic = load("ixic")

    monthly_seasonality(gspc, "S&P 500 full")
    monthly_seasonality(gspc, "S&P 500 recent", start="1996-01-01")
    halloween(gspc, "S&P 500 full")
    halloween(gspc, "S&P 500 recent", start="1996-01-01")
    presidential(gspc, "S&P 500 full")
    bear_markets(gspc, "S&P 500")
    bubble_extension(gspc, "S&P 500")
    bubble_extension(ixic, "Nasdaq (the bubble index)")
