"""Active strategy lineup — the ONE file the evolution loop may rewrite.

The live bot and the backtester both trade exactly what FACTORIES lists.
The evolution loop (evolve.py) may propose a new version of this file
(optionally alongside a new engine/evolved_*.py module), but the change only
ships if it beats the incumbent in the arena: full 10-year sample AND a
majority of walk-forward folds, with a 14-day adoption cooldown. Risk rules
(engine/risk.py) are enforced by the backtester and live bot regardless of
what strategies emit — the lineup cannot loosen them.
"""
from .strategies import mean_reversion_rsi2, momentum_donchian

FACTORIES = [momentum_donchian, mean_reversion_rsi2]
