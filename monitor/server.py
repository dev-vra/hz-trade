"""HZ Cripto Monitor — monitoramento e análise ao vivo (sem corretora, sem robô).
Dados: websocket público da Binance (M5). Sinais: modelo IA 15 min, rompimento falso de S/R 24h, exaustão verde.
Rodar: python -X utf8 monitor/server.py   (senha em monitor/.env: MONITOR_PASSWORD=...)"""
import asyncio
import hashlib
import hmac
import json
import re
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import lightgbm as lgb
import numpy as np
import pandas as pd
import uvicorn
import websockets
from fastapi import FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "src"))
from features import DATA, build  # noqa: E402
from mine import frame  # noqa: E402
from train import extra_feats  # noqa: E402

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LTCUSDT", "BNBUSDT", "DOGEUSDT"]
REST = "https://api.binance.com/api/v3/klines?symbol={}&interval=5m&limit=1000"
STREAM = "wss://stream.binance.com:443/stream?streams=" + "/".join(f"{s.lower()}@kline_5m" for s in SYMBOLS)
BAR = 300
STATIC = HERE / "static"
DB = HERE / "sinais.db"

# corte de força e janelas
IA_FORTE, IA_CONFIRMA = 0.20, 0.15
PRE_ATR = 0.5
HEAT_N = 12
# exaustão verde: acerto por ativo (Hezilex 120d / Binance teste) — regra própria, não usa a força da IA
# backtest 3 anos sem veto: só XRP/DOGE/SOL ficam claramente acima do breakeven (54,9%)
EXV_WR = {"XRPUSDT": .62, "DOGEUSDT": .61, "SOLUSDT": .57}  # expiração 15 min
EXV_WR_DEF = .56


def env() -> dict:
    p = HERE / ".env"
    out = dict(os.environ)
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.split("=", 1)
                out.setdefault(k.strip(), v.strip())
    return out


ENV = env()
PASSWORD = ENV.get("MONITOR_PASSWORD", "")
SECRET_FILE = HERE / ".secret"
if not SECRET_FILE.exists():
    SECRET_FILE.write_text(secrets.token_hex(32))
SECRET = SECRET_FILE.read_text().strip().encode()
COOKIE, COOKIE_DAYS = "hzm", 30

BOOSTER = lgb.Booster(model_file=str(DATA / "modelo15.txt"))
FEATS = json.loads((DATA / "modelo15_feats.json").read_text())
CALIB = pd.read_csv(DATA.parent / "replica" / "modelo15_calib.csv")


def calib_wr(conf: float) -> float | None:
    """Acerto histórico (teste) da faixa de força >= conf."""
    k = CALIB[CALIB["conf_min"] <= conf + 1e-9]
    return None if k.empty else float(k.iloc[-1]["acerto"])


# ---------------------------------------------------------------- estado
candles: dict[str, list[list[float]]] = {s: [] for s in SYMBOLS}   # fechadas [t,o,h,l,c,v] (t em s)
live: dict[str, list[float] | None] = {s: None for s in SYMBOLS}
analysis: dict[str, dict] = {}
pre: dict[str, dict] = {}
clients: set[WebSocket] = set()


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.execute("""create table if not exists sinais(id integer primary key, t integer, sym text, estrategia text,
                 dir integer, forca real, wr_hist real, entrada real, expira integer, saida real, resultado text)""")
    if "motivos" not in [r[1] for r in c.execute("pragma table_info(sinais)")]:
        c.execute("alter table sinais add column motivos text")
    return c


async def broadcast(msg: dict) -> None:
    data = json.dumps(msg, default=float)
    for ws in list(clients):
        try:
            await ws.send_text(data)
        except Exception:
            clients.discard(ws)


def raw_of(sym: str) -> dict:
    a = np.array(candles[sym], dtype=float)
    return {"t": a[:, 0].astype(int).tolist(), **{k: a[:, i + 1].tolist() for i, k in enumerate("ohlcv")}}


