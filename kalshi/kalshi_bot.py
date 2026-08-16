"""KalshiKnight — favorites harvester on the Kalshi DEMO exchange.

Strategy: buy high-probability contracts (ask $0.90-$0.985) resolving within
30 days, ranked by post-fee annualized yield, harvesting favorite-longshot
bias. Every position is negative-skew (small win likely, big loss possible),
so the risk engine is the strategy:

  HARD RULES (not tunable):
    - max 2% of bankroll per market
    - max 1 position per event, 2 per series, 6% of bankroll per series
      (correlation defense: 20 bets on one tournament are not 20 bets)
    - max 80% of bankroll deployed; max 10 new positions per run
    - DEMO exchange only — this file pins demo-api.kalshi.co

Usage: kalshi_bot.py scan | trade | status
"""
import base64
import csv
import json
import ssl
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import os

import certifi
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

os.environ["KALSHI_MARKETS_BASE"] = "https://demo-api.kalshi.co/trade-api/v2/markets"
import scan as scanner

HERE = Path(__file__).parent
API = "https://demo-api.kalshi.co/trade-api/v2"   # DEMO — never the live host
KEY_ID = "6cc8d8f3-741b-4e41-925a-1c8dc7abb741"
TRADES = HERE / "trades.csv"


def _load_private_key_pem() -> bytes:
    """Cloud: PEM content in KALSHI_PRIVATE_KEY secret. Local: file path in
    KALSHI_PRIVATE_KEY_PATH, or the original key location outside the repo.
    The key is never stored inside the git tree."""
    if os.getenv("KALSHI_PRIVATE_KEY"):
        return os.environ["KALSHI_PRIVATE_KEY"].encode()
    path = os.getenv("KALSHI_PRIVATE_KEY_PATH",
                     str(HERE.parent.parent / "KALSHI" / "kalshi_demo_private.pem"))
    return Path(path).read_bytes()
CTX = ssl.create_default_context(cafile=certifi.where())

MAX_PER_MARKET = 0.02
MAX_PER_SERIES = 0.06
MAX_MARKETS_PER_SERIES = 2
MAX_DEPLOYED = 0.80
MAX_NEW_PER_RUN = 10


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


class Kalshi:
    def __init__(self):
        self.pk = serialization.load_pem_private_key(_load_private_key_pem(), password=None)

    def _req(self, method, path, body=None):
        ts = str(int(time.time() * 1000))
        msg = (ts + method + "/trade-api/v2" + path.split("?")[0]).encode()
        sig = self.pk.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                           salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        headers = {
            "KALSHI-ACCESS-KEY": KEY_ID,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "User-Agent": "knighttrader",
        }
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            return json.loads(r.read())

    def balance(self):
        return float(self._req("GET", "/portfolio/balance")["balance_dollars"])

    def positions(self):
        out, cursor = [], None
        while True:
            path = "/portfolio/positions?limit=200" + (f"&cursor={cursor}" if cursor else "")
            d = self._req("GET", path)
            out += [p for p in d.get("market_positions", []) if float(p.get("position_fp") or 0)]
            cursor = d.get("cursor")
            if not cursor:
                break
        return out

    def buy(self, ticker, side, count, price_dollars):
        """V2 orders: side is expressed on the YES leg — buying YES is a
        'bid'; buying NO is an 'ask' (selling YES) at 1 - no_price."""
        if side == "yes":
            v2_side, px = "bid", price_dollars
        else:
            v2_side, px = "ask", round(1 - price_dollars, 4)
        body = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "side": v2_side,
            "count": f"{int(count)}.00",
            "price": f"{px:.4f}",
            "time_in_force": "immediate_or_cancel",  # take the offer or skip
            "self_trade_prevention_type": "taker_at_cross",
        }
        return self._req("POST", "/portfolio/events/orders", body)


def series_of(ticker):
    return ticker.split("-")[0]


def event_of(ticker):
    return "-".join(ticker.split("-")[:-1]) or ticker


def log_trade(row):
    new = not TRADES.exists()
    with open(TRADES, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "ticker", "side", "count", "price", "cost", "net_if_win", "ann_yield", "title"])
        w.writerow(row)


