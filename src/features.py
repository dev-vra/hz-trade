"""Carga de dados e features (indicadores, multi-timeframe, tempo, Fibonacci, estrutura)."""
import json
import pathlib
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
CRYPTO_SUFFIX = "USDT"
PAYOUT_FOREX = 0.85
PAYOUT_CRYPTO = 0.82
LONDON, NEW_YORK = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")


def load(symbol: str, raw: dict | None = None) -> pd.DataFrame:
    raw = raw or json.loads((DATA / f"{symbol}.json").read_text())
    df = pd.DataFrame({k: raw[k] for k in ("t", "o", "h", "l", "c")})
    df["t"] = pd.to_datetime(df["t"], unit="s", utc=True)
    df = df.drop_duplicates("t").set_index("t").sort_index()
    return df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(plus, df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus, df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    return df.resample(rule, label="left", closed="left").agg(agg).dropna()


def higher_tf_trend(df5: pd.DataFrame, rule: str, fast: int, slow: int) -> pd.Series:
    """+1/-1 da tendência no TF maior, usando só barras JÁ FECHADAS (sem look-ahead)."""
    htf = resample(df5, rule)
    trend = np.sign(ema(htf["close"], fast) - ema(htf["close"], slow))
    # barra HTF iniciada em T só fecha em T+rule: desloca o rótulo para o fechamento
    trend.index = trend.index + pd.Timedelta(rule)
    return trend.reindex(df5.index, method="ffill")


def fib_position(df: pd.DataFrame, lookback: int = 48) -> tuple[pd.Series, pd.Series]:
    """Retração do preço dentro do último swing (janela fechada) e direção do swing.
    0 = no extremo do swing, 1 = retraiu tudo. Zona de ouro ~0.5–0.618."""
    hi = df["high"].rolling(lookback).max().shift()
    lo = df["low"].rolling(lookback).min().shift()
    hi_pos = df["high"].rolling(lookback).apply(np.argmax, raw=True).shift()
    lo_pos = df["low"].rolling(lookback).apply(np.argmin, raw=True).shift()
    swing_up = hi_pos > lo_pos  # topo mais recente que o fundo => impulso de alta
    rng = (hi - lo).replace(0, np.nan)
    retr = np.where(swing_up, (hi - df["close"]) / rng, (df["close"] - lo) / rng)
    return pd.Series(retr, df.index), pd.Series(np.where(swing_up, 1, -1), df.index)


def structure_score(df: pd.DataFrame, w: int = 6, pivots: int = 4) -> pd.Series:
    """Proxy de estrutura de Elliott/Dow: saldo de topos e fundos ascendentes nos últimos pivôs.
    +pivots = impulso de alta limpo, -pivots = de baixa."""
    h, l = df["high"], df["low"]
    is_ph = h == h.rolling(2 * w + 1, center=True).max()
    is_pl = l == l.rolling(2 * w + 1, center=True).min()
    # pivô só é confirmado w barras depois
    ph = h.where(is_ph).shift(w).ffill()
    pl = l.where(is_pl).shift(w).ffill()
    hh = np.sign(ph.diff().where(ph.diff() != 0)).ffill().fillna(0)
    hl = np.sign(pl.diff().where(pl.diff() != 0)).ffill().fillna(0)
    return (hh + hl).rolling(pivots * w).mean() * 2  # -2..+2 escalado


def session_features(idx: pd.DatetimeIndex) -> pd.DataFrame:
    lon = idx.tz_convert(LONDON)
    ny = idx.tz_convert(NEW_YORK)
    lon_min = lon.hour * 60 + lon.minute
    ny_min = ny.hour * 60 + ny.minute
    out = pd.DataFrame(index=idx)
    out["hour_utc"] = idx.hour
    out["weekday"] = idx.weekday
    out["minute"] = idx.minute
    out["end_of_hour"] = idx.minute >= 50
    out["london_open_30"] = (lon_min >= 8 * 60) & (lon_min < 8 * 60 + 30)
    out["ny_open_30"] = (ny_min >= 8 * 60) & (ny_min < 8 * 60 + 30)  # forex NY ~08:00 ET; dados US 08:30
    out["ny_news_window"] = (ny_min >= 8 * 60 + 25) & (ny_min < 8 * 60 + 45)  # CPI/NFP/payroll 08:30 ET
    out["fomc_window"] = (ny_min >= 13 * 60 + 55) & (ny_min < 14 * 60 + 40) & (idx.weekday == 2)
    out["overlap_lon_ny"] = (lon_min >= 13 * 60) & (lon_min < 16 * 60 + 30)
    out["asia_session"] = (idx.hour >= 23) | (idx.hour < 7)
    return out


NEWS_BEFORE = pd.Timedelta("20min")
NEWS_AFTER = pd.Timedelta("30min")


def news_flags(idx: pd.DatetimeIndex, symbol: str) -> pd.DataFrame:
    """Evento do calendário (ForexFactory) das moedas do ativo perto da barra.
    Trade da barra t vive em [t+5, t+10]min; veta de 20min antes a 30min depois do evento."""
    path = DATA / "_news.json"
    out = pd.DataFrame(False, index=idx, columns=["news_high", "news_medium"])
    if not path.exists():
        return out
    ev = pd.DataFrame(json.loads(path.read_text()), columns=["ts", "cur", "impact", "name"])
    currencies = {"USD"} if symbol.endswith(CRYPTO_SUFFIX) else {symbol[:3], symbol[3:6]}
    ev = ev[ev["cur"].isin(currencies)]
    for impact in ("high", "medium"):
        times = pd.to_datetime(ev.loc[ev["impact"] == impact, "ts"], unit="s", utc=True).sort_values().values
        if len(times) == 0:
            continue
        pos = np.searchsorted(times, (idx - NEWS_AFTER).values)  # primeiro evento >= t-30min (já passou há até 30min)
        nxt = times[np.minimum(pos, len(times) - 1)]
        near = (pos < len(times)) & (nxt <= (idx + NEWS_BEFORE).values)
        out[f"news_{impact}"] = near
    return out


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3) -> tuple[pd.Series, pd.Series]:
    lo, hi = df["low"].rolling(n).min(), df["high"].rolling(n).max()
    k = (100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)).rolling(d).mean()
    return k, k.rolling(d).mean()


