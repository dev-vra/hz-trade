"""Combina modelo IA (15 min) + rompimento falso de S/R 24h e simula banca no período de teste.
Uso: SRC=binance3y python combo.py  (precisa de _pred15.pkl gerado por train.py)"""
import numpy as np
import pandas as pd

from engine import StakePlan
from mine import SRC

PAYOUT = 0.82


def signals(d: pd.DataFrame) -> dict[str, pd.Series]:
    ia = np.sign(d["p"] - .5)
    conf = (d["p"] - .5).abs() * 2
    fs = ((d["d_sup24h"] < 0) & (d["volume"] >= 3)).astype(int)        # furou suporte -> CALL
    fr = -((d["d_res24h"] < 0) & (d["volume"] >= 3)).astype(int)       # furou resistência -> PUT
    rf = fs + fr
    return {
        "IA>=20": ia.where(conf >= .20, 0),
        "IA>=15": ia.where(conf >= .15, 0),
        "falso_rompimento": rf,
        "falso_romp + IA concorda": rf.where(ia == rf, 0),
        "falso_romp + IA>=10 concorda": rf.where((ia == rf) & (conf >= .10), 0),
        "falso_romp + IA>=15 concorda": rf.where((ia == rf) & (conf >= .15), 0),
        "falso_romp sem vetos": rf.where(d["vetos"] == 0, 0),
        "IA>=20 OU (falso_romp+IA>=10)": ia.where((conf >= .20) | ((ia == rf) & (conf >= .10)), 0),
    }


def dedupe(sig: pd.Series, d: pd.DataFrame, max_simul: int = 3) -> pd.Series:
    """1 operação aberta por ativo (15 min = 3 velas) e no máx. max_simul entradas no mesmo minuto."""
    s = d.loc[sig != 0, ["sym", "y"]].assign(dir=sig[sig != 0]).sort_index()
    keep, busy, cnt = [], {}, {}
    for t, sym, y, dr in zip(s.index, s["sym"], s["y"], s["dir"]):
        if cnt.get(t, 0) >= max_simul or busy.get(sym, t) > t:
            continue
        busy[sym] = t + pd.Timedelta(minutes=15)
        cnt[t] = cnt.get(t, 0) + 1
        keep.append((t, sym, dr, y == dr))
    return pd.DataFrame(keep, columns=["t", "sym", "dir", "win"])


def bank(trades: pd.DataFrame, base: float = 1000) -> dict:
    plan, bal, peak, dd = StakePlan(base), base, base, 0.0
    day, pnl_day = None, 0.0
    for r in trades.itertuples():
        dd_day = r.t.tz_convert("America/Sao_Paulo").date()
        if dd_day != day:
            day, pnl_day = dd_day, 0.0
        if not plan.can_trade(pnl_day, bal):
            continue
        st = plan.stake(bal)
        g = st * PAYOUT if r.win else -st
        bal += g
        pnl_day += g
        peak = max(peak, bal)
        dd = max(dd, 1 - bal / peak)
    return dict(banca_final=round(bal), dd_max=f"{dd:.1%}")


def main() -> None:
    d = pd.read_pickle(SRC.parent / "_pred15.pkl")
    days = d["dia"].nunique()
    rows = []
    for name, sig in signals(d).items():
        k = sig != 0
        raw_wr = (d.loc[k, "y"] == sig[k]).mean()
        tr = dedupe(sig, d)
        rows.append(dict(estrategia=name, sinais_dia=round(k.sum() / days, 1), acerto=round(raw_wr, 4),
                         operadas_dia=round(len(tr) / days, 1), acerto_operadas=round(tr["win"].mean(), 4),
                         **bank(tr.sort_values("t"))))
        print(rows[-1], flush=True)
    pd.set_option("display.width", 220)
    print(f"\nteste {days} dias, breakeven 0.549\n", pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
