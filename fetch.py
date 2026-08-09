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

UNIVERSE = ["SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "TSLA"]
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


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
    for sym in UNIVERSE:
        n = fetch(sym)
        print(f"{sym}: {n} bars")
        time.sleep(0.5)
    print("done.")
