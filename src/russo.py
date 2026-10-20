"""Backtest das métricas do Algoritmo Russo (RSI, StochRSI, %B, MACD, EMA20/50) em M5,
expiração 5 e 15 min, com e sem os vetos do HZ Sinais. Regras determinísticas que imitam
a leitura da IA deles (extremos = reversão; MACD/EMA = tendência)."""
import numpy as np
import pandas as pd

from features import DATA, build, ema, rsi

SYMBOLS = sorted(p.stem for p in DATA.glob("*.json") if not p.stem.startswith("_"))


def russo_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    r = rsi(c)
    lo, hi = r.rolling(14).min(), r.rolling(14).max()
    df["srsi_k"] = (100 * (r - lo) / (hi - lo).replace(0, np.nan)).rolling(3).mean()
    macd = ema(c, 12) - ema(c, 26)
    df["macd_hist"] = macd - ema(macd, 9)
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    df["pct_b"] = (c - (mid - 2 * sd)) / (4 * sd).replace(0, np.nan)
    rev = pd.DataFrame({
        "rsi": np.where(df["rsi"] > 70, -1, np.where(df["rsi"] < 30, 1, 0)),
        "srsi": np.where(df["srsi_k"] > 80, -1, np.where(df["srsi_k"] < 20, 1, 0)),
        "bb": np.where(df["pct_b"] > 1, -1, np.where(df["pct_b"] < 0, 1, 0)),
    }, index=df.index)
    trend = pd.DataFrame({
        "macd": np.sign(df["macd_hist"]),
        "ema": np.sign(df["ema20"] - df["ema50"]),
    }, index=df.index)
    df["sig_reversao"] = np.where(rev.sum(axis=1).abs() == 3, np.sign(rev.sum(axis=1)), 0)
    df["sig_rsi"] = rev["rsi"]  # leitura principal da IA: RSI extremo => contra
    df["sig_tendencia"] = np.where((trend.sum(axis=1).abs() == 2) & rev["rsi"].eq(0), trend.sum(axis=1) / 2, 0)
    votes = rev.sum(axis=1) + trend.sum(axis=1)  # "X CALL vs Y PUT"
    df["sig_votacao"] = np.where(votes.abs() >= 4, np.sign(votes), 0)
    for n, name in ((1, "m5"), (3, "m15")):
        df[f"move_{name}"] = np.sign(c.shift(-n) - c)
    return df


def vetoed(df: pd.DataFrame) -> pd.Series:
    return (df["overlap_lon_ny"] | df["ny_news_window"] | df["fomc_window"]
            | df["news_high"] | df["end_of_hour"])


def main() -> None:
    rows = []
    for sym in SYMBOLS:
        df = russo_indicators(build(sym))
        df = df.dropna(subset=["move_m15"])
        if len(df) < 1000:
            print("pulando", sym, len(df))
            continue
        cut = df.index[int(len(df) * 0.7)]
        for strat in ("sig_rsi", "sig_reversao", "sig_tendencia", "sig_votacao"):
            for exp in ("m5", "m15"):
                for filt in ("sem", "vetos", "vetos+A"):
                    m = df[strat] != 0
                    if filt != "sem":
                        m &= ~vetoed(df)
                    if filt == "vetos+A":
                        m &= df["big_candle_recent"]
                    t = df[m & (df[f"move_{exp}"] != 0)]
                    win = t[f"move_{exp}"] == t[strat]
                    rows.append(dict(sym=sym, strat=strat[4:], exp=exp, filt=filt,
                                     n=len(t), w=int(win.sum()), payout=t["payout"].mean() if len(t) else 0,
                                     n_test=int((t.index >= cut).sum()), w_test=int(win[t.index >= cut].sum())))
    r = pd.DataFrame(rows)
    r["pnl"] = r["w"] * r["payout"] - (r["n"] - r["w"])
    g = r.groupby(["strat", "exp", "filt"]).agg(n=("n", "sum"), w=("w", "sum"), pnl=("pnl", "sum"),
                                                  n_test=("n_test", "sum"), w_test=("w_test", "sum"))
    g["acerto%"] = (100 * g["w"] / g["n"]).round(1)
    g["teste%"] = (100 * g["w_test"] / g["n_test"]).round(1)
    g["EV/entrada"] = (g["pnl"] / g["n"]).round(3)
    print(g[["n", "acerto%", "n_test", "teste%", "EV/entrada"]].to_string())
    print("\nbreakeven ~54% (payout 85%) / ~55% (82%)")
    r.to_csv(DATA.parent / "replica" / "russo_backtest.csv", index=False)


if __name__ == "__main__":
    main()
