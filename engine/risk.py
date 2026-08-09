"""Risk engine. These rules are the non-negotiable layer: strategies propose,
the risk engine disposes. Nothing here is tuned by the evolution loop.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.01      # fraction of equity risked between entry and stop
    max_positions: int = 6
    max_position_notional: float = 0.20  # fraction of equity per position
    stop_atr_mult: float = 2.0
    daily_loss_limit: float = 0.03    # flatten + stand down after a -3% day
    max_drawdown_halt: float = 0.20   # stop the whole system at -20% from peak
    halt_cooldown_days: int = 21      # live: human review; backtest: resume after
                                      # this many flat days with the peak reset
    slippage_bps: float = 5.0         # per side


def position_size(equity: float, entry: float, stop: float, cfg: RiskConfig,
                  size_scale: float = 1.0) -> int:
    """Shares such that (entry - stop) * shares ~= risk_per_trade * equity,
    capped by max notional. Returns 0 if the stop is degenerate."""
    stop_dist = abs(entry - stop)
    if stop_dist <= 0 or entry <= 0:
        return 0
    qty_risk = (cfg.risk_per_trade * size_scale * equity) / stop_dist
    qty_notional = (cfg.max_position_notional * equity) / entry
    return max(int(min(qty_risk, qty_notional)), 0)