def cmd_status(k):
    bal = k.balance()
    pos = k.positions()
    exposure = sum(abs(float(p.get("market_exposure_dollars") or 0)) for p in pos)
    print(f"balance ${bal:,.2f}  |  {len(pos)} positions, ~${exposure:,.2f} at risk")
    for p in pos:
        print(f"  {p['ticker']:34s} pos={p['position_fp']:>8} exposure=${p.get('market_exposure_dollars')}")


def cmd_trade(k):
    bal = k.balance()
    pos = k.positions()
    deployed = sum(abs(float(p.get("market_exposure_dollars") or 0)) for p in pos)
    bankroll = bal + deployed
    held_events = {event_of(p["ticker"]) for p in pos}
    series_count, series_cost = {}, {}
    for p in pos:
        s = series_of(p["ticker"])
        series_count[s] = series_count.get(s, 0) + 1
        series_cost[s] = series_cost.get(s, 0) + abs(float(p.get("market_exposure_dollars") or 0))

    log(f"bankroll ${bankroll:,.2f} (cash ${bal:,.2f}, deployed ${deployed:,.2f}, "
        f"{len(pos)} positions)")
    if deployed >= MAX_DEPLOYED * bankroll:
        log("max deployment reached — no new positions")
        return

    # ---- select candidates under the hard caps ----
    offers = scanner.scan()
    picks = []
    sim_deployed, sim_count, sim_cost, sim_events = deployed, dict(series_count), dict(series_cost), set(held_events)
    for o in offers:
        if len(picks) >= MAX_NEW_PER_RUN:
            break
        t, s = o["ticker"], series_of(o["ticker"])
        if event_of(t) in sim_events or sim_count.get(s, 0) >= MAX_MARKETS_PER_SERIES:
            continue
        if sim_cost.get(s, 0) >= MAX_PER_SERIES * bankroll:
            continue
        budget = min(MAX_PER_MARKET * bankroll,
                     MAX_DEPLOYED * bankroll - sim_deployed,
                     MAX_PER_SERIES * bankroll - sim_cost.get(s, 0))
        count = int(budget / o["ask"])
        if count < 1:
            continue
        picks.append({**o, "count": count})
        sim_deployed += count * o["ask"]
        sim_events.add(event_of(t))
        sim_count[s] = sim_count.get(s, 0) + 1
        sim_cost[s] = sim_cost.get(s, 0) + count * o["ask"]

    # ---- research screen: Claude + live web search, veto-only, FAIL-CLOSED ----
    if picks:
        if os.getenv("ANTHROPIC_API_KEY"):
            try:
                import research
                verdicts = research.screen(picks)
            except Exception as e:
                log(f"research screen failed ({e}) — FAIL-CLOSED, no bets this run")
                return
            vetoed = {v["ticker"] for v in verdicts if v["action"] == "VETO"}
            for v in verdicts:
                log(f"  research [{v['action']}] {v['ticker']}: {v['reason'][:120]}")
            picks = [p for p in picks if p["ticker"] not in vetoed]
        else:
            log("no ANTHROPIC_API_KEY — trading unscreened (local mode)")

    # ---- place surviving orders ----
    placed = 0
    for o in picks:
        t = o["ticker"]
        try:
            r = k.buy(t, o["side"].lower(), o["count"], o["ask"])
            status = r.get("order", {}).get("status", "?")
        except Exception as e:
            log(f"  order failed {t}: {e}")
            continue
        cost = o["count"] * o["ask"]
        placed += 1
        log(f"  BUY {o['count']} x {t} {o['side']} @ ${o['ask']:.2f} "
            f"(${cost:,.2f}, ann {o['ann_yield'] * 100:.0f}%) [{status}]")
        log_trade([datetime.now(timezone.utc).isoformat(), t, o["side"], o["count"],
                   o["ask"], round(cost, 2), o["net_if_win"], round(o["ann_yield"], 3),
                   o["title"]])
    log(f"placed {placed} new positions")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    k = Kalshi()
    if cmd == "scan":
        scanner.scan()
    elif cmd == "trade":
        cmd_trade(k)
    elif cmd == "status":
        cmd_status(k)
    else:
        sys.exit(__doc__)
