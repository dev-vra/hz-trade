"""Modelo de leitura M5 -> direção dos próximos 15 min (entrada no fechamento da vela, expira 3 velas depois).
Treino walk-forward: tudo antes do corte treina, últimos TEST_MONTHS testam. Reporta acerto real por faixa de
probabilidade (calibração) — o '% de força' do app só vale se essa tabela confirmar.
Uso: SRC=binance3y python train.py"""
import json
import os

import lightgbm as lgb
import numpy as np
import pandas as pd

from mine import SRC, SYMBOLS, build, frame, raw_tf

TEST_MONTHS = int(os.environ.get("TEST_MONTHS", 6))
HORIZON = int(os.environ.get("HORIZON", 3))  # velas M5: 1=5min 3=15min 6=30min 12=1h
OUT = SRC.parent.parent / "replica"


def extra_feats(f: pd.DataFrame, raw: dict) -> pd.DataFrame:
    """Retornos e suporte/resistência (distância em ATR até máx/mín de 4h e 24h, toques na zona)."""
    idx = pd.to_datetime(raw["t"], unit="s", utc=True)
    c, h, lo = (pd.Series(raw[k], index=idx) for k in "chl")
    c, h, lo = (x[~x.index.duplicated()] for x in (c, h, lo))
    f["ret3"] = (c / c.shift(3) - 1).reindex(f.index) * 100
    f["ret12"] = (c / c.shift(12) - 1).reindex(f.index) * 100
    atr = (h - lo).rolling(14).mean()
    for n, tag in ((48, "4h"), (288, "24h")):
        hi_n, lo_n = h.shift(1).rolling(n).max(), lo.shift(1).rolling(n).min()
        f[f"d_res{tag}"] = ((hi_n - c) / atr).reindex(f.index)
        f[f"d_sup{tag}"] = ((c - lo_n) / atr).reindex(f.index)
        f[f"toq_res{tag}"] = ((h >= hi_n - .3 * atr).astype(int).rolling(n).sum()).reindex(f.index)
        f[f"toq_sup{tag}"] = ((lo <= lo_n + .3 * atr).astype(int).rolling(n).sum()).reindex(f.index)
    return f


def dataset() -> pd.DataFrame:
    b = build("BTCUSDT", raw_tf("BTCUSDT", "5min"))
    btc = pd.DataFrame({"move_atr": (b["close"] - b["open"]) / b["atr"]})
    parts = []
    for s in SYMBOLS:
        raw = raw_tf(s, "5min")
        f = frame(s, "5min", None if s == "BTCUSDT" else btc)
        c = pd.Series(raw["c"], index=pd.to_datetime(raw["t"], unit="s", utc=True))
        c = c[~c.index.duplicated()]
        f["y"] = np.sign(c.shift(-HORIZON) - c).reindex(f.index)
        extra_feats(f, raw)
        f["cores3"] = f["cores3"].astype("category")
        f["sym"] = f["sym"].astype("category")
        parts.append(f.dropna(subset=["y"]).astype({c: "float32" for c in f.columns
                                                     if f[c].dtype == "float64"}))
        print("ok", s, len(parts[-1]), flush=True)
    d = pd.concat(parts)
    d["cores3"] = d["cores3"].astype("category")
    d["sym"] = d["sym"].astype("category")
    return d[d["y"] != 0]


