"""Baixa 1 ano de velas M5 da Binance (API pÃºblica) no mesmo formato de data/*.json (t,o,h,l,c,v)."""
import json
import os
import sys
import time
import urllib.request

from features import DATA

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LTCUSDT", "BNBUSDT", "DOGEUSDT"]
URL = "https://api.binance.com/api/v3/klines?symbol={}&interval=5m&startTime={}&limit=1000"
STEP_MS = 1000 * 5 * 60 * 1000


def download(sym: str, days: int = int(os.environ.get("DAYS", 365))) -> None:
    start = int(time.time() * 1000) - days * 86_400_000
    out = {k: [] for k in "tohlcv"}
    while start < time.time() * 1000:
        rows = json.load(urllib.request.urlopen(URL.format(sym, start), timeout=30))
        if not rows:
            break
        for r in rows:
            for k, v in zip("tohlcv", (r[0] // 1000, *map(float, r[1:6]))):
                out[k].append(v)
        start = rows[-1][0] + 300_000
    (DATA / os.environ.get("OUT", "binance")).mkdir(exist_ok=True)
    (DATA / os.environ.get("OUT", "binance") / f"{sym}.json").write_text(json.dumps(out))
    print(sym, len(out["t"]), flush=True)


if __name__ == "__main__":
    for s in sys.argv[1:] or SYMBOLS:
        download(s)

