"""Estratégias-base. Cada uma devolve direção do sinal por barra: +1 CALL, -1 PUT, 0 nada."""
import numpy as np
import pandas as pd


def trend_pullback(df: pd.DataFrame) -> pd.Series:
    """S1: tendência M15 + toque na EMA20 do M5 e vela de rejeição a favor."""
    up = (df["trend_m15"] > 0) & (df["ema20"] > df["ema50"]) & (df["low"] <= df["ema20"]) & (df["close"] > df["ema20"]) & (df["color"] > 0)
    dn = (df["trend_m15"] < 0) & (df["ema20"] < df["ema50"]) & (df["high"] >= df["ema20"]) & (df["close"] < df["ema20"]) & (df["color"] < 0)
    return pd.Series(np.select([up, dn], [1, -1], 0), df.index)


def bb_reversal(df: pd.DataFrame) -> pd.Series:
    """S2: exaustão — fecha fora da Bollinger(20, 2) com RSI extremo, mercado sem tendência forte."""
    ranging = df["adx"] < 25
    up = ranging & (df["close"] < df["bb_dn"]) & (df["rsi"] < 30)
    dn = ranging & (df["close"] > df["bb_up"]) & (df["rsi"] > 70)
    return pd.Series(np.select([up, dn], [1, -1], 0), df.index)


def streak_reversal(df: pd.DataFrame, n: int = 5) -> pd.Series:
    """S3: n velas seguidas da mesma cor -> aposta na reversão (estratégia popular em binárias)."""
    same = df["color"].rolling(n).sum()
    return pd.Series(np.select([same == n, same == -n], [-1, 1], 0), df.index)


def momentum_breakout(df: pd.DataFrame) -> pd.Series:
    """S4: vela de corpo forte rompendo máxima/mínima das últimas 12 barras, a favor do M15."""
    hh = df["high"].rolling(12).max().shift()
    ll = df["low"].rolling(12).min().shift()
    up = (df["close"] > hh) & (df["body"] > 0.8) & (df["trend_m15"] > 0)
    dn = (df["close"] < ll) & (df["body"] < -0.8) & (df["trend_m15"] < 0)
    return pd.Series(np.select([up, dn], [1, -1], 0), df.index)


def bb25_reversal(df: pd.DataFrame) -> pd.Series:
    """S2b: versão extrema do S2 — Bollinger 2.5 e RSI 25/75."""
    ranging = df["adx"] < 25
    up = ranging & (df["close"] < df["bb_dn25"]) & (df["rsi"] < 25)
    dn = ranging & (df["close"] > df["bb_up25"]) & (df["rsi"] > 75)
    return pd.Series(np.select([up, dn], [1, -1], 0), df.index)


def rsi7_reversal(df: pd.DataFrame) -> pd.Series:
    """S5: RSI(7) extremo 15/85 em mercado sem tendência."""
    ranging = df["adx"] < 25
    return pd.Series(np.select([ranging & (df["rsi7"] < 15), ranging & (df["rsi7"] > 85)], [1, -1], 0), df.index)


def stoch_cross(df: pd.DataFrame) -> pd.Series:
    """S6: %K cruza %D dentro da zona extrema (<20 compra, >80 venda)."""
    k, d = df["stoch_k"], df["stoch_d"]
    up = (k > d) & (k.shift() <= d.shift()) & (d < 20)
    dn = (k < d) & (k.shift() >= d.shift()) & (d > 80)
    return pd.Series(np.select([up, dn], [1, -1], 0), df.index)


def fade_breakout(df: pd.DataFrame) -> pd.Series:
    """S7: inverso do S4 — rompimento com vela forte tende a devolver no M5."""
    return -momentum_breakout(df)


def wick_rejection(df: pd.DataFrame) -> pd.Series:
    """S8: toque na Bollinger(20,2) com pavio de rejeição >= 50% da vela e RSI(7) esticado."""
    up = (df["low"] < df["bb_dn"]) & (df["close"] > df["bb_dn"]) & (df["wick_low"] >= 0.5) & (df["rsi7"] < 30)
    dn = (df["high"] > df["bb_up"]) & (df["close"] < df["bb_up"]) & (df["wick_high"] >= 0.5) & (df["rsi7"] > 70)
    return pd.Series(np.select([up, dn], [1, -1], 0), df.index)


def osc_turn(df: pd.DataFrame) -> pd.Series:
    """S9: Williams %R sai da zona extrema ou CCI volta de +-150 para dentro de +-100, ADX<20."""
    w, cci, calm = df["willr"], df["cci"], df["adx"] < 20
    dn = calm & (((w < -20) & (w.shift() >= -20)) | ((cci < 100) & (cci.shift() > 150)))
    up = calm & (((w > -80) & (w.shift() <= -80)) | ((cci > -100) & (cci.shift() < -150)))
    return pd.Series(np.select([up, dn], [1, -1], 0), df.index)


def keltner_bb(df: pd.DataFrame) -> pd.Series:
    """S10: fecha fora da Keltner(20,1.5ATR) E da BB(20,2) -> reversão."""
    up = (df["close"] < df["kc_dn"]) & (df["close"] < df["bb_dn"])
    dn = (df["close"] > df["kc_up"]) & (df["close"] > df["bb_up"])
    return pd.Series(np.select([up, dn], [1, -1], 0), df.index)


def mhi(df: pd.DataFrame) -> pd.Series:
    """S11 (controle): MHI — nas 3 últimas velas do bloco de 15min (min 0/15/30/45 +5/+10),
    aposta na cor minoritária. Sinal na vela que fecha o trio (minuto 10 do bloco)."""
    last3 = df["color"].rolling(3).sum()
    at_block = (df.index.minute % 15) == 10
    return pd.Series(np.select([at_block & (last3 > 0), at_block & (last3 < 0)], [-1, 1], 0), df.index)


def tdi_bb(df: pd.DataFrame) -> pd.Series:
    """S12: S2 (BB+RSI) confirmado pelo cruzamento TDI rápida x lenta na zona 68/32."""
    f, s = df["tdi_fast"], df["tdi_slow"]
    dn = (f < s) & (f.shift() >= s.shift()) & (s > 68) & (df["high"].rolling(3).max() > df["bb_up"].rolling(3).max())
    up = (f > s) & (f.shift() <= s.shift()) & (s < 32) & (df["low"].rolling(3).min() < df["bb_dn"].rolling(3).min())
    return pd.Series(np.select([up, dn], [1, -1], 0), df.index)


STRATEGIES = {
    "S1_trend_pullback": trend_pullback,
    "S2_bb_reversal": bb_reversal,
    "S3_streak_reversal": streak_reversal,
    "S4_momentum_breakout": momentum_breakout,
    "S2b_bb25_reversal": bb25_reversal,
    "S5_rsi7_reversal": rsi7_reversal,
    "S6_stoch_cross": stoch_cross,
    "S7_fade_breakout": fade_breakout,
    "S8_wick_rejection": wick_rejection,
    "S9_osc_turn": osc_turn,
    "S10_keltner_bb": keltner_bb,
    "S11_mhi_controle": mhi,
    "S12_tdi_bb": tdi_bb,
}
