# ENGINE — autonomous trading system (paper)

Regime-aware, multi-strategy, risk-first trading engine. Backtest lab today;
the same modules will drive the Alpaca paper bot next.

## Layout

- `fetch.py` — downloads 10y of daily bars (Yahoo, no API key) into `data/`
- `engine/data.py` — loads bars, split/dividend-adjusted OHLC
- `engine/regime.py` — classifies each day UP / DOWN / CHOP from SPY
- `engine/strategies.py` — momentum (Donchian 20/10) + mean reversion (RSI-2),
  regime-gated
- `engine/risk.py` — the non-negotiable layer: 1% risk per trade, 2×ATR stops,
  max 6 positions, 20% notional cap, -3% daily-loss stand-down, -20% drawdown
  kill switch. Not tunable by the evolution loop.
- `engine/backtest.py` — event-driven daily backtester; signals on close,
  fills next open, gap-aware stops, 5 bps slippage per side
- `engine/stats.py` — CAGR/Sharpe/drawdown, per-strategy/direction/regime/symbol
  breakdowns, buy & hold benchmark
- `run_backtest.py` — full-system backtest (`--shorts` to re-enable shorts)
- `experiments.py` — A/B variant harness (manual evolution loop)

## Usage

```bash
.venv/bin/python fetch.py         # refresh data
.venv/bin/python run_backtest.py  # full 10y backtest
.venv/bin/python experiments.py   # compare rule variants
```

## Findings log

- **2026-08-09** (10y, 2016–2026, 10 liquid symbols): baseline long+short did
  9.7% CAGR / 0.64 Sharpe / -32.5% maxDD with 3 kill-switch halts. Shorts lost
  -$81k over 243 trades (whipsawed by bear-market rallies); longs made +$223k.
  **Adopted: no shorts — DOWN regime means cash.** Result: 13.7% CAGR /
  0.97 Sharpe / -16.7% maxDD / 0 halts vs SPY buy-hold 15.4% / 0.89 / -33.7%.
  Slightly lower CAGR than buy & hold, half the drawdown, better risk-adjusted.
  CHOP-regime entries also net losers (kept at half size; variant C showed
  cutting them entirely costs more than it saves).

## Rules

- Paper trading only until a multi-month track record earns a human decision.
- Strategy/parameter changes must beat the incumbent in `experiments.py`
  before adoption; risk limits in `risk.py` are never loosened by automation.
