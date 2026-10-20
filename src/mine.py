"""Mineração de padrões em cripto (Binance, 1 ano, M5 e M15).
Treino = primeiros 8 meses, teste = últimos 4. Só reporta regras que passam nos dois.
Alvo: cor da PRÓXIMA vela do timeframe (entrada na abertura, expira no fechamento)."""
import json
import os
import sys
from itertools import combinations

import numpy as np
import pandas as pd

from engine import REVERSAL_RULES
from features import DATA, build

SRC = DATA / os.environ.get("SRC", "binance")
SYMBOLS = sorted(p.stem for p in SRC.glob("*.json"))
PAYOUT = 0.82
BE = 1 / (1 + PAYOUT)
TZ = "America/Sao_Paulo"


def raw_tf(sym: str, rule: str) -> dict:
    raw = json.loads((SRC / f"{sym}.json").read_text())
    if rule == "5min":
        return raw
    df = pd.DataFrame(raw)
    df["t"] = pd.to_datetime(df["t"], unit="s", utc=True)
    g = df.set_index("t").resample(rule).agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna()
    return {"t": [int(x.timestamp()) for x in g.index], **{k: g[k].tolist() for k in "ohlcv"}}


def frame(sym: str, rule: str, btc: pd.DataFrame | None, raw: dict | None = None, live: bool = False) -> pd.DataFrame:
    raw = raw or raw_tf(sym, rule)
    df = build(sym, raw, live=live)
    vol = pd.Series(raw["v"], index=pd.to_datetime(raw["t"], unit="s", utc=True))
    df["vol"] = vol[~vol.index.duplicated()].reindex(df.index)
    c, o = df["close"], df["open"]
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    # alvo: próxima vela (entra no fechamento de i = abertura de i+1)
    df["y"] = np.sign(c.shift(-1) - c)
    loc = df.index.tz_convert(TZ)
    f = pd.DataFrame(index=df.index)
    f["hora"] = loc.hour
    f["min"] = loc.minute
    f["dow"] = loc.weekday
    col = np.sign(c - o).astype(int)
    f["cores3"] = (col.shift(2).fillna(0).astype(int).astype(str) + col.shift(1).fillna(0).astype(int).astype(str)
                   + col.astype(str))
    streak = col.groupby((col != col.shift()).cumsum()).cumcount() + 1
    f["seq"] = (streak.clip(upper=6) * col).astype(int)
    f["corpo"] = pd.cut((c - o).abs() / df["atr"], [0, .3, .7, 1.2, 2, 99], labels=False)
    f["pavio_sup"] = pd.cut(df["wick_high"], [-.01, .1, .3, .5, 1.01], labels=False)
    f["pavio_inf"] = pd.cut(df["wick_low"], [-.01, .1, .3, .5, 1.01], labels=False)
    f["rsi"] = pd.cut(df["rsi"], [0, 20, 30, 45, 55, 70, 80, 100], labels=False)
    f["rsi7"] = pd.cut(df["rsi7"], [0, 10, 20, 35, 65, 80, 90, 100], labels=False)
    pb = (c - df["bb_dn"]) / (df["bb_up"] - df["bb_dn"]).replace(0, np.nan)
    f["bb"] = pd.cut(pb, [-9, -.1, 0, .2, .8, 1, 1.1, 9], labels=False)
    f["stoch"] = pd.cut(df["stoch_k"], [-1, 10, 20, 80, 90, 101], labels=False)
    f["volat"] = pd.cut(df["atr_pct"], [0, .6, .9, 1.2, 1.8, 99], labels=False)
    f["volume"] = pd.cut(df["vol"] / df["vol"].rolling(48).median(), [0, .5, 1, 2, 4, 1e9], labels=False)
    f["ema"] = np.sign(df["ema20"] - df["ema50"]).astype(int) * 1 + np.sign(c - df["ema200"]).astype(int) * 3
    f["h4"] = df["trend_h4"].fillna(0).astype(int)
    f["gigante"] = df["big_candle_recent"].astype(int)
    if btc is not None:  # ideia: BTC lidera alts — BTC andou forte nesta vela e a alt ficou para trás?
        bb = btc[~btc.index.duplicated()].reindex(df.index)
        lag = (bb["move_atr"] - (c - o) / df["atr"])
        f["btc_lead"] = pd.cut(lag, [-99, -1.5, -.7, .7, 1.5, 99], labels=False)
        f["btc_corpo"] = pd.cut(bb["move_atr"], [-99, -1.5, -.7, .7, 1.5, 99], labels=False)
    hits = {n: r(df) for n, r in REVERSAL_RULES}
    nosso = pd.concat(hits, axis=1)
    agree = nosso.replace(0, np.nan).nunique(axis=1) == 1
    f["nossa_rev"] = np.where(agree, nosso.replace(0, np.nan).max(axis=1), 0)
    f["vetos"] = (df["overlap_lon_ny"] | df["ny_news_window"] | df["fomc_window"] | df["end_of_hour"]).astype(int)
    f["y"], f["sym"], f["dia"] = df["y"], sym, loc.date
    return f.iloc[300:] if live else f.dropna(subset=["y"]).iloc[300:]


