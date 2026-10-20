import numpy as np, pandas as pd
from features import DATA
d = pd.read_pickle(DATA/"binance"/"_mine_5min.pkl")
d15 = pd.read_pickle(DATA/"binance"/"_mine_15min.pkl")
loc = d.index.tz_convert("America/Sao_Paulo")
cut = d.index.min() + (d.index.max()-d.index.min())*8/12

def ev(w): return w.mean()*0.82-(1-w.mean())
def show(name, d, m, dirn, cut):
    t = d[m]; w = t["y"]==dirn
    tr, te = w[t.index<cut], w[t.index>=cut]
    perday = t[t.index>=cut].groupby("dia").size()
    days = d[d.index>=cut]["dia"].nunique()
    print(f"{name:55s} treino {tr.mean():.3f}(n{len(tr)})  TESTE {te.mean():.3f}(n{len(te)}) EV{ev(te):+.3f}  sinais/dia {len(te)/days:.1f}  dias>=4: {(perday>=4).sum()}/{days}")

# T2 variantes
green_exh = (d["corpo"]==4)&(d["seq"]>=3)
show("T2 base", d, green_exh, -1, cut)
show("T2 + vetos", d, green_exh&(d["vetos"]==0), -1, cut)
show("T2 + rsi7>80", d, green_exh&(d["rsi7"]>=5), -1, cut)
show("T2 + BTC também explodiu (alts)", d, green_exh&(d["btc_corpo"]==4), -1, cut)
show("T2 + volume>=2x", d, green_exh&(d["volume"]>=3), -1, cut)
show("T2 seq>=2 corpo>=1.2ATR rsi7>80", d, (d["corpo"]>=3)&(d["seq"]>=2)&(d["rsi7"]>=5), -1, cut)
show("T2 + bb acima da banda", d, green_exh&(d["bb"]>=5), -1, cut)
# horas escolhidas no TREINO para T2
t = d[green_exh&(d.index<cut)]
hw = (t["y"]==-1).groupby(loc[green_exh&(d.index<cut)].hour).agg(["mean","size"])
good = hw[(hw["mean"]>=0.60)&(hw["size"]>=40)].index.tolist()
print("horas boas (treino):", good)
show("T2 + horas do treino", d, green_exh&np.isin(loc.hour, good), -1, cut)
# M15 equivalente
l15 = d15.index.tz_convert("America/Sao_Paulo"); cut15 = cut
g15 = (d15["corpo"]==4)&(d15["seq"]>=2)
show("M15: 2+ verdes + gigante PUT", d15, g15, -1, cut15)
show("M15: 3+ verdes + corpo>=1.2", d15, (d15["corpo"]>=3)&(d15["seq"]>=3), -1, cut15)
show("M15: 3+ vermelhas corpo>=0.7 CALL", d15, (d15["seq"]<=-3)&(d15["corpo"]>=2), 1, cut15)
show("M15: 3+ verm. corpo>=0.7 + stoch<20 CALL", d15, (d15["seq"]<=-3)&(d15["corpo"]>=2)&(d15["stoch"]<=1), 1, cut15)
show("M15: rsi<30 + vela >=1.2ATR CALL", d15, (d15["rsi"]<=1)&(d15["corpo"]>=3), 1, cut15)
# por ativo T2
for s,g in d[green_exh].groupby("sym"):
    w=(g["y"]==-1); print(f"  {s}: {w.mean():.3f} n{len(g)}  teste {w[g.index>=cut].mean():.3f}")
