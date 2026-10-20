"""Auditoria: recalcula vela a vela os sinais a partir do cache de candles e compara com o sinais.db.

Uso: python src/audit.py [desde HH:MM ou AAAA-MM-DD HH:MM, hora local; padrão = hoje 00:00] [pasta dados]
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

import engine
from features import build

LOCAL_TZ = datetime.now().astimezone().tzinfo
DEFAULT_DATA = Path(__file__).resolve().parent.parent / "dist" / "HZSinais" / "dados"


def parse_since(arg: str | None) -> pd.Timestamp:
    today = pd.Timestamp.now(tz=LOCAL_TZ).normalize()
    if not arg:
        return today
    if len(arg) <= 5:
        h, m = map(int, arg.split(":"))
        return today + pd.Timedelta(hours=h, minutes=m)
    return pd.Timestamp(arg).tz_localize(LOCAL_TZ)


def result(d: str, price: float, after: float | None) -> str:
    if after is None:
        return "PEND"
    sign = 1 if d == "CALL" else -1
    return "TIE" if after == price else "WIN" if (after - price) * sign > 0 else "LOSS"


def recompute(data: Path, since: pd.Timestamp) -> list[dict]:
    out = []
    for f in sorted((data / "candles").glob("*.json")):
        symbol = f.stem
        df = build(symbol, json.loads(f.read_text()), live=True)
        for i in range(len(df)):
            if df.index[i] < since:
                continue
            sig = engine.evaluate(symbol, df.iloc[: i + 1])
            if not sig:
                continue
            after = float(df["close"].iloc[i + 1]) if i + 1 < len(df) else None
            out.append({"bar_utc": sig.bar_utc, "symbol": symbol, "dir": sig.dir, "tier": sig.tier,
                        "price": sig.price, "vetos": sig.vetos, "calc": result(sig.dir, sig.price, after)})
    return out


def main() -> None:
    since = parse_since(sys.argv[1] if len(sys.argv) > 1 else None)
    data = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DATA
    calc = recompute(data, since)
    db = sqlite3.connect(data / "sinais.db")
    stored = {(b, s): (t, r) for b, s, t, r in db.execute("SELECT bar_utc,symbol,tier,result FROM signals")}

    print(f"Auditoria desde {since:%d/%m %H:%M} — {len(calc)} sinal(is) recalculado(s)\n")
    print(f"{'hora':5}  {'ativo':8} {'dir':4} {'nív':4} {'calc':5} {'no app':12} vetos")
    missing = 0
    for c in sorted(calc, key=lambda c: c["bar_utc"]):
        hit = stored.get((c["bar_utc"], c["symbol"]))
        app = f"{hit[0]} {hit[1] or 'PEND'}" if hit else "AUSENTE"
        missing += hit is None
        local = pd.Timestamp(c["bar_utc"]).tz_convert(LOCAL_TZ)
        print(f"{local:%H:%M}  {c['symbol']:8} {c['dir']:4} {c['tier']:4} {c['calc']:5} {app:12} {c['vetos']}")
    extra = set(stored) - {(c["bar_utc"], c["symbol"]) for c in calc}
    extra = [k for k in extra if pd.Timestamp(k[0]) >= since]
    print(f"\nAusentes no app: {missing} (normal se o app estava fechado na hora) · só no app: {len(extra)}")


if __name__ == "__main__":
    main()
