"""Kalshi favorite scanner — public data, no account needed.

Finds high-probability contracts (ask >= $0.90 on either side), computes the
post-fee yield if the favorite pays, annualized by time to resolution.

HONESTY NOTE: the yield shown is the market's offered rate, not free money.
At a $0.95 ask the market itself says ~5% chance of losing your $0.95. The
strategy's edge, if any, comes from favorite-longshot bias (favorites tend to
be slightly underpriced). This scanner ranks the offers; it cannot see true
probabilities. Kalshi fee: ~0.07 * P * (1-P) per contract, charged on trade.
"""
import json
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import certifi

import os

# scan the same exchange you trade on: prod for research, demo for the bot
BASE = os.environ.get("KALSHI_MARKETS_BASE",
                      "https://api.elections.kalshi.com/trade-api/v2/markets")
CTX = ssl.create_default_context(cafile=certifi.where())
UA = {"User-Agent": "Mozilla/5.0"}

MIN_PRICE, MAX_PRICE = 0.90, 0.985   # the favorite zone (above .985 fees eat it)
MAX_DAYS = 30                        # capital lockup limit
MIN_ACTIVITY = 100                   # open interest or lifetime volume (contracts)
MIN_ASK_SIZE = 50                    # contracts actually offered at the ask
MAX_SPREAD = 0.03                    # a real two-sided market


def fetch_all(max_pages=200):
    """All open markets closing within MAX_DAYS (server-side window)."""
    now = int(time.time())
    markets, cursor = [], None
    for _ in range(max_pages):
        q = {"status": "open", "limit": "1000",
             "min_close_ts": str(now), "max_close_ts": str(now + MAX_DAYS * 86400)}
        if cursor:
            q["cursor"] = cursor
        url = f"{BASE}?{urllib.parse.urlencode(q)}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                    timeout=30, context=CTX) as r:
            d = json.loads(r.read())
        markets += d.get("markets", [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return markets


def fee(price):
    return round(0.07 * price * (1 - price) + 0.0049, 2)  # per-contract, rounded up-ish


def scan():
    now = datetime.now(timezone.utc)
    rows = []
    all_m = fetch_all()
    for m in all_m:
        if m.get("market_type") != "binary" or "MVE" in (m.get("ticker") or "").upper():
            continue  # skip parlays/multi-leg
        try:
            close = datetime.fromisoformat(m["close_time"].replace("Z", "+00:00"))
        except Exception:
            continue
        days = (close - now).total_seconds() / 86400
        if not (0.25 <= days <= MAX_DAYS):
            continue
        activity = max(float(m.get("open_interest_fp") or 0),
                       float(m.get("volume_fp") or 0))
        if activity < MIN_ACTIVITY:
            continue
        for side in ("yes", "no"):
            ask = m.get(f"{side}_ask_dollars")
            bid = m.get(f"{side}_bid_dollars")
            size = float(m.get(f"{side}_ask_size_fp") or 0)
            if ask is None or bid is None:
                continue
            ask, bid = float(ask), float(bid)
            if size < MIN_ASK_SIZE or bid <= 0 or (ask - bid) > MAX_SPREAD:
                continue
            if not (MIN_PRICE <= ask <= MAX_PRICE):
                continue
            net = (1 - ask) - fee(ask)          # profit per contract if it pays
            if net <= 0:
                continue
            ann = (net / ask) * (365 / max(days, 0.25))
            rows.append({
                "ticker": m["ticker"], "side": side.upper(), "ask": ask,
                "days": round(days, 1), "net_if_win": round(net, 3),
                "ann_yield": ann, "activity": int(activity),
                "title": (m.get("title") or m.get("yes_sub_title") or "")[:64],
            })
    rows.sort(key=lambda r: -r["ann_yield"])
    print(f"scanned {len(all_m)} open markets (<{MAX_DAYS}d) -> {len(rows)} "
          f"favorite-zone offers (${MIN_PRICE:.2f}-{MAX_PRICE:.2f}, "
          f"activity>{MIN_ACTIVITY}, spread<={MAX_SPREAD:.02f})\n")
    print(f"{'side':4s} {'ask':>5s} {'days':>5s} {'net¢':>5s} {'ann%':>7s} {'activ':>7s}  ticker / title")
    for r in rows[:25]:
        print(f"{r['side']:4s} {r['ask']:5.2f} {r['days']:5.1f} {r['net_if_win'] * 100:5.1f} "
              f"{r['ann_yield'] * 100:7.0f} {r['activity']:7d}  {r['ticker'][:28]:28s} {r['title'][:40]}")
    return rows


if __name__ == "__main__":
    scan()