def rules(d: pd.DataFrame, cut) -> None:
    """Estratégias de regra fixa (pullback/rejeição em S/R), medidas em treino e teste."""
    verde = d["cores3"].astype(str).str.endswith("1") & ~d["cores3"].astype(str).str.endswith("-1")
    verm = d["cores3"].astype(str).str.endswith("-1")
    R = {
        "pullback_alta_sup4h CALL": ((d["ema"] == 4) & (d["d_sup4h"] <= .5) & verm, 1),
        "pullback_baixa_res4h PUT": ((d["ema"] == -4) & (d["d_res4h"] <= .5) & verde, -1),
        "pullback_alta_ema CALL": ((d["ema"] == 4) & d["bb"].isin([2]) & verm, 1),
        "pullback_baixa_ema PUT": ((d["ema"] == -4) & d["bb"].isin([4]) & verde, -1),
        "rejeicao_res24h PUT": ((d["d_res24h"] <= .3) & (d["toq_res24h"] >= 3) & (d["pavio_sup"] >= 2), -1),
        "rejeicao_sup24h CALL": ((d["d_sup24h"] <= .3) & (d["toq_sup24h"] >= 3) & (d["pavio_inf"] >= 2), 1),
        "rompe_res24h CALL": ((d["d_res24h"] < 0) & (d["volume"] >= 3), 1),
        "rompe_sup24h PUT": ((d["d_sup24h"] < 0) & (d["volume"] >= 3), -1),
    }
    out = []
    for name, (mask, dr) in R.items():
        for per, part in (("treino", d.index < cut), ("teste", d.index >= cut)):
            k = mask & part
            n = int(k.sum())
            days = d.loc[part, "dia"].nunique()
            out.append(dict(regra=name, periodo=per, n=n, por_dia=round(n / days, 1),
                            acerto=round((d.loc[k, "y"] == dr).mean(), 4) if n else None))
    print(f"\n== regras S/R, expiração {HORIZON * 5} min ==")
    print(pd.DataFrame(out).pivot(index="regra", columns="periodo", values=["por_dia", "acerto"]).to_string())


def main() -> None:
    d = dataset()
    cut = d.index.max() - pd.DateOffset(months=TEST_MONTHS)
    rules(d, cut)
    feats = [c for c in d.columns if c not in ("y", "dia")]
    tr, te = d[d.index < cut], d[d.index >= cut]
    val_cut = tr.index.max() - pd.DateOffset(months=2)
    fit, val = tr[tr.index < val_cut], tr[tr.index >= val_cut]
    m = lgb.LGBMClassifier(n_estimators=2000, learning_rate=0.02, num_leaves=31, min_child_samples=500,
                           subsample=0.7, subsample_freq=1, colsample_bytree=0.7, reg_lambda=5, verbose=-1)
    m.fit(fit[feats], fit["y"] > 0, eval_set=[(val[feats], val["y"] > 0)],
          callbacks=[lgb.early_stopping(100, verbose=False)])
    p = m.predict_proba(te[feats])[:, 1]
    conf = np.abs(p - 0.5) * 2  # 0..1
    te.assign(p=p)[["sym", "dia", "y", "p", "d_sup24h", "d_res24h", "volume", "vetos"]].to_pickle(
        SRC.parent / f"_pred{HORIZON * 5}.pkl")
    win = np.where(p > 0.5, te["y"] > 0, te["y"] < 0)
    days = te["dia"].nunique()
    rows = []
    for lo in (0, .02, .04, .06, .08, .10, .15, .20):
        k = conf >= lo
        rows.append(dict(conf_min=lo, n=int(k.sum()), por_dia=round(k.sum() / days, 1),
                         acerto=round(win[k].mean(), 4) if k.any() else None))
    tab = pd.DataFrame(rows)
    print(f"\nteste {te.index.min():%Y-%m-%d}..{te.index.max():%Y-%m-%d} ({days} dias), "
          f"árvores={m.best_iteration_}, breakeven 0.549")
    print(tab.to_string(index=False))
    imp = pd.Series(m.booster_.feature_importance("gain"), feats).sort_values(ascending=False)
    print("\nimportância:\n", (imp / imp.sum()).round(3).head(15).to_string())
    OUT.mkdir(exist_ok=True)
    tab.to_csv(OUT / f"modelo{HORIZON*5}_calib.csv", index=False)
    m.booster_.save_model(str(SRC.parent / f"modelo{HORIZON*5}.txt"))
    (SRC.parent / f"modelo{HORIZON*5}_feats.json").write_text(json.dumps(feats))


if __name__ == "__main__":
    main()