# ---------------------------------------------------------------- análise no fechamento
def analyze(sym: str, btc: pd.DataFrame | None) -> dict:
    raw = raw_of(sym)
    f = frame(sym, "5min", btc, raw=raw, live=True)
    extra_feats(f, raw)
    f["cores3"] = f["cores3"].astype("category")
    f["sym"] = pd.Categorical([sym] * len(f))
    for col in FEATS:  # BTC não tem btc_lead/btc_corpo (no treino ficaram NaN)
        if col not in f:
            f[col] = np.nan
    last = f.iloc[-HEAT_N:]
    ps = BOOSTER.predict(last[FEATS])
    row = f.iloc[[-1]]
    p = float(ps[-1])
    conf = abs(p - .5) * 2
    ia = 1 if p > .5 else -1
    heat = [[int(ts.timestamp()), round((q - .5) * 2, 4)] for ts, q in zip(last.index, ps)]
    contrib = BOOSTER.predict(row[FEATS], pred_contrib=True)[0][:-1]
    df = build(sym, raw, live=True)
    r = df.iloc[-1]
    atr = float(r["atr"])
    hi24 = max(x[2] for x in candles[sym][-289:-1])
    lo24 = min(x[3] for x in candles[sym][-289:-1])
    cor = np.sign(df["close"] - df["open"]).iloc[-3:]
    exaustao = bool((cor > 0).all() and (r["close"] - r["open"]) > 2 * atr)
    vol_alto = int(row["volume"].iloc[0]) >= 3 if pd.notna(row["volume"].iloc[0]) else False
    furou_sup = float(row["d_sup24h"].iloc[0]) < 0 and vol_alto
    furou_res = float(row["d_res24h"].iloc[0]) < 0 and vol_alto
    sinais = []
    if conf >= IA_FORTE:
        sinais.append(("IA forte", ia, 3))
    if furou_sup and ia == 1 and conf >= IA_CONFIRMA:
        sinais.append(("Rompimento falso (suporte)", 1, 3))
    if furou_res and ia == -1 and conf >= IA_CONFIRMA:
        sinais.append(("Rompimento falso (resistência)", -1, 3))
    if exaustao and sym in EXV_WR and not int(row["vetos"].iloc[0]):
        sinais.append(("Exaustão verde", -1, 3))
    t = int(raw["t"][-1])
    motivos = explain(row.iloc[0], r, contrib, ia)
    return dict(heat=heat, motivos=motivos, sym=sym, t=t, preco=float(r["close"]), p_alta=round(p, 4), forca=round(conf, 4), dir=ia,
                wr_hist=calib_wr(conf), atr=atr, res24=hi24, sup24=lo24, vetos=int(row["vetos"].iloc[0]),
                rsi=round(float(r["rsi"]), 1), sinais=sinais)


def explain(row: pd.Series, r: pd.Series, contrib: np.ndarray, ia: int) -> list[dict]:
    """Top fatores do modelo (contribuição SHAP do LightGBM) a favor da direção lida, em linguagem humana."""
    rng = r["high"] - r["low"]

    def sr(d: float, nome: str) -> str:
        return f"rompeu {nome} ({-d:.1f} ATR além)" if d < 0 else f"{d:.1f} ATR da {nome}"

    pb = (r["close"] - r["bb_dn"]) / (r["bb_up"] - r["bb_dn"]) if r["bb_up"] != r["bb_dn"] else .5
    txt = {
        "rsi": f"RSI 14 em {r['rsi']:.0f}", "rsi7": f"RSI 7 em {r['rsi7']:.0f}",
        "stoch": f"Estocástico em {r['stoch_k']:.0f}", "bb": f"posição nas Bandas {pb:.0%}",
        "hora": f"horário {int(row['hora']):02d}h", "min": f"minuto :{int(row['min']):02d}",
        "dow": ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"][int(row["dow"])],
        "cores3": "últimas 3 velas " + "".join({"1": "🟢", "-1": "🔴", "0": "⚪"}[x] for x in re.findall(r"-?\d", str(row["cores3"]))),
        "ret3": f"variação 15 min {row['ret3']:+.2f}%", "ret12": f"variação 1 h {row['ret12']:+.2f}%",
        "d_sup4h": sr(row["d_sup4h"], "suporte 4h"), "d_res4h": sr(row["d_res4h"], "resistência 4h"),
        "d_sup24h": sr(row["d_sup24h"], "suporte 24h"), "d_res24h": sr(row["d_res24h"], "resistência 24h"),
        "toq_sup24h": f"suporte 24h tocado {row['toq_sup24h']:.0f}×", "toq_res24h": f"resistência 24h tocada {row['toq_res24h']:.0f}×",
        "toq_sup4h": f"suporte 4h tocado {row['toq_sup4h']:.0f}×", "toq_res4h": f"resistência 4h tocada {row['toq_res4h']:.0f}×",
        "volat": f"volatilidade (ATR) {r['atr_pct']:.2f}%", "volume": "volume " + ["muito baixo", "baixo", "normal", "alto", "muito alto"][int(row["volume"])] if pd.notna(row["volume"]) else "volume",
        "ema": "tendência das EMAs " + ("de alta" if row["ema"] > 0 else "de baixa" if row["ema"] < 0 else "neutra"),
        "h4": "tendência 4h " + ("de alta" if row["h4"] > 0 else "de baixa" if row["h4"] < 0 else "neutra"),
        "corpo": f"corpo da vela {abs(r['close'] - r['open']) / r['atr']:.1f} ATR",
        "pavio_sup": f"pavio superior {(r['high'] - max(r['open'], r['close'])) / rng:.0%}" if rng else "pavio superior",
        "pavio_inf": f"pavio inferior {(min(r['open'], r['close']) - r['low']) / rng:.0%}" if rng else "pavio inferior",
        "btc_corpo": "movimento do BTC na vela", "btc_lead": "alt atrasada em relação ao BTC",
        "seq": f"sequência de {abs(int(row['seq']))} velas", "gigante": "vela gigante recente",
        "nossa_rev": "padrão de reversão", "vetos": "janela de notícia/horário", "sym": "perfil do ativo",
    }
    order = np.argsort(-contrib * ia)
    out = []
    for i in order[:4]:
        if contrib[i] * ia <= 0:
            break
        out.append(dict(f=FEATS[i], txt=txt.get(FEATS[i], FEATS[i]), peso=round(float(abs(contrib[i])), 3)))
    return out


