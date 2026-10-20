"""Núcleo do app: cache de candles, avaliação de sinais, liquidação de resultados e persistência (SQLite).
Sem dependência de GUI — testável isolado."""
import json
import math
import sqlite3
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from features import build
from nexor import score as nexor_score
from strategies import bb25_reversal, bb_reversal, rsi7_reversal

BARS_KEPT = 3000
BAR = pd.Timedelta("5min")
REVERSAL_RULES = (("S5", rsi7_reversal), ("S2b", bb25_reversal), ("S2", bb_reversal))
# acerto fora da amostra no backtest (jun–out/2026) — referência exibida na UI
TIER_EXPECTED = {"X": 0.665, "A": 0.659, "B": 0.581, "C": 0.565}
# Exaustão Verde (cenário A): todas as cripto + vetos; BTC ficou ~53% na Hezilex — acompanhar
EXHAUSTION_SYMBOLS = ("BTCUSDT", "BNBUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LTCUSDT", "DOGEUSDT")
ALL_VETOS = ("overlap_lon_ny", "noticia_US", "noticia_FF_alta", "fim_de_hora", "fora_horario")
ENABLED_VETOS: set[str] = set(ALL_VETOS)  # a UI altera conforme configuração
TRADING_HOURS = (0, 24)  # [início, fim) em hora LOCAL da entrada; a UI altera
TIER_DESC = {"X": "exaustão verde (cripto)", "A": "vela gigante recente", "B": "H4 a favor", "C": "só vetos ok", "VETO": "não operar"}


@dataclass
class Signal:
    bar_utc: str
    symbol: str
    dir: str
    tier: str
    strategies: str
    price: float
    vetos: str
    result: str = ""
    close_after: float | None = None
    reasons: str = ""  # explicação legível, separada por " · "


class CandleCache:
    """Guarda até BARS_KEPT velas FECHADAS por ativo; aceita lotes iniciais ou incrementais."""

    def __init__(self, folder: Path | None = None):
        self._data: dict[str, dict[str, list]] = {}
        self.folder = folder
        if folder:
            folder.mkdir(parents=True, exist_ok=True)
            for f in folder.glob("*.json"):
                try:
                    self._data[f.stem] = json.loads(f.read_text())
                except ValueError:
                    pass

    def save(self, symbol: str) -> None:
        """Persiste para não baixar o histórico de novo ao reabrir o app."""
        if self.folder and symbol in self._data:
            (self.folder / f"{symbol}.json").write_text(json.dumps(self._data[symbol]))

    def bars_needed(self, symbol: str, now_ts: int) -> int:
        """Quantas velas pedir: só a diferença desde a última salva (+ folga), ou o histórico todo."""
        if not self.has(symbol):
            return BARS_KEPT
        gap = (now_ts - self._data[symbol]["t"][-1]) // 300 + 3
        return BARS_KEPT if gap > BARS_KEPT // 2 else max(12, int(gap))

    def has_any(self) -> bool:
        return any(len(v["t"]) for v in self._data.values())

    def has(self, symbol: str) -> bool:
        return symbol in self._data and len(self._data[symbol]["t"]) > 0

    def merge(self, symbol: str, raw: dict, now_ts: int) -> None:
        cur = self._data.setdefault(symbol, {k: [] for k in "tohlc"})
        index = {t: i for i, t in enumerate(cur["t"])}
        for i, t in enumerate(raw["t"]):
            if t + 300 > now_ts:  # vela ainda em formação
                continue
            if t in index:
                for k in "ohlc":
                    cur[k][index[t]] = raw[k][i]
            elif not cur["t"] or t > cur["t"][-1]:
                for k in "tohlc":
                    cur[k].append(raw[k][i])
        for k in "tohlc":
            cur[k] = cur[k][-BARS_KEPT:]

    def get(self, symbol: str) -> dict:
        return self._data[symbol]

    @staticmethod
    def forming(raw: dict, now_ts: int) -> dict | None:
        """Última vela do lote se ainda estiver aberta."""
        if not raw["t"] or raw["t"][-1] + 300 <= now_ts:
            return None
        return {k: raw[k][-1] for k in "tohlc"}


