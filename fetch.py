"""Download ~10 years of daily bars from Yahoo Finance (no API key) into data/*.csv."""
import json
import ssl
import time
import urllib.request
from pathlib import Path

import certifi

SSL_CTX = ssl.create_default_context(cafile=certifi.where())

HERE = Path(__file__).parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

ETFS = ["SPY", "QQQ", "IWM", "DIA"]
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def sp500_symbols() -> list[str]:
    """Current S&P 500 constituents from Wikipedia. NOTE: using today's list
    over historical backtests introduces survivorship bias (documented in README)."""
    import re
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
        html = r.read().decode("utf-8", errors="replace")
    # NYSE rows link to nyse.com/quote/XNYS:MMM, Nasdaq rows to
    # nasdaq.com/market-activity/stocks/aapl
    syms = re.findall(r'quote/[A-Z]+:([A-Z.]{1,6})"', html)
    syms += [s.upper() for s in re.findall(r'nasdaq\.com/market-activity/stocks/([a-zA-Z.]{1,6})', html)]
    seen, out = set(), []
    for s in syms:
        s = s.replace(".", "-")  # BRK.B -> BRK-B (Yahoo convention)
        if s not in seen:
            seen.add(s)
            out.append(s)
    if len(out) < 400:
        raise RuntimeError(f"only parsed {len(out)} S&P symbols — page layout changed?")
    return out


def fetch(symbol: str) -> int:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        "?range=10y&interval=1d&events=div%2Csplit"
    )
    req = urllib.request.Request(url, headers=UA)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
                payload = json.loads(r.read())
            break
        except Exception as e:
            if attempt == 3:
                raise
            print(f"  retry {attempt + 1}: {e}")
            time.sleep(2 * (attempt + 1))

    result = payload["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    adj = result["indicators"]["adjclose"][0]["adjclose"]

    out = DATA / f"{symbol.lower()}_1d.csv"
    n = 0
    with open(out, "w") as f:
        f.write("date,open,high,low,close,adjclose,volume\n")
        for i, t in enumerate(ts):
            o, h, l, c, a, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], adj[i], q["volume"][i]
            if None in (o, h, l, c, a):
                continue
            day = time.strftime("%Y-%m-%d", time.gmtime(t))
            f.write(f"{day},{o},{h},{l},{c},{a},{v or 0}\n")
            n += 1
    return n


if __name__ == "__main__":
    focus_file = DATA / "focus.txt"
    extras = focus_file.read_text().split() if focus_file.exists() else []
    universe = list(dict.fromkeys(ETFS + sp500_symbols() + extras))
    print(f"universe: {len(universe)} symbols")
    ok, failed = [], []
    for i, sym in enumerate(universe):
        try:
            n = fetch(sym)
            ok.append(sym)
            if i % 25 == 0:
                print(f"  {i}/{len(universe)} {sym}: {n} bars")
        except Exception as e:
            failed.append(sym)
            print(f"  SKIP {sym}: {e}")
        time.sleep(0.3)
    (DATA / "universe.txt").write_text("\n".join(ok) + "\n")
    print(f"done. fetched {len(ok)}, failed {len(failed)}: {failed}")