def settle(sym: str) -> list[dict]:
    """Fecha sinais vencidos usando o fechamento da vela de expiração."""
    closes = {int(x[0]): x[4] for x in candles[sym][-50:]}
    out = []
    with db() as c:
        rows = c.execute("select id,dir,entrada,expira from sinais where sym=? and resultado is null", (sym,)).fetchall()
        for i, d, entrada, exp in rows:
            bar = exp - BAR  # vela que fecha no instante da expiração
            if bar not in closes:
                continue
            saida = closes[bar]
            res = "EMPATE" if saida == entrada else ("WIN" if np.sign(saida - entrada) == d else "LOSS")
            c.execute("update sinais set saida=?, resultado=? where id=?", (saida, res, i))
            out.append(dict(id=i, sym=sym, resultado=res, saida=saida))
    return out


async def on_bar_closed(t: int) -> None:
    await asyncio.sleep(1.5)  # espera as outras moedas fecharem a mesma vela
    btc = None
    if candles["BTCUSDT"] and candles["BTCUSDT"][-1][0] == t:
        b = await asyncio.to_thread(build, "BTCUSDT", raw_of("BTCUSDT"), True)
        btc = pd.DataFrame({"move_atr": (b["close"] - b["open"]) / b["atr"]})
    for sym in SYMBOLS:
        if not candles[sym] or candles[sym][-1][0] != t:
            continue
        try:
            a = await asyncio.to_thread(analyze, sym, None if sym == "BTCUSDT" else btc)
        except Exception as e:  # noqa: BLE001
            print("analise", sym, e, flush=True)
            continue
        analysis[sym] = a
        pre.pop(sym, None)
        for r in settle(sym):
            await broadcast({"type": "resultado", **r})
        for nome, d, h in a["sinais"]:
            exv = nome.startswith("Exaust")
            forca, wr = (None, EXV_WR.get(sym, EXV_WR_DEF)) if exv else (a["forca"], a["wr_hist"])
            with db() as c:
                if c.execute("select 1 from sinais where t=? and sym=? and estrategia=?", (t, sym, nome)).fetchone():
                    continue  # mesma vela já registrada (ex.: reinício do servidor)
                cur = c.execute("insert into sinais(t,sym,estrategia,dir,forca,wr_hist,entrada,expira,motivos) "
                                "values(?,?,?,?,?,?,?,?,?)", (t, sym, nome, d, forca, wr, a["preco"],
                                                             t + BAR + h * BAR, json.dumps(a["motivos"], ensure_ascii=False)))
            await broadcast({"type": "sinal", "id": cur.lastrowid, "sym": sym, "estrategia": nome, "dir": d,
                             "forca": forca, "wr_hist": wr, "entrada": a["preco"],
                             "expira": t + BAR + h * BAR, "t": t, "min": h * 5, "motivos": a["motivos"]})
        await broadcast({"type": "analise", **{k: v for k, v in a.items() if k != "sinais"}})


