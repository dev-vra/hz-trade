"""Valida técnicas compostas (escolhidas a partir do mine.py) mês a mês, 1 ano Binance."""
import numpy as np
import pandas as pd
from features import DATA

def load(rule):
    d = pd.read_pickle(DATA / "binance" / f"_mine_{rule}.pkl")
    return d

def report(name, d, mask, direction):
    t = d[mask]
    w = (t["y"] == direction) if np.isscalar(direction) else (t["y"] == direction[mask])
    m = pd.DataFrame({"w": w.values}, index=t.index.tz_convert("America/Sao_Paulo"))
    mo = m.groupby(m.index.strftime("%y-%m"))["w"].agg(["size", "mean"])
    days = d["dia"].nunique()
    ev = w.mean() * 0.82 - (1 - w.mean())
    print(f"\n{name}: n={len(t)}  acerto={w.mean():.3f}  EV/entrada={ev:+.3f}  sinais/dia={len(t)/days:.1f}  meses<55%: {(mo['mean']<0.55).sum()}/{len(mo)}")
    print("  mensal:", " ".join(f"{i}:{r['mean']:.0%}({int(r['size'])})" for i, r in mo.iterrows()))
    h = m.groupby(m.index.hour)["w"].agg(["size", "mean"])
    print("  por hora BRT:", " ".join(f"{i}h:{r['mean']:.0%}({int(r['size'])})" for i, r in h.iterrows() if r["size"] >= 15))

d5 = load("5min"); d15 = load("15min")
alt5 = d5["sym"] != "BTCUSDT"
ok5 = d5["vetos"] == 0
# T1 ideia minha: BTC explode e a alt acompanhou (verde forte) -> PUT na alt na próxima M5
t1 = alt5 & (d5["btc_corpo"] == 4) & ((d5["corpo"] >= 3) | (d5["cores3"] == "111"))
report("T1 BTC-exaustão PUT (M5)", d5, t1, -1)
report("T1 + vetos", d5, t1 & ok5, -1)
t1c = alt5 & (d5["btc_corpo"] == 0) & ((d5["corpo"] >= 3) | (d5["cores3"] == "-1-1-1"))
report("T1 espelho CALL (BTC despenca)", d5, t1c, 1)
# T2 exaustão M5 isolada: vela gigante verde (>2ATR) após sequência >=3 ou rsi7>90
t2 = (d5["corpo"] == 4) & (d5["seq"] >= 3)
report("T2 sequência verde + vela gigante PUT (M5)", d5, t2, -1)
t2b = (d5["corpo"] == 4) & (d5["seq"] <= -3)
report("T2 espelho CALL", d5, t2b, 1)
# T3 M15: 3+ vermelhas com corpo médio/grande -> CALL
t3 = (d15["seq"] <= -3) & (d15["corpo"] >= 2)
report("T3 M15 3+ vermelhas corpo>=0.7ATR CALL", d15, t3, 1)
t3p = (d15["seq"] >= 3) & (d15["corpo"] >= 2)
report("T3 espelho PUT", d15, t3p, -1)
# nossa estratégia (S5/S2b/S2) em M15, sem e com vetos
nr = d15["nossa_rev"] != 0
report("NOSSA reversão M15 sem filtro", d15, nr, d15["nossa_rev"])
report("NOSSA reversão M15 + vetos", d15, nr & (d15["vetos"] == 0), d15["nossa_rev"])
report("NOSSA reversão M15 + vetos + gigante(A)", d15, nr & (d15["vetos"] == 0) & (d15["gigante"] == 1), d15["nossa_rev"])
nr5 = d5["nossa_rev"] != 0
report("NOSSA reversão M5 cripto + vetos", d5, nr5 & ok5, d5["nossa_rev"])