def scan(data: pd.DataFrame, cut, max_pairs: bool = True, min_n: int = 150) -> pd.DataFrame:
    """Para cada condição (1 ou 2 features), direção majoritária no treino; mede no teste."""
    tr, te = data[data.index < cut], data[data.index >= cut]
    feats = [c for c in data.columns if c not in ("y", "sym", "dia")]
    combos = [(a,) for a in feats] + (list(combinations(feats, 2)) if max_pairs else [])
    out = []
    for cols in combos:
        cols = list(cols)
        g_tr = tr.groupby(cols, observed=True)["y"].agg(["size", "sum"])
        g_tr = g_tr[g_tr["size"] >= min_n]
        if g_tr.empty:
            continue
        g_tr["wr"] = (g_tr["size"] + g_tr["sum"].abs()) / (2 * g_tr["size"])  # acerto na direção do sinal
        g_tr = g_tr[g_tr["wr"] >= 0.58]
        if g_tr.empty:
            continue
        g_te = te.groupby(cols, observed=True)["y"].agg(["size", "sum"])
        for key, r in g_tr.iterrows():
            d = np.sign(r["sum"])
            if key not in g_te.index:
                continue
            t = g_te.loc[key]
            wr_te = (t["size"] + d * t["sum"]) / (2 * t["size"])
            out.append(dict(regra=" & ".join(f"{c}={k}" for c, k in zip(cols, key if isinstance(key, tuple) else (key,))),
                            dir="CALL" if d > 0 else "PUT", n_tr=int(r["size"]), wr_tr=r["wr"],
                            n_te=int(t["size"]), wr_te=wr_te))
    return pd.DataFrame(out)


def main(rule: str) -> None:
    btc5 = None
    frames = []
    for s in SYMBOLS:
        if s == "BTCUSDT":
            b = build(s, raw_tf(s, rule))
            btc5 = pd.DataFrame({"move_atr": (b["close"] - b["open"]) / b["atr"]})
        frames.append(frame(s, rule, None if s == "BTCUSDT" else btc5))
        print("ok", s, len(frames[-1]), flush=True)
    data = pd.concat(frames)
    data = data[data["y"] != 0]  # empate = devolução
    cut = data.index.min() + (data.index.max() - data.index.min()) * 8 / 12
    data.to_pickle(DATA / "binance" / f"_mine_{rule}.pkl")
    r = scan(data.drop(columns=["btc_lead", "btc_corpo"]).fillna(-1), cut)
    alts = data[data["sym"] != "BTCUSDT"]
    r2 = scan(alts.fillna(-1), cut)
    r2 = r2[r2["regra"].str.contains("btc_")]
    res = pd.concat([r, r2]).drop_duplicates("regra")
    days_te = data[data.index >= cut]["dia"].nunique()
    res["sinais_dia_te"] = (res["n_te"] / days_te).round(2)
    res = res[(res["wr_te"] >= 0.57) & (res["n_te"] >= 60)].sort_values("wr_te", ascending=False)
    res.to_csv(DATA.parent / "replica" / f"mine_{rule}.csv", index=False)
    pd.set_option("display.width", 250)
    print(f"\n== {rule}: {len(res)} regras OK (teste {days_te} dias, breakeven {BE:.3f}) ==")
    print(res.head(40).round(3).to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "5min")
