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

- **2026-08-09d** (owner-directed AI/EM tilt experiments):
  - Universe expanded to 528 symbols (S&P 500 + ETFs + AI/semis/data-center
    ADRs + emerging-market ETFs/ADRs, curated in `data/focus.txt`). Kept —
    more opportunity, names earn slots on signal strength.
  - **Focus-priority slot tilt — REJECTED.** Forcing focus names to the front
    of the slot queue halved CAGR (17.4% → 8.5%): weak signals on focus names
    (BABA, INTC, EM ETFs) crowd out strong signals elsewhere, while NVDA-type
    winners get bought either way when their signals are strong. Raising risk
    on top (1.5%/2%) only deepened drawdowns (-41%/-52%).
  - **Focus-ONLY universe: 13.4%/0.76 at 1% risk; 20.9%/0.87/-42% at 1.5%.**
    Not adopted (owner chose the incumbent after seeing the numbers).
  - **"Tune until the backtest shows 30%" — REFUSED on principle.** No honest
    configuration of this system reaches 30% CAGR; a backtest optimized to
    hit a target number is curve-fitting and predicts nothing.
  - Current official (528 symbols, incumbent config): **17.4% CAGR / 0.97
    Sharpe / -30.6% maxDD**, worst year 2022 (-16.4%) vs SPY 15.4% / 0.89 /
    -33.7% / -18.2%.

- **2026-08-09c** (research-driven experiments; sources in the repo history):
  - **MR without tight stops (Alvarez) — REJECTED.** The best-evidenced forum
    advice ("stops hurt mean reversion") failed our lab: 16.8% CAGR / 0.95
    Sharpe vs incumbent 19.7% / 1.08, and only 2/6 walk-forward folds better.
    Advice doesn't transfer automatically; the tight 2xATR stop stays.
  - **Regime hysteresis (1% band) — NOT ADOPTED (neutral).** 19.5% / 1.07 /
    -28.9% DD; wins 4/6 folds and softens drawdown but loses the full sample
    marginally. Documented, benched, worth retesting later.
  - **Kill switch recalibrated -20% → -35%.** Monte Carlo block-bootstrap
    (10k paths) showed healthy runs of this system hit -25% median / -35% p90
    drawdowns, so -20% fired on ordinary variance (3 halts in backtest). The
    -3% daily stand-down remains the fast brake.
  - **Walk-forward folds added to experiments.py** — changes must now win the
    full sample AND a majority of ~2y folds before adoption (anti-overfit
    guardrail; a full-sample win alone can be one lucky stretch).

- **2026-08-09b** (universe expanded to S&P 500 + 4 ETFs, 506 symbols):
  - Slot ranking added: when entries outnumber free slots, each strategy's
    best-scored candidate wins in round-robin. This exposed and fixed a flaw —
    raw scores aren't comparable across strategies, and MR had been silently
    hogging all slots. Fair allocation took the system from 11.6% to 19.7% CAGR.
  - **Adopted default: momentum + mean-reversion, longs only, round-robin
    slots** — 19.7% CAGR / 1.08 Sharpe / -30.3% maxDD vs SPY 15.4% / 0.89 /
    -33.7%. Yearly: worst 2022 at -13% (SPY -18%); three -20% halts triggered
    and recovered.
  - Momentum carries nearly all PnL (+$491k / 493 trades at 41% win rate);
    MR is ~breakeven standalone but the combo beats momentum-only
    (11.2%/0.68) — MR occupies slots that would otherwise take weaker
    momentum entries.
  - Fibonacci pullback (analyzers.py) tested: solo 4.5%/0.46; added to
    incumbent it cuts maxDD to -24.9% but drops Sharpe to 0.93 — **not
    adopted**, stays in the toolbox. MR >200sma quality filter also failed
    (17.1%/0.95).
  - CAVEAT: universe is *today's* S&P 500 → survivorship bias inflates
    results (delisted losers absent). Treat absolute numbers as optimistic;
    relative variant comparisons remain valid.

- **2026-08-09** (10y, 2016–2026, 10 liquid symbols): baseline long+short did
  9.7% CAGR / 0.64 Sharpe / -32.5% maxDD with 3 kill-switch halts. Shorts lost
  -$81k over 243 trades (whipsawed by bear-market rallies); longs made +$223k.
  **Adopted: no shorts — DOWN regime means cash.** Result: 13.7% CAGR /
  0.97 Sharpe / -16.7% maxDD / 0 halts vs SPY buy-hold 15.4% / 0.89 / -33.7%.
  Slightly lower CAGR than buy & hold, half the drawdown, better risk-adjusted.
  CHOP-regime entries also net losers (kept at half size; variant C showed
  cutting them entirely costs more than it saves).

## Autonomy

The bot runs itself via GitHub Actions (`.github/workflows/trading-bot.yml`) —
no local machine needs to be on:

- **plan** (Mon-Fri evenings ET): refresh data, compute signals, submit
  market-on-open orders for the next session
- **evolve** (Mon-Fri nights ET): the self-modification loop — Claude proposes
  ONE strategy-code change (`evolve.py`); a deterministic arena adopts it only
  if it beats the incumbent on the full 10y sample AND a majority of
  walk-forward folds, with ≥200 trades and a 14-day adoption cooldown. Only
  `engine/lineup.py` + `engine/evolved_*.py` are writable; `risk.py` never.
  Every trial logged to `state/evolution_log.jsonl`.
- **news** (Mon-Fri ~9am ET): Claude's veto-only pre-open news screen
- **arm** (Mon-Fri ~10am ET): place GTC protective stops on filled entries
- **brief** (Fri post-close): weekly performance email incl. evolution activity
- every run commits `state/` back to the repo — the trade log is public and
  auditable by design

Alpaca paper keys live in the repo's Actions secrets (`ALPACA_API_KEY_ID`,
`ALPACA_API_SECRET_KEY`). Local runs still work; `git pull` first so local
state matches the last cloud run, and remember `plan` is once-per-day
idempotent (`--force` to override).

## Rules

- Paper trading only until a multi-month track record earns a human decision.
- Strategy/parameter changes must beat the incumbent in `experiments.py`
  before adoption; risk limits in `risk.py` are never loosened by automation.