def build(symbol: str, raw: dict | None = None, live: bool = False) -> pd.DataFrame:
    df = load(symbol, raw)
    c = df["close"]
    df["ema20"], df["ema50"], df["ema200"] = ema(c, 20), ema(c, 50), ema(c, 200)
    df["rsi"] = rsi(c)
    df["rsi7"] = rsi(c, 7)
    df["stoch_k"], df["stoch_d"] = stochastic(df)
    hi14, lo14 = df["high"].rolling(14).max(), df["low"].rolling(14).min()
    df["willr"] = -100 * (hi14 - c) / (hi14 - lo14).replace(0, np.nan)
    tp = (df["high"] + df["low"] + c) / 3
    df["cci"] = (tp - tp.rolling(20).mean()) / (0.015 * tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True))
    rsi13 = rsi(c, 13)
    df["tdi_fast"], df["tdi_slow"] = rsi13.rolling(2).mean(), rsi13.rolling(7).mean()
    df["wick_low"] = (df[["open", "close"]].min(axis=1) - df["low"]) / (df["high"] - df["low"]).replace(0, np.nan)
    df["wick_high"] = (df["high"] - df[["open", "close"]].max(axis=1)) / (df["high"] - df["low"]).replace(0, np.nan)
    df["atr"] = atr(df)
    df["atr_pct"] = df["atr"] / df["atr"].rolling(288).median()  # volatilidade relativa ao dia
    df["adx"] = adx(df)
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    df["bb_up"], df["bb_dn"] = mid + 2.0 * sd, mid - 2.0 * sd
    df["bb_up25"], df["bb_dn25"] = mid + 2.5 * sd, mid - 2.5 * sd
    kc_mid = ema(c, 20)
    df["kc_up"], df["kc_dn"] = kc_mid + 1.5 * df["atr"], kc_mid - 1.5 * df["atr"]
    df["body"] = (c - df["open"]) / df["atr"]
    df["color"] = np.sign(c - df["open"])
    df["big_candle_recent"] = ((df["high"] - df["low"]) / df["atr"]).rolling(3).max() > 3  # proxy notícia
    df["trend_m15"] = higher_tf_trend(df, "15min", 20, 50)
    df["trend_h1"] = higher_tf_trend(df, "1h", 20, 50)
    df["trend_h4"] = higher_tf_trend(df, "4h", 20, 50)
    df["fib_retr"], df["fib_dir"] = fib_position(df)
    df["structure"] = structure_score(df)
    df = df.join(session_features(df.index)).join(news_flags(df.index, symbol))
    # resultado da opção de 5 min: entra no fechamento de i, expira no fechamento de i+1
    df["next_move"] = np.sign(c.shift(-1) - c)
    df["payout"] = PAYOUT_CRYPTO if symbol.endswith(CRYPTO_SUFFIX) else PAYOUT_FOREX
    df["symbol"] = symbol
    return df.dropna(subset=["ema200", "trend_h4"] if live else ["ema200", "trend_h4", "next_move"])