# ---------------------------------------------------------------- pré-sinal durante a vela
def presignal(sym: str, k: list[float]) -> dict | None:
    a = analysis.get(sym)
    if not a or not a["atr"]:
        return None
    price, atr = k[4], a["atr"]
    if price <= a["sup24"] + PRE_ATR * atr:
        return dict(sym=sym, tipo="Perto/abaixo do suporte 24h", dir=1, nivel=a["sup24"])
    if price >= a["res24"] - PRE_ATR * atr:
        return dict(sym=sym, tipo="Perto/acima da resistência 24h", dir=-1, nivel=a["res24"])
    last = candles[sym][-2:]
    if len(last) == 2 and all(x[4] > x[1] for x in last) and (k[4] - k[1]) > 1.5 * atr:
        return dict(sym=sym, tipo="Exaustão verde se formando", dir=-1, nivel=None)
    return None


async def stream() -> None:
    last_tick: dict[str, float] = {}
    while True:
        try:
            async with websockets.connect(STREAM, ping_interval=20) as ws:
                print("stream conectado", flush=True)
                async for msg in ws:
                    k = json.loads(msg)["data"]["k"]
                    sym = k["s"]
                    bar = [k["t"] // 1000, float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"]), float(k["v"])]
                    if k["x"]:
                        if not candles[sym] or candles[sym][-1][0] < bar[0]:
                            candles[sym].append(bar)
                            del candles[sym][:-1100]
                        live[sym] = None
                        if sym == "BTCUSDT":  # todas fecham juntas; on_bar_closed espera as demais
                            asyncio.create_task(on_bar_closed(bar[0]))
                        continue
                    live[sym] = bar
                    now = time.time()
                    if now - last_tick.get(sym, 0) >= 1:
                        last_tick[sym] = now
                        await broadcast({"type": "tick", "sym": sym, "bar": bar})
                        ps = presignal(sym, bar)
                        if ps and pre.get(sym, {}).get("tipo") != ps["tipo"]:
                            pre[sym] = ps
                            await broadcast({"type": "pre", **ps})
                        elif not ps and sym in pre:
                            pre.pop(sym)
                            await broadcast({"type": "pre_off", "sym": sym})
        except Exception as e:  # noqa: BLE001
            print("stream caiu:", e, "— reconectando", flush=True)
            await asyncio.sleep(3)


async def bootstrap() -> None:
    async with httpx.AsyncClient(timeout=30) as cl:
        for s in SYMBOLS:
            rows = (await cl.get(REST.format(s))).json()
            candles[s] = [[r[0] // 1000, *map(float, r[1:6])] for r in rows[:-1]]  # última ainda aberta
    t = candles["BTCUSDT"][-1][0]
    await on_bar_closed(t)
    print("histórico carregado, análise inicial pronta", flush=True)


# ---------------------------------------------------------------- web
app = FastAPI(docs_url=None, redoc_url=None)
PUBLIC = {"/login", "/manifest.webmanifest", "/sw.js", "/icon.svg", "/style.css"}


def sign(exp: int) -> str:
    return f"{exp}.{hmac.new(SECRET, str(exp).encode(), hashlib.sha256).hexdigest()}"


def authed(token: str | None) -> bool:
    if not PASSWORD:
        return True
    try:
        exp, _ = token.split(".", 1)
        return hmac.compare_digest(token, sign(int(exp))) and int(exp) > time.time()
    except Exception:  # noqa: BLE001
        return False


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path not in PUBLIC and not authed(request.cookies.get(COOKIE)):
        if request.url.path.startswith("/api"):
            return JSONResponse({"erro": "login"}, status_code=401)
        return RedirectResponse("/login")
    return await call_next(request)


@app.get("/login")
async def login_page():
    return FileResponse(STATIC / "login.html")


@app.post("/login")
async def login(senha: str = Form(...)):
    if not PASSWORD or not hmac.compare_digest(senha, PASSWORD):
        await asyncio.sleep(1)
        return RedirectResponse("/login?erro=1", status_code=303)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, sign(int(time.time()) + COOKIE_DAYS * 86400), max_age=COOKIE_DAYS * 86400,
                 httponly=True, samesite="lax", secure=ENV.get("HTTPS") == "1")
    return r


@app.get("/logout")
async def logout():
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(COOKIE)
    return r


@app.get("/api/state")
async def state():
    return {"symbols": SYMBOLS, "analise": analysis, "pre": pre,
            "calib": CALIB.to_dict("records"), "historico": history(80), "stats": stats()}


@app.get("/api/candles/{sym}")
async def get_candles(sym: str):
    if sym not in candles:
        return JSONResponse({"erro": "símbolo"}, status_code=404)
    rows = candles[sym][-500:] + ([live[sym]] if live[sym] else [])
    return rows


def history(n: int) -> list[dict]:
    with db() as c:
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute("select * from sinais order by id desc limit ?", (n,))]
    for r in rows:
        r["motivos"] = json.loads(r["motivos"]) if r.get("motivos") else []
        r["min"] = (r["expira"] - r["t"] - BAR) // 60
    return rows


def stats() -> list[dict]:
    with db() as c:
        rows = c.execute("""select estrategia, sum(resultado='WIN'), sum(resultado='LOSS'), sum(resultado is null)
                            from sinais group by estrategia""").fetchall()
    return [dict(estrategia=e, win=w or 0, loss=l or 0, aberto=a or 0) for e, w, l, a in rows]


@app.get("/api/perf")
async def perf():
    """Sinais encerrados — o front calcula curva de banca e quebras por estratégia/hora/ativo."""
    with db() as c:
        rows = c.execute("select t,sym,estrategia,dir,forca,resultado from sinais "
                         "where resultado in ('WIN','LOSS') order by t").fetchall()
    return [dict(t=t, sym=s, estrategia=e, dir=d, forca=f, resultado=r) for t, s, e, d, f, r in rows]


NEWS_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
news_cache: dict = {"t": 0, "rows": []}


async def load_news() -> list[dict]:
    """Calendário econômico (ForexFactory, público). Cripto reage a USD: mostra USD alto/médio + outros alto."""
    if time.time() - news_cache["t"] < 6 * 3600 and news_cache["rows"]:
        return news_cache["rows"]
    rows = []
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            for ev in (await cl.get(NEWS_URL)).json():
                imp = ev.get("impact", "").lower()
                if imp not in ("high", "medium") or (ev.get("country") != "USD" and imp != "high"):
                    continue
                ts = int(pd.Timestamp(ev["date"]).timestamp())
                rows.append(dict(t=ts, moeda=ev.get("country"), impacto=imp, titulo=ev.get("title", ""),
                                 previsto=ev.get("forecast", ""), anterior=ev.get("previous", "")))
    except Exception as e:  # noqa: BLE001
        print("noticias:", e, flush=True)
    if not rows:  # fallback: calendário que o HZ Sinais já usa
        f = DATA / "_news.json"
        if f.exists():
            rows = [dict(t=t, moeda=m, impacto=i, titulo=n or "", previsto="", anterior="")
                    for t, m, i, n in json.loads(f.read_text()) if m == "USD" or i == "high"]
    news_cache.update(t=time.time(), rows=sorted(rows, key=lambda r: r["t"]))
    return news_cache["rows"]


@app.get("/api/news")
async def news():
    now = time.time()
    return [r for r in await load_news() if now - 3 * 3600 <= r["t"] <= now + 3 * 86400]


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not authed(ws.cookies.get(COOKIE)):
        await ws.close(code=4401)
        return
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        clients.discard(ws)


@app.get("/{path:path}")
async def static_files(path: str):
    f = (STATIC / (path or "index.html")).resolve()
    if not str(f).startswith(str(STATIC.resolve())) or not f.is_file():
        f = STATIC / "index.html"
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


@app.on_event("startup")
async def startup():
    if not PASSWORD:
        print("AVISO: sem MONITOR_PASSWORD em monitor/.env — acesso sem login (só use local)", flush=True)
    db().close()
    await bootstrap()
    asyncio.create_task(stream())


if __name__ == "__main__":
    uvicorn.run(app, host=ENV.get("HOST", "127.0.0.1"), port=int(ENV.get("PORT", 8787)), log_level="warning")
