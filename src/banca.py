"""Simula banca nos 4 meses de teste: R$1000, entrada R$25, stop diário -R$125,
entrada +10% a cada +15% de banca (degraus; volta degrau se a banca cair)."""
import math, numpy as np, pandas as pd
from features import DATA
PAY = 0.82
d5 = pd.read_pickle(DATA/"binance"/"_mine_5min.pkl"); d15 = pd.read_pickle(DATA/"binance"/"_mine_15min.pkl")
cut = d5.index.min() + (d5.index.max()-d5.index.min())*8/12

def sinais(d, m, dirn, tf):
    t = d[m & (d.index >= cut)]
    return pd.DataFrame({"win": (t["y"] == dirn).values, "sym": t["sym"].values, "tf": tf}, index=t.index)

def sim(name, s):
    s = s.sort_index(); bank, peak, mdd = 1000.0, 1000.0, 0
    day_pnl, cur_day, stops, streak, worst = 0, None, 0, 0, 0
    curve = []
    for ts, r in s.iterrows():
        day = ts.tz_convert("America/Sao_Paulo").date()
        if day != cur_day: cur_day, day_pnl, stopped = day, 0, False
        if stopped: continue
        lvl = max(0, math.floor(math.log(bank/1000)/math.log(1.15))) if bank > 0 else 0
        stake = round(25*1.1**lvl, 2)
        if bank < stake: break
        pnl = stake*PAY if r["win"] else -stake
        bank += pnl; day_pnl += pnl
        streak = 0 if r["win"] else streak+1; worst = max(worst, streak)
        peak = max(peak, bank); mdd = max(mdd, (peak-bank)/peak)
        if day_pnl <= -125: stopped = True; stops += 1
        curve.append((day, bank, r["win"]))
    c = pd.DataFrame(curve, columns=["dia","banca","win"])
    daily = c.groupby("dia")["banca"].last().diff()
    print(f"\n{name}\n  entradas {len(c)}  acerto {c.win.mean():.1%}  banca final R${bank:,.2f} ({bank/1000-1:+.0%})"
          f"\n  entrada final R${25*1.1**max(0,math.floor(math.log(bank/1000)/math.log(1.15))):.2f}  drawdown máx {mdd:.1%}  pior sequência de LOSS {worst}"
          f"\n  stops diários {stops}  dias negativos {(daily<0).sum()}/{len(daily)}  pior dia R${daily.min():.0f}  melhor dia R${daily.max():.0f}")
    m = c.groupby(pd.to_datetime(c.dia).dt.strftime("%m/%y"))["banca"].last()
    print("  banca fim do mês:", " | ".join(f"{k}: R${v:,.0f}" for k, v in m.items()))

ex = (d5.corpo == 4) & (d5.seq >= 3)
top = d5.sym.isin(["ETHUSDT", "SOLUSDT", "XRPUSDT"])
m15 = (d15.seq <= -3) & (d15.corpo >= 2) & (d15.stoch <= 1)
sim("A) Exaustão Verde + vetos (todas)", sinais(d5, ex & (d5.vetos == 0), -1, "M5"))
sim("B) Exaustão Verde pura (todas)", sinais(d5, ex, -1, "M5"))
sim("C) Exaustão Verde só ETH/SOL/XRP", sinais(d5, ex & top, -1, "M5"))
sim("D) A + complemento M15 CALL", pd.concat([sinais(d5, ex & (d5.vetos == 0), -1, "M5"), sinais(d15, m15, 1, "M15")]))