def green_exhaustion(df: pd.DataFrame) -> bool:
    """3ª+ vela verde seguida com corpo > 2×ATR → PUT (validado 1 ano Binance: 61%, c/ vetos 66%)."""
    color = (df["close"] - df["open"]).iloc[-3:]
    r = df.iloc[-1]
    return bool((color > 0).all() and (r["close"] - r["open"]) > 2 * r["atr"])


def evaluate(symbol: str, df: pd.DataFrame) -> Signal | None:
    """Sinal da ÚLTIMA vela fechada: exaustão verde (cripto) ou reversão (S5/S2b/S2 concordando) + vetos + nível."""
    exhaustion = symbol in EXHAUSTION_SYMBOLS and green_exhaustion(df)
    hits = {name: int(rule(df).iloc[-1]) for name, rule in REVERSAL_RULES}
    if exhaustion:
        hits = {"EXV": -1}
    dirs = {d for d in hits.values() if d}
    if len(dirs) != 1:
        return None
    d = dirs.pop()
    r = df.iloc[-1]
    entry_local = (df.index[-1] + BAR).tz_convert(datetime.now().astimezone().tzinfo)
    start, end = TRADING_HOURS
    outside = not (start <= entry_local.hour < end) if start < end else not (entry_local.hour >= start or entry_local.hour < end)
    vetos = [name for name, on in (
        ("overlap_lon_ny", r["overlap_lon_ny"]),
        ("noticia_US", r["ny_news_window"] or r["fomc_window"]),
        ("noticia_FF_alta", r["news_high"]),
        ("fim_de_hora", r["end_of_hour"]),
        ("fora_horario", outside),
    ) if on and name in ENABLED_VETOS]
    tier = "VETO" if vetos else "X" if exhaustion else "A" if r["big_candle_recent"] else "B" if r["trend_h4"] == d else "C"
    return Signal(df.index[-1].isoformat(), symbol, "CALL" if d > 0 else "PUT", tier,
                  "+".join(k for k, v in hits.items() if v), float(r["close"]), "|".join(vetos),
                  reasons=explain(df, d))


def explain(df: pd.DataFrame, d: int) -> str:
    """Por que o sinal saiu, em texto curto (estilo do placar Nexor, mas para a nossa reversão)."""
    r = df.iloc[-1]
    if d < 0 and green_exhaustion(df):
        return (f"3+ verdes seguidas, última com corpo {(r['close'] - r['open']) / r['atr']:.1f}×ATR — exaustão de alta"
                f" · RSI7 {r['rsi7']:.0f} · validado 1 ano: ~61–66%")
    side = "sobrevendido" if d > 0 else "sobrecomprado"
    band = "abaixo" if d > 0 else "acima"
    out = [f"RSI7 {r['rsi7']:.0f} / RSI14 {r['rsi']:.0f} — {side}"]
    if (d > 0 and r["close"] < r["bb_dn25"]) or (d < 0 and r["close"] > r["bb_up25"]):
        out.append(f"fechou {band} da Bollinger 2,5")
    elif (d > 0 and r["close"] < r["bb_dn"]) or (d < 0 and r["close"] > r["bb_up"]):
        out.append(f"fechou {band} da Bollinger 2")
    out.append(f"ADX {r['adx']:.0f} — {'sem tendência forte' if r['adx'] < 25 else 'tendência'}")
    if r["big_candle_recent"]:
        out.append("vela gigante recente (exaustão)")
    out.append(f"H4 {'a favor' if r['trend_h4'] == d else 'contra'}")
    try:
        nx = int(nexor_score(df.iloc[-400:])["total"].iloc[-1])
        out.append(f"placar momentum {nx:+d}/11")
    except Exception:
        pass
    return " · ".join(out)


def analyze(symbol: str, raw: dict) -> tuple[Signal | None, pd.Series]:
    df = build(symbol, raw, live=True)
    return evaluate(symbol, df), df["close"]


def analyze_preview(symbol: str, closed: dict, forming: dict) -> Signal | None:
    """Avalia a vela EM FORMAÇÃO (prévia). Não grava nada: o sinal pode sumir até o fechamento."""
    raw = {k: closed[k] + [forming[k]] for k in "tohlc"}
    df = build(symbol, raw, live=True)
    return evaluate(symbol, df)


