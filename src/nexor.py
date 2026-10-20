"""Reprodução do "placar de confluência" do Nexor (recon em replica/recon.md).

Sete leituras com pesos (total ±11). Regras exatas não são públicas: as abaixo seguem o texto
exibido na análise (EURUSD M5) e são aproximações — marcadas onde é palpite.
Sem look-ahead: pivôs só contam depois de confirmados.
"""
import numpy as np
import pandas as pd

from features import ema

PIVOT_W = 3          # pivô confirmado após 3 velas
BOS_MEMORY = 12      # BOS vale por 12 velas (1h) — palpite
ZONE_MEMORY = 48     # OB/FVG vivos por 4h — palpite


def _pivots(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    h, l, w = df["high"], df["low"], PIVOT_W
    ph = h.where(h == h.rolling(2 * w + 1, center=True).max()).shift(w).ffill()
    pl = l.where(l == l.rolling(2 * w + 1, center=True).min()).shift(w).ffill()
    return ph, pl


def moving_averages(df) -> pd.Series:  # ±2: alinhamento EMA 9/21/50
    e9, e21, e50 = ema(df["close"], 9), ema(df["close"], 21), ema(df["close"], 50)
    return pd.Series(np.select([(e9 > e21) & (e21 > e50), (e9 < e21) & (e21 < e50)], [2, -2], 0), df.index)


def structure(df) -> tuple[pd.Series, np.ndarray, np.ndarray]:  # ±3: BOS recente
    ph, pl = _pivots(df)
    c = df["close"]
    up = (c > ph) & (c.shift() <= ph.shift())
    dn = (c < pl) & (c.shift() >= pl.shift())
    last = pd.Series(np.select([up, dn], [3, -3], np.nan), df.index).ffill(limit=BOS_MEMORY).fillna(0)
    return last, up.values, dn.values


def order_blocks(df, bos_up, bos_dn) -> pd.Series:  # ±2: preço dentro do OB mais recente
    o, c, h, l = (df[k].values for k in ("open", "close", "high", "low"))
    out = np.zeros(len(df))
    bull = bear = None  # (low, high, idx)
    for i in range(1, len(df)):
        if bos_up[i]:
            j = next((k for k in range(i - 1, max(i - 15, 0), -1) if c[k] < o[k]), None)
            bull = (l[j], h[j], i) if j is not None else bull
        if bos_dn[i]:
            j = next((k for k in range(i - 1, max(i - 15, 0), -1) if c[k] > o[k]), None)
            bear = (l[j], h[j], i) if j is not None else bear
        if bull and i - bull[2] <= ZONE_MEMORY and bull[0] <= c[i] <= bull[1]:
            out[i] = 2
        elif bear and i - bear[2] <= ZONE_MEMORY and bear[0] <= c[i] <= bear[1]:
            out[i] = -2
    return pd.Series(out, df.index)


def bollinger(df) -> pd.Series:  # ±1 — palpite: acima/abaixo da média dentro das bandas; fora = 0
    mid, sd = df["close"].rolling(20).mean(), df["close"].rolling(20).std()
    pb = (df["close"] - (mid - 2 * sd)) / (4 * sd)
    return pd.Series(np.select([(pb > 0.5) & (pb < 1), (pb < 0.5) & (pb > 0)], [1, -1], 0), df.index)


def rsi_read(df) -> pd.Series:  # ±1: RSI(14) > 55 compra, < 45 venda (60 foi "força compradora")
    return pd.Series(np.select([df["rsi"] > 55, df["rsi"] < 45], [1, -1], 0), df.index)


def macd(df) -> pd.Series:  # ±1: histograma positivo e crescendo / negativo e caindo
    c = df["close"]
    line = ema(c, 12) - ema(c, 26)
    hist = line - ema(line, 9)
    return pd.Series(np.select([(hist > 0) & (hist > hist.shift()), (hist < 0) & (hist < hist.shift())], [1, -1], 0),
                     df.index)


def fvg(df) -> pd.Series:  # ±1: preço dentro de FVG recente (gap de 3 velas não preenchido)
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    out = np.zeros(len(df))
    zones: list[tuple[float, float, int, int]] = []  # (lo, hi, sinal, idx)
    for i in range(2, len(df)):
        if l[i] > h[i - 2]:
            zones.append((h[i - 2], l[i], 1, i))
        elif h[i] < l[i - 2]:
            zones.append((h[i], l[i - 2], -1, i))
        zones = [z for z in zones if i - z[3] <= ZONE_MEMORY]
        for lo, hi, sign, idx in reversed(zones):
            if idx < i and lo <= c[i] <= hi:
                out[i] = sign
                break
    return pd.Series(out, df.index)


def score(df: pd.DataFrame) -> pd.DataFrame:
    smc, bos_up, bos_dn = structure(df)
    parts = pd.DataFrame({
        "ma": moving_averages(df), "smc": smc, "ob": order_blocks(df, bos_up, bos_dn),
        "bb": bollinger(df), "rsi": rsi_read(df), "macd": macd(df), "fvg": fvg(df),
    })
    parts["total"] = parts.sum(axis=1)
    return parts
