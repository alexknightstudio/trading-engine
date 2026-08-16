# KalshiKnight — research basis (2026-08-16)

Full sources in the session log; key findings that shaped the bot:

- **Favorite-longshot bias is confirmed on Kalshi** (Bürgi/Deng/Whelan, GWU
  2026-001; 313,972 contract prices 2021–2025): <10c contracts lose >60%;
  >70c contracts earn small but statistically significant positive post-fee
  returns. The edge is real, small, and weakening as the market matures.
- **Maker vs taker is the biggest lever**: average returns −9.6% (makers) vs
  −31.5% (takers); makers ≥50c averaged **+2.6%**. Maker fees are 75% lower.
  → the bot posts GTC post-only orders inside the spread; never lifts the ask.
- **Wipe-ratio cliff above ~97c**: at 98c one loss erases ~49 wins, at 99c
  ~99. → price cap 97c + ≥1.5c post-fee edge floor (also blocks the
  annualized-yield trap that migrates books to short-dated 98c corners).
- **Price-path markets are excluded** (crypto/index levels): favorites there
  can gap through the level on one cascade; the bias thesis is about event
  mispricing, not price paths. (GWU excluded them; practitioner postmortems
  agree.)
- **Adverse selection lives in single-name markets** (Bartlett/O'Hara, SSRN):
  informed flow concentrates in single-entity announcement markets; broad
  markets (econ prints, sports, weather aggregates) are behavioral-flow.
  → the Claude research screen vetoes interpretive-resolution wording and
  single-name announcement markets, and web-searches each candidate for
  breaking news before any bet. FAIL-CLOSED: no research, no bets.
- **Known caveats**: demo fills are optimistic (no real queue position — treat
  demo P/L as an upper bound); the edge shows signs of decay post-2025;
  driver-level correlation (many favorites sharing one macro assumption) is
  the canonical blowup mode — series caps mitigate, driver tagging is a
  future upgrade.
