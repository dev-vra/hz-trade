"""Motor ao vivo (forward test). O browser logado envia os candles M5 a cada fechamento;
aqui calculamos sinal + vetos + nível e registramos em reports/forward.csv.

Uso: python src/live.py   (porta 8765, substitui o receiver.py)
NÃO executa ordens: só alerta. Entrada é manual.
"""
import csv
import json
import pathlib
import sys
import winsound
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from features import build  # noqa: E402
from strategies import bb25_reversal, bb_reversal, rsi7_reversal  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG = ROOT / "reports" / "forward.csv"
ALLOWED = {"https://app.hezilex.com"}
FIELDS = ["bar_utc", "symbol", "dir", "tier", "strategies", "price", "vetos", "result", "close_after"]

# acerto esperado (teste fora da amostra): A 65.9% · B 58.1% · C 56.5% — breakeven ~54-55%
TIER_INFO = {"A": "vela gigante recente", "B": "H4 a favor", "C": "só vetos"}


def evaluate(symbol: str, df: pd.DataFrame) -> dict | None:
    """Avalia a ÚLTIMA barra fechada."""
    last = df.iloc[[-1]]
    hits = {n: int(f(df).iloc[-1]) for n, f in
            (("S5", rsi7_reversal), ("S2b", bb25_reversal), ("S2", bb_reversal))}
    dirs = {d for d in hits.values() if d}
    if len(dirs) != 1:
        return None
    d = dirs.pop()
    r = last.iloc[0]
    vetos = [name for name, on in (
        ("overlap_lon_ny", r["overlap_lon_ny"]),
        ("noticia_US", r["ny_news_window"] or r["fomc_window"]),
        ("noticia_FF_alta", r["news_high"]),
        ("fim_de_hora", r["end_of_hour"]),
    ) if on]
    tier = "VETO" if vetos else "A" if r["big_candle_recent"] else "B" if r["trend_h4"] == d else "C"
    return {"bar_utc": last.index[0].isoformat(), "symbol": symbol, "dir": "CALL" if d > 0 else "PUT",
            "tier": tier, "strategies": "+".join(k for k, v in hits.items() if v), "price": r["close"],
            "vetos": "|".join(vetos), "result": "", "close_after": ""}


def settle(symbol: str, df: pd.DataFrame) -> None:
    """Marca WIN/LOSS dos sinais pendentes cuja vela seguinte já fechou."""
    if not LOG.exists():
        return
    rows = list(csv.DictReader(LOG.open(encoding="utf-8")))
    closes = df["close"]
    changed = False
    for row in rows:
        if row["symbol"] != symbol or row["result"]:
            continue
        nxt = pd.Timestamp(row["bar_utc"]) + pd.Timedelta("5min")
        if nxt not in closes.index:
            continue
        after, entry = closes[nxt], float(row["price"])
        sign = 1 if row["dir"] == "CALL" else -1
        row["close_after"] = after
        row["result"] = "TIE" if after == entry else "WIN" if (after - entry) * sign > 0 else "LOSS"
        changed = True
    if changed:
        with LOG.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, FIELDS); w.writeheader(); w.writerows(rows)


def record(sig: dict) -> None:
    new = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerow(sig)


def stats() -> str:
    if not LOG.exists():
        return ""
    df = pd.read_csv(LOG)
    df = df[df["result"].isin(["WIN", "LOSS"])]
    if df.empty:
        return ""
    g = df.groupby("tier")["result"].agg(lambda s: f"{(s == 'WIN').mean():.0%} ({len(s)})")
    return " · ".join(f"{k}: {v}" for k, v in g.items())


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = []
        for symbol, raw in payload.items():
            try:
                df = build(symbol, raw, live=True)
                settle(symbol, df)
                sig = evaluate(symbol, df)
            except Exception as e:  # um ativo ruim não derruba o ciclo
                print(f"[erro] {symbol}: {e}", flush=True)
                continue
            if sig:
                record(sig)
                out.append(sig)
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        live = [s for s in out if s["tier"] != "VETO"]
        for s in sorted(live, key=lambda s: s["tier"]):
            print(f"🔔 {now} [{s['tier']}] {s['symbol']:8} {s['dir']:4} @ {s['price']}  ({s['strategies']}; {TIER_INFO[s['tier']]})", flush=True)
            if s["tier"] == "A":
                winsound.Beep(1200, 400)
        for s in out:
            if s["tier"] == "VETO":
                print(f"   {now} vetado {s['symbol']} {s['dir']} ({s['vetos']})", flush=True)
        print(f"   {now} ciclo ok: {len(payload)} ativos · forward {stats() or 'sem resultados ainda'}", flush=True)
        body = json.dumps(live).encode()
        self.send_response(200); self._cors(); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print("live em 127.0.0.1:8765 — aguardando candles do browser", flush=True)
    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
