import math, pandas as pd
from banca import d5, sinais, PAY
s = sinais(d5, (d5.corpo==4)&(d5.seq>=3)&(d5.vetos==0), -1, "M5").sort_index()
def run(mode, pause_after=None):
    bank=1000.; peak=1000.; mdd=0; cur=None
    for ts,r in s.iterrows():
        day=ts.tz_convert("America/Sao_Paulo").date()
        if day!=cur: cur,open_bank,dpnl,stop,lstreak=day,bank,0,False,0
        if stop: continue
        if mode=="degraus": stake=25*1.1**max(0,math.floor(math.log(bank/1000)/math.log(1.15))); lim=125
        else: stake=round(open_bank*0.025,2); lim=open_bank*0.125
        p=stake*PAY if r.win else -stake; bank+=p; dpnl+=p
        lstreak=0 if r.win else lstreak+1
        peak=max(peak,bank); mdd=max(mdd,1-bank/peak)
        if dpnl<=-lim or (pause_after and lstreak>=pause_after): stop=True
    return f"{mode:12s} pausa{pause_after}: final R${bank:,.0f}  dd {mdd:.1%}"
for m in ("degraus","proporcional"):
    for p in (None,3,4): print(run(m,p))