class StakePlan:
    """Gestão do usuário: banca 1000 → entrada 10 (1%) e stop diário 50 (5%);
    +10% na entrada a cada +15% de banca (volta o degrau se a banca cair)."""

    def __init__(self, base_bank: float, stake_pct: float = 0.01, stop_pct: float = 0.05,
                 step_gain: float = 0.15, step_raise: float = 0.10):
        self.base_bank, self.stake_pct, self.stop_pct = base_bank, stake_pct, stop_pct
        self.step_gain, self.step_raise = step_gain, step_raise

    def level(self, bank: float) -> int:
        if bank <= self.base_bank:
            return 0
        return int(math.floor(math.log(bank / self.base_bank) / math.log(1 + self.step_gain)))

    def stake(self, bank: float) -> float:
        return round(self.base_bank * self.stake_pct * (1 + self.step_raise) ** self.level(bank), 2)

    def daily_stop(self) -> float:
        return round(self.base_bank * self.stop_pct, 2)

    def can_trade(self, day_pnl: float, bank: float) -> bool:
        return day_pnl > -self.daily_stop() and bank >= self.stake(bank)


class Store:
    """SQLite thread-safe (uma conexão, lock). Inserção idempotente por (bar_utc, symbol)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("""CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY, bar_utc TEXT NOT NULL, symbol TEXT NOT NULL, dir TEXT NOT NULL,
                tier TEXT NOT NULL, strategies TEXT, price REAL, vetos TEXT, result TEXT DEFAULT '',
                close_after REAL, created_at TEXT NOT NULL, UNIQUE(bar_utc, symbol))""")
            cols = {row[1] for row in self._db.execute("PRAGMA table_info(signals)")}
            if "reasons" not in cols:
                self._db.execute("ALTER TABLE signals ADD COLUMN reasons TEXT DEFAULT ''")
            self._db.commit()

    def insert(self, s: Signal) -> bool:
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO signals (bar_utc,symbol,dir,tier,strategies,price,vetos,reasons,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (s.bar_utc, s.symbol, s.dir, s.tier, s.strategies, s.price, s.vetos, s.reasons,
                 datetime.now(timezone.utc).isoformat()))
            self._db.commit()
            return cur.rowcount == 1

    def settle(self, symbol: str, closes: pd.Series) -> list[Signal]:
        """Liquida sinais pendentes cuja vela seguinte já fechou. Empate = TIE (devolução)."""
        settled = []
        with self._lock:
            rows = self._db.execute(
                "SELECT id,bar_utc,dir,price,tier,strategies,vetos FROM signals WHERE symbol=? AND result=''",
                (symbol,)).fetchall()
            for sid, bar, d, price, tier, strat, vetos in rows:
                nxt = pd.Timestamp(bar) + BAR
                if nxt not in closes.index:
                    continue
                after = float(closes[nxt])
                sign = 1 if d == "CALL" else -1
                res = "TIE" if after == price else "WIN" if (after - price) * sign > 0 else "LOSS"
                self._db.execute("UPDATE signals SET result=?, close_after=? WHERE id=?", (res, after, sid))
                settled.append(Signal(bar, symbol, d, tier, strat, price, vetos, res, after))
            self._db.commit()
        return settled

    def frame(self) -> pd.DataFrame:
        with self._lock:
            return pd.read_sql("SELECT * FROM signals ORDER BY bar_utc DESC", self._db)


def winrate_table(df: pd.DataFrame, by: str) -> pd.DataFrame:
    done = df[df["result"].isin(["WIN", "LOSS"])]
    if done.empty:
        return pd.DataFrame(columns=[by, "n", "acerto"])
    g = done.groupby(by)["result"].agg(n="size", acerto=lambda s: (s == "WIN").mean())
    return g.reset_index().sort_values("n", ascending=False)


__all__ = ["Signal", "CandleCache", "Store", "analyze", "analyze_preview", "winrate_table", "StakePlan", "TIER_EXPECTED", "TIER_DESC", "asdict"]
