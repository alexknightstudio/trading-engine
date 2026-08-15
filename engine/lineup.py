"""Active strategy lineup — the ONE file the evolution loop may rewrite.

The live bot and the backtester both trade exactly what FACTORIES lists.
The evolution loop (evolve.py) may propose a new version of this file
(optionally alongside a new engine/evolved_*.py module), but the change only
ships if it beats the incumbent in the arena: full 10-year sample AND a
majority of walk-forward folds, with a 14-day adoption cooldown. Risk rules
(engine/risk.py) are enforced by the backtester and live bot regardless of
what strategies emit — the lineup cannot loosen them.

Tonight's experiment: keep both incumbent legs exactly as they are and add a
third, independent long-horizon trend leg (55d breakout / 20d exit / 3xATR
stop, quality-gated by the 200d SMA and 12-month return). The round-robin slot
ranker then allocates across three horizons instead of two — fast breakout,
2-day mean reversion, and slow trend — capturing multi-month trends that the
fast leg's 10-day exit necessarily cuts short, without weakening that exit.
"""
from .evolved_slow_trend_leg import slow_breakout
from .strategies import mean_reversion_rsi2, momentum_donchian

FACTORIES = [momentum_donchian, mean_reversion_rsi2, slow_breakout]
