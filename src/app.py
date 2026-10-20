"""HZ Sinais — app Windows de monitoramento M5 para a Hezilex (UI Fluent / Windows 11).

Página "Operar": plataforma embutida (você opera manualmente nela) + painel de sinais:
⏳ PRÉVIO (setup se formando no fim da vela) → ✅ CONFIRMADO no fechamento → WIN/LOSS.
Grava tudo em SQLite e calcula estatísticas. NÃO executa ordens.
"""
import itertools
import json
import shutil
import sys
import winsound
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Qt, Signal as QtSignal
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QApplication, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QSplitter, QSystemTrayIcon, QTableWidgetItem, QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, CardWidget, CheckBox, ComboBox, CompactDoubleSpinBox,
                            CompactSpinBox, FluentIcon,
                            FluentWindow, InfoBar, InfoBarPosition, NavigationItemPosition, PlainTextEdit,
                            PrimaryPushButton, PushButton, SimpleCardWidget, SmoothScrollArea,
                            StrongBodyLabel, SubtitleLabel, SwitchButton, TableWidget, Theme, TitleLabel,
                            setTheme, setThemeColor)

FROZEN = getattr(sys, "frozen", False)
BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
USER_DATA = APP_DIR / "dados"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features  # noqa: E402
import engine  # noqa: E402
from engine import (ALL_VETOS, TIER_DESC, TIER_EXPECTED, CandleCache, Signal, StakePlan, Store, analyze,  # noqa: E402
                    analyze_preview, winrate_table)

features.DATA = USER_DATA  # _news.json do usuário fica em dados/

TRADEROOM = "https://app.hezilex.com/traderoom"
BACKTESTED = ["EURUSD", "GBPUSD", "USDJPY", "AUDJPY", "EURJPY", "EURGBP", "AUDCAD", "USDCAD", "NZDUSD",
              "CADJPY", "CHFJPY", "GBPJPY", "USDCHF", "EURAUD", "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
EXTRA = ["EURNZD", "AUDCHF", "GBPCHF", "GBPAUD", "NZDJPY", "EURCHF", "CADCHF", "EURCAD",
         "LTCUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT"]
ALL_SYMBOLS = BACKTESTED + EXTRA
WEAK = {"EURJPY", "BTCUSDT", "USDJPY", "CHFJPY"}  # piores no backtest: desmarcados por padrão
VETO_LABELS = {"overlap_lon_ny": "Overlap Londres/NY", "noticia_US": "Janela notícia EUA (08:30 ET / FOMC)",
               "noticia_FF_alta": "Notícia alto impacto (ForexFactory)", "fim_de_hora": "Fim de hora (:50 e :55)",
               "fora_horario": "Fora do horário de operação"}
DEFAULT_CONFIG = {"symbols": [s for s in BACKTESTED if s not in WEAK], "vetos": list(ALL_VETOS),
                  "payout": 0.84, "stake": 10.0, "preview_s": 90, "sound": True, "show_c": False,
                  "hour_start": 0, "hour_end": 24, "stop_win": 0.0, "stop_loss": 0.0, "max_losses": 0, "max_wins": 0,
                  "auto_demo": False, "demo_account": 0, "bank_base": 1000.0}
BAR_S = 300
DELAY_AFTER_CLOSE_MS = 4_000
PREVIEW_EVERY_MS = 15_000
POLL_MS = 400
FETCH_TIMEOUT_S = 120
FIRST_LOAD_TIMEOUT_S = 300  # carga inicial (3000 velas/ativo) é lenta no servidor da Hezilex
SIDEBAR_WIDTH = 400
MAX_CARDS = 40

ACCENT = "#3b82f6"
GREEN, RED, AMBER, BLUE, GREY = "#22c55e", "#ef4444", "#f59e0b", "#3b82f6", "#71717a"
TIER_COLORS = {"X": "#7c3aed", "A": GREEN, "B": BLUE, "C": GREY, "VETO": "#a16207"}
RESULT_COLORS = {"WIN": GREEN, "LOSS": RED, "TIE": AMBER}

# Roda na página logada. Devolve velas cruas (inclusive a em formação) em window.__hz[id].
# Janela > ~20 dias = HTTP 500; concorrência 4 + 1 retry (carga inicial em paralelo total derruba o servidor).
FETCH_JS = """
(() => {
  const id = %d, plan = %s, now = Math.floor(Date.now()/1000), out = {}, queue = Object.keys(plan);
  (window.__hz = window.__hz || {})[id] = null;
  const prog = (window.__hzp = window.__hzp || {})[id] = {now, data: out, err: {}, done: 0, total: queue.length};
  let loginError = false;
  async function one(s) {
    const n = plan[s], from = now - Math.min(300 * n * 3, 86400 * 18);
    const r = await fetch(`/publicapi/tradingview/udf-history?symbol=${s}&resolution=5&from=${from}&to=${now}&countback=${n}&site=app.hezilex.com`);
    if (r.status === 401) { loginError = true; return; }
    if (!r.ok) { prog.err[s] = 'HTTP ' + r.status; return; }
    const j = await r.json();
    if (j.s === 'ok') out[s] = {t: j.t, o: j.o, h: j.h, l: j.l, c: j.c};
  }
  const worker = async () => { for (let s; (s = queue.shift());) {
    for (let a = 0; a < 2 && !out[s] && !loginError; a++) {
      try { await one(s); } catch (e) { prog.err[s] = String(e).slice(0, 80); }
    }
    prog.done++;
  } };
  Promise.all([0, 1, 2, 3].map(worker)).then(() => { window.__hz[id] = JSON.stringify({now, loginError, data: out}); });
  return true;
})();
"""


# Desenha marcas no gráfico TradingView da Hezilex (iframe tradingview_*) e um banner com sinais de
# outros ativos. Só visual/local: não interage com ordens. Marcas do ativo aberto são redesenhadas
# quando você troca de ativo (o gráfico limpa as formas ao mudar de símbolo).
CHART_SYNC_JS = """
(() => {
  const marks = %s;
  const st = window.__hzChart = window.__hzChart || {sym: null, drawn: {}};
  let banner = document.getElementById('hz-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'hz-banner';
    banner.style.cssText = 'position:fixed;top:64px;left:50%%;transform:translateX(-50%%);z-index:99999;' +
      'display:flex;gap:8px;pointer-events:none;font:600 13px Segoe UI,sans-serif';
    document.body.appendChild(banner);
  }
  const f = document.querySelector('iframe[id^="tradingview"]');
  const api = f && f.contentWindow && f.contentWindow.tradingViewApi;
  let cur = null;
  try { cur = api && api.activeChart().symbol(); } catch (e) {}
  const live = marks.filter(m => m.kind !== 'result' && m.sym !== cur);
  banner.innerHTML = live.map(m => `<div style="background:${m.kind === 'preview' ? '#b45309' : (m.dir > 0 ? '#15803d' : '#b91c1c')};` +
    `color:#fff;padding:6px 12px;border-radius:8px;box-shadow:0 4px 14px rgba(0,0,0,.4)">` +
    `${m.kind === 'preview' ? '⏳' : '✅'} ${m.sym} ${m.dir > 0 ? '▲ CALL' : '▼ PUT'} · ${m.tier}</div>`).join('');
  if (!cur) return 'sem gráfico';
  const ch = api.activeChart();
  if (st.sym !== cur) { st.sym = cur; st.drawn = {}; }
  const want = new Set();
  for (const m of marks.filter(m => m.sym === cur)) {
    const key = m.kind + m.time; want.add(key);
    if (st.drawn[key]) continue;
    const color = m.kind === 'preview' ? '#f59e0b' : m.kind === 'result' ? (m.text.startsWith('WIN') ? '#22c55e' : '#ef4444')
      : (m.dir > 0 ? '#22c55e' : '#ef4444');
    const shape = m.kind === 'result' ? 'text' : (m.dir > 0 ? 'arrow_up' : 'arrow_down');
    try {
      st.drawn[key] = ch.createShape({time: m.time, price: m.price}, {shape, text: m.text, lock: true,
        disableSelection: true, disableSave: true, overrides: {color, fontsize: 12}});
    } catch (e) {}
  }
  for (const [key, id] of Object.entries(st.drawn)) {   // prévio cancelado/confirmado: remove a marca antiga
    if (!want.has(key)) { try { Promise.resolve(id).then(x => ch.removeEntity(x)); } catch (e) {} delete st.drawn[key]; }
  }
  return 'ok ' + cur;
})();
"""


# ---------------- configuração ----------------
WS_CAPTURE_JS = r"""(() => {
  if (!window.__wscap) {
    window.__wscap = [];
    const S = WebSocket.prototype.send;
    WebSocket.prototype.send = function (d) {
      const keep = s => !/candle|quote|tick|ping|pong|heartbeat/i.test(s);
      const t = String(d);
      if (keep(t)) window.__wscap.push(['>', Date.now(), this.url, t.slice(0, 4000)]);
      if (!this.__h) { this.__h = 1; this.addEventListener('message', e => {
        const m = typeof e.data === 'string' ? e.data : '[bin]';
        if (keep(m)) window.__wscap.push(['<', Date.now(), this.url, m.slice(0, 4000)]); }); }
      return S.call(this, d);
    };
    // ordens vão por HTTP: registra fetch/XHR (menos velas/estáticos), com corpo e resposta
    const skip = u => /candle|history|udf|\.(js|css|png|svg|woff2?)(\?|$)/i.test(u);
    const F = window.fetch;
    window.fetch = async function (input, init) {
      const url = String(input && input.url || input), body = init && init.body ? String(init.body) : '';
      const r = await F.apply(this, arguments);
      if (!skip(url)) r.clone().text().then(t => window.__wscap.push(['http', Date.now(),
        ((init && init.method) || 'GET') + ' ' + url, body.slice(0, 4000) + ' => ' + r.status + ' ' + t.slice(0, 4000)]));
      return r;
    };
    const O = XMLHttpRequest.prototype.open, XS = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (m, u) { this.__mu = m + ' ' + u; return O.apply(this, arguments); };
    XMLHttpRequest.prototype.send = function (b) {
      const tk = /"__token":"([^"]+)"/.exec(String(b || ''));
      if (tk) window.__hztok = tk[1];
      if (!skip(this.__mu || '')) this.addEventListener('loadend', () => window.__wscap.push(['xhr', Date.now(),
        this.__mu, String(b || '').slice(0, 4000) + ' => ' + this.status + ' ' + String(this.responseText || '').slice(0, 4000)]));
      return XS.apply(this, arguments);
    };
  }
  const out = window.__wscap.splice(0);
  return out.length ? JSON.stringify(out) : '';
})()"""

# Robô DEMO: mesma chamada que o botão do site faz (capturada em dados/hz_ws.json)
ORDER_JS = r"""(() => {
  const o = %s;
  window.__hzord = window.__hzord || [];
  const meta = document.querySelector('meta[name="csrf-token"]');
  const token = window.__hztok || (meta && meta.content);
  if (!token) { window.__hzord.push({ref: o.ref, error: 'sem token: faça 1 operação manual na demo'}); return 'no-token'; }
  const x = new XMLHttpRequest();
  x.open('POST', 'https://app.hezilex.com/publicapi/binary/transaction');
  x.setRequestHeader('Content-Type', 'application/json');
  x.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
  x.withCredentials = true;
  x.onloadend = () => { let r; try { r = JSON.parse(x.responseText); } catch (e) { r = {error: x.status + ' ' + String(x.responseText).slice(0, 300)}; }
    r.ref = o.ref; window.__hzord.push(r); };
  x.send(JSON.stringify({__token: token, amount: o.amount, direction: o.direction, expiration: o.expiration,
                         symbol: o.symbol, symbol_price: o.price, selected_account: o.account}));
  return 'sent';
})()"""
ORDER_POLL_JS = "window.__hzord && window.__hzord.length ? JSON.stringify(window.__hzord.splice(0)) : ''"
AUTO_LEDGER = USER_DATA / "robo_demo.json"


class Config(dict):
    """Configuração persistida em dados/config.json."""
    PATH = USER_DATA / "config.json"

    @classmethod
    def load(cls) -> "Config":
        cfg = cls(DEFAULT_CONFIG)
        if cls.PATH.exists():
            try:
                cfg.update(json.loads(cls.PATH.read_text(encoding="utf-8")))
            except ValueError:
                pass
        cfg.apply()
        return cfg

    def save(self):
        self.PATH.write_text(json.dumps(self, indent=2, ensure_ascii=False), encoding="utf-8")
        self.apply()

    def apply(self):
        engine.ENABLED_VETOS.clear()
        engine.ENABLED_VETOS.update(self["vetos"])
        engine.TRADING_HOURS = (self["hour_start"], self["hour_end"])


def local_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone()


def session_today(df, cfg: Config) -> dict:
    """Resultado do dia supondo entrada em todos os sinais A+B. Regras com 0 ficam desligadas."""
    empty = {"pnl": 0.0, "wins": 0, "losses": 0, "streak_loss": 0, "stop": ""}
    if not len(df):
        return empty
    today = datetime.now().astimezone().date()
    d = df[df["tier"].isin(["X", "A", "B"]) & df["result"].isin(["WIN", "LOSS"])]
    d = d[d["bar_utc"].map(lambda x: local_dt(x).date() == today)].sort_values("bar_utc")
    wins, losses = int((d["result"] == "WIN").sum()), int((d["result"] == "LOSS").sum())
    pnl = cfg["stake"] * (wins * cfg["payout"] - losses)
    streak = 0
    for res in reversed(d["result"].tolist()):
        if res != "LOSS":
            break
        streak += 1
    stop = ""
    if cfg["stop_win"] and pnl >= cfg["stop_win"]:
        stop = f"🎯 Meta do dia atingida (R$ {pnl:+.2f}) — pare por hoje"
    elif cfg["stop_loss"] and pnl <= -cfg["stop_loss"]:
        stop = f"🛑 Stop loss do dia atingido (R$ {pnl:+.2f}) — pare por hoje"
    elif cfg["max_losses"] and streak >= cfg["max_losses"]:
        stop = f"🛑 {streak} perdas seguidas — pausa recomendada"
    elif cfg["max_wins"] and wins >= cfg["max_wins"]:
        stop = f"🎯 {wins} vitórias hoje — meta de acertos batida"
    return {"pnl": pnl, "wins": wins, "losses": losses, "streak_loss": streak, "stop": stop}


# ---------------- workers ----------------
class JobSignals(QObject):
    done = QtSignal(object)


class Job(QRunnable):
    """Executa fn() fora da thread da UI e devolve o resultado por sinal Qt."""

    def __init__(self, fn):
        super().__init__()
        self.fn, self.signals = fn, JobSignals()

    def run(self):
        self.signals.done.emit(self.fn())


def close_analysis(cache: CandleCache, store: Store, symbols: list[str]):
    new, settled, errors = [], [], []
    for s in symbols:
        try:
            sig, closes = analyze(s, cache.get(s))
            settled += store.settle(s, closes)
            if sig and store.insert(sig):
                new.append(sig)
        except Exception as e:  # um ativo ruim não derruba o ciclo
            errors.append(f"{s}: {e}")
    return new, settled, errors


def preview_analysis(cache: CandleCache, forming: dict[str, dict]):
    out = {}
    for s, bar in forming.items():
        try:
            sig = analyze_preview(s, cache.get(s), bar)
            if sig and sig.tier != "VETO":
                out[s] = sig
        except Exception:
            pass
    return out


# ---------------- componentes ----------------
def pill(text: str, color: str) -> CaptionLabel:
    lb = CaptionLabel(text)
    lb.setStyleSheet(f"background:{color}; color:white; border-radius:9px; padding:2px 10px; font-weight:600;")
    return lb


class SignalCard(CardWidget):
    """Card de sinal. state: preview | confirmed | done | cancelled"""
    BORDER = {"preview": AMBER, "confirmed": GREEN, "cancelled": "#3f3f46"}

    def __init__(self, sig: Signal, state: str, note: str, plan: str = ""):
        super().__init__()
        border = self.BORDER.get(state, RESULT_COLORS.get(sig.result, "#3f3f46"))
        style = "dashed" if state == "preview" else "solid"
        self.setStyleSheet(f"SignalCard {{ border: 1px {style} {border}; border-left: 4px solid {border}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        top = QHBoxLayout()
        sym = StrongBodyLabel(sig.symbol)
        sym.setStyleSheet("font-size:15px;")
        is_call = sig.dir == "CALL"
        top.addWidget(sym)
        top.addWidget(pill("▲ CALL" if is_call else "▼ PUT", GREEN if is_call else RED))
        top.addStretch()
        top.addWidget(pill(f"Nível {sig.tier}", TIER_COLORS.get(sig.tier, GREY)))
        lay.addLayout(top)

        if state == "done":
            mark = {"WIN": "✔ WIN", "LOSS": "✖ LOSS", "TIE": "= EMPATE"}.get(sig.result, "… aguardando")
            head, color = mark, RESULT_COLORS.get(sig.result, GREY)
        else:
            head, color = {"preview": ("⏳ PRÉVIO — prepare-se", AMBER), "confirmed": ("✅ CONFIRMADO — ENTRAR", GREEN),
                           "cancelled": ("✖ Cancelado — não entrar", GREY)}[state]
        st = BodyLabel(head)
        st.setStyleSheet(f"color:{color}; font-weight:700;")
        lay.addWidget(st)
        if plan:
            pl = BodyLabel(plan)
            pl.setWordWrap(True)
            pl.setStyleSheet("background:rgba(34,197,94,0.12); border-radius:6px; padding:6px 8px; font-weight:600;")
            lay.addWidget(pl)
        info = CaptionLabel(note)
        info.setWordWrap(True)
        info.setTextColor(QColor("#a1a1aa"), QColor("#a1a1aa"))
        lay.addWidget(info)
        if sig.reasons and state != "cancelled":
            why = CaptionLabel("Por quê: " + sig.reasons)
            why.setWordWrap(True)
            why.setTextColor(QColor("#8b93a3"), QColor("#8b93a3"))
            lay.addWidget(why)
        if state == "cancelled":
            self.setWindowOpacity(0.6)
            for w in (sym, st, info):
                w.setStyleSheet(w.styleSheet() + "color:#71717a;")


class StatTile(SimpleCardWidget):
    """KPI pequeno: título + valor."""

    def __init__(self, title: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        self.title = CaptionLabel(title)
        self.title.setTextColor(QColor("#a1a1aa"), QColor("#a1a1aa"))
        self.value = StrongBodyLabel("—")
        self.value.setStyleSheet("font-size:16px;")
        lay.addWidget(self.title)
        lay.addWidget(self.value)

    def set(self, text: str, color: str | None = None):
        self.value.setText(text)
        self.value.setStyleSheet(f"font-size:16px; color:{color};" if color else "font-size:16px;")


SPIN_WIDTH = 170


def num(decimal: bool):
    """Campo numérico compacto com largura fixa (o padrão some com o texto dentro de QFormLayout)."""
    sp = CompactDoubleSpinBox() if decimal else CompactSpinBox()
    sp.setFixedWidth(SPIN_WIDTH)
    return sp


def table(headers: list[str]) -> TableWidget:
    t = TableWidget()
    t.setColumnCount(len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(TableWidget.NoEditTriggers)
    t.setSelectionBehavior(TableWidget.SelectRows)
    t.setBorderVisible(True)
    t.setBorderRadius(8)
    t.setWordWrap(False)
    t.horizontalHeader().setStretchLastSection(True)
    t.verticalHeader().setVisible(False)
    return t


def fill(t: TableWidget, rows: list[list], colorize: dict[int, dict[str, str]] | None = None) -> None:
    t.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            item = QTableWidgetItem("" if v is None else str(v))
            color = (colorize or {}).get(j, {}).get(str(v))
            if color:
                item.setForeground(QColor(color))
            t.setItem(i, j, item)
    t.resizeColumnsToContents()


def page(name: str, title: str, subtitle: str = "") -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    w.setObjectName(name)
    lay = QVBoxLayout(w)
    lay.setContentsMargins(28, 20, 28, 20)
    lay.setSpacing(12)
    lay.addWidget(TitleLabel(title))
    if subtitle:
        sub = CaptionLabel(subtitle)
        sub.setTextColor(QColor("#a1a1aa"), QColor("#a1a1aa"))
        lay.addWidget(sub)
    return w, lay


# ---------------- janela principal ----------------
class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HZ Sinais — monitoramento M5 (não executa ordens)")
        self.resize(1680, 960)
        USER_DATA.mkdir(exist_ok=True)
        self._seed_news()
        self.cfg = Config.load()
        self.store = Store(USER_DATA / "sinais.db")
        self.cache = CandleCache(USER_DATA / "candles")
        self.pool = QThreadPool.globalInstance()
        self.req_ids = itertools.count(1)
        self.pending: dict[int, tuple[str, datetime]] = {}  # id -> (modo, início)
        self.previews: dict[str, Signal] = {}
        self.cancelled: list[Signal] = []
        self.running = False
        self.closing_busy = False
        self.last_cycle: datetime | None = None
        self.log_file = (USER_DATA / "app.log").open("a", encoding="utf-8")

        nav = self.navigationInterface
        nav.setExpandWidth(220)
        nav.setMinimumExpandWidth(10_000)  # nunca abre sozinho: fica recolhido até clicar no ☰
        nav.setReturnButtonVisible(False)
        self._build_operate()
        self._build_history()
        self._build_stats()
        self._build_calendar()
        self._build_log()
        self._build_settings()
        self._build_tray()

        self.close_timer = QTimer(self, singleShot=True, timeout=self.close_cycle)
        self.preview_timer = QTimer(self, interval=PREVIEW_EVERY_MS, timeout=self.preview_cycle)
        self.poll_timer = QTimer(self, interval=POLL_MS, timeout=self._poll)
        QTimer(self, interval=1000, timeout=self._tick).start()
        QTimer(self, interval=5000, timeout=self.sync_chart).start()
        QTimer(self, interval=3000, timeout=self._ws_capture).start()
        QTimer(self, interval=1500, timeout=self._orders_poll).start()
        self.poll_timer.start()
        self.stackedWidget.currentChanged.connect(lambda _: self.refresh_pages())
        self._on_settings_saved(announce=False)
        self.log("App iniciado. Faça login na plataforma e clique em Iniciar.")

    # ---------- páginas ----------
    def _build_operate(self):
        w = QWidget()
        w.setObjectName("operar")
        root = QHBoxLayout(w)
        root.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(1)
        self.profile = QWebEngineProfile("hezilex", self)  # sessão persistente: login sobrevive a reinícios
        self.profile.setPersistentStoragePath(str(USER_DATA / "browser"))
        self.profile.setCachePath(str(USER_DATA / "browser-cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        self.web = QWebEngineView()
        self.web.setPage(QWebEnginePage(self.profile, self.web))
        self.web.load(QUrl(TRADEROOM))
        split.addWidget(self.web)

        side = QWidget()
        side.setMinimumWidth(SIDEBAR_WIDTH)
        side.setMaximumWidth(SIDEBAR_WIDTH + 140)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(16, 16, 16, 12)
        sl.setSpacing(10)

        head = SimpleCardWidget()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(16, 12, 16, 12)
        row = QHBoxLayout()
        row.addWidget(SubtitleLabel("HZ Sinais"))
        row.addStretch()
        self.countdown = TitleLabel("0:00")
        row.addWidget(self.countdown)
        hl.addLayout(row)
        self.status = CaptionLabel("Parado")
        self.status.setTextColor(QColor("#a1a1aa"), QColor("#a1a1aa"))
        hl.addWidget(self.status)
        brow = QHBoxLayout()
        self.btn = PrimaryPushButton(FluentIcon.PLAY, "Iniciar")
        self.btn.clicked.connect(self.toggle)
        brow.addWidget(self.btn, 1)
        hl.addLayout(brow)
        sl.addWidget(head)

        tiles = QGridLayout()
        tiles.setSpacing(8)
        self.t_pnl, self.t_wl, self.t_rate, self.t_assets = (StatTile(t) for t in
                                                             ("Hoje (R$)", "Hoje W/L", "Forward A+B", "Ativos"))
        for i, tile in enumerate((self.t_pnl, self.t_wl, self.t_rate, self.t_assets)):
            tiles.addWidget(tile, i // 2, i % 2)
        sl.addLayout(tiles)

        self.risk_banner = BodyLabel("")
        self.risk_banner.setWordWrap(True)
        self.risk_banner.setVisible(False)
        sl.addWidget(self.risk_banner)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        holder = QWidget()
        holder.setObjectName("holder")
        holder.setStyleSheet("#holder { background: transparent; }")
        self.cards = QVBoxLayout(holder)
        self.cards.setContentsMargins(0, 0, 4, 0)
        self.cards.setSpacing(10)
        self.cards.addStretch()
        scroll.setWidget(holder)
        sl.addWidget(scroll, 1)
        foot = CaptionLabel("Entrada manual na plataforma · expiração 1 vela M5")
        foot.setAlignment(Qt.AlignCenter)
        foot.setTextColor(QColor("#71717a"), QColor("#71717a"))
        sl.addWidget(foot)

        split.addWidget(side)
        split.setStretchFactor(0, 1)
        split.setSizes([1680 - SIDEBAR_WIDTH, SIDEBAR_WIDTH])
        root.addWidget(split)
        self.addSubInterface(w, FluentIcon.MARKET, "Operar")

    def _build_history(self):
        w, lay = page("historico", "Histórico", "Todos os sinais gravados, com resultado calculado automaticamente.")
        bar = QHBoxLayout()
        self.f_period, self.f_tier, self.f_sym = ComboBox(), ComboBox(), ComboBox()
        self.f_period.addItems(["Tudo", "Hoje", "Ontem", "7 dias", "30 dias"])
        self.f_tier.addItems(["Todos níveis", "X", "A", "B", "C", "VETO"])
        self.f_sym.addItems(["Todos ativos", *ALL_SYMBOLS])
        for cb in (self.f_period, self.f_tier, self.f_sym):
            cb.currentIndexChanged.connect(self.refresh_pages)
            bar.addWidget(cb)
        bar.addStretch()
        exp = PushButton(FluentIcon.SAVE, "Exportar CSV")
        exp.clicked.connect(self.export_csv)
        bar.addWidget(exp)
        lay.addLayout(bar)
        self.hist = table(["Vela", "Ativo", "Direção", "Nível", "Regras", "Preço", "Fech. seguinte", "Resultado",
                           "Vetos", "Por quê"])
        lay.addWidget(self.hist)
        self.addSubInterface(w, FluentIcon.HISTORY, "Histórico")

    def _build_stats(self):
        w, lay = page("estatisticas", "Estatísticas",
                      "Forward test real vs. esperado no backtest. Resultado em R$ supõe entrada em todos os sinais.")
        row = QHBoxLayout()
        self.st_tier = table(["Nível", "n", "Acerto", "Esperado", "EV/R$1", "Resultado R$"])
        self.st_sym = table(["Ativo (A+B)", "n", "Acerto"])
        self.st_hour = table(["Hora (A+B)", "n", "Acerto"])
        for t in (self.st_tier, self.st_sym, self.st_hour):
            row.addWidget(t)
        lay.addLayout(row)
        self.addSubInterface(w, FluentIcon.PIE_SINGLE, "Estatísticas")

    def _build_calendar(self):
        w, lay = page("calendario", "Calendário econômico", "Eventos de médio e alto impacto (ForexFactory), próximas 48h.")
        self.cal = table(["Quando (local)", "Moeda", "Impacto", "Evento"])
        lay.addWidget(self.cal)
        self.addSubInterface(w, FluentIcon.CALENDAR, "Calendário")

    def _build_log(self):
        w, lay = page("log", "Log", "Eventos, sinais e resultados — também em dados/app.log.")
        self.logview = PlainTextEdit()
        self.logview.setReadOnly(True)
        lay.addWidget(self.logview)
        self.addSubInterface(w, FluentIcon.DOCUMENT, "Log")

    def _build_settings(self):
        w, lay = page("config", "Configurações", "Salvas em dados/config.json. Pares novos entram no próximo fechamento.")
        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        body = QWidget()
        body.setObjectName("cfgbody")
        body.setStyleSheet("#cfgbody { background: transparent; }")
        cols = QHBoxLayout(body)
        cols.setSpacing(16)

        pairs = SimpleCardWidget()
        pl = QVBoxLayout(pairs)
        pl.setContentsMargins(18, 14, 18, 14)
        pl.addWidget(SubtitleLabel("Ativos monitorados"))
        grid = QGridLayout()
        self.chk_pairs = {}
        for i, sym in enumerate(ALL_SYMBOLS):
            label = sym + ("" if sym in BACKTESTED else "  · sem backtest") + ("  · ⚠ fraco" if sym in WEAK else "")
            cb = CheckBox(label)
            cb.setChecked(sym in self.cfg["symbols"])
            self.chk_pairs[sym] = cb
            grid.addWidget(cb, i % 15, i // 15)
        pl.addLayout(grid)
        btns = QHBoxLayout()
        for text, pick in (("Todos", lambda s: True), ("Nenhum", lambda s: False),
                           ("Recomendados", lambda s: s in BACKTESTED and s not in WEAK),
                           ("Só forex", lambda s: not s.endswith("USDT")), ("Só cripto", lambda s: s.endswith("USDT"))):
            b = PushButton(text)
            b.clicked.connect(lambda _=False, pick=pick: [cb.setChecked(pick(s)) for s, cb in self.chk_pairs.items()])
            btns.addWidget(b)
        pl.addLayout(btns)
        pl.addStretch()
        cols.addWidget(pairs, 3)

        right = QVBoxLayout()
        right.setSpacing(12)

        def group(title: str) -> QFormLayout:
            card = SimpleCardWidget()
            v = QVBoxLayout(card)
            v.setContentsMargins(18, 14, 18, 14)
            v.addWidget(SubtitleLabel(title))
            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignLeft)
            form.setVerticalSpacing(10)
            v.addLayout(form)
            right.addWidget(card)
            return form

        vf = group("Vetos (não operar quando…)")
        self.sw_vetos = {}
        for v in ALL_VETOS:
            sw = SwitchButton()
            sw.setChecked(v in self.cfg["vetos"])
            self.sw_vetos[v] = sw
            vf.addRow(BodyLabel(VETO_LABELS[v]), sw)

        of = group("Valores e alertas")
        self.sp_payout = num(True)
        self.sp_payout.setRange(50, 100)
        self.sp_payout.setDecimals(0)
        self.sp_payout.setSuffix(" %")
        self.sp_payout.setValue(self.cfg["payout"] * 100)
        self.sp_stake = num(True)
        self.sp_stake.setRange(1, 100000)
        self.sp_stake.setPrefix("R$ ")
        self.sp_stake.setValue(self.cfg["stake"])
        self.sp_preview = num(False)
        self.sp_preview.setRange(30, 240)
        self.sp_preview.setSingleStep(15)
        self.sp_preview.setSuffix(" s")
        self.sp_preview.setValue(self.cfg["preview_s"])
        self.sw_sound, self.sw_c = SwitchButton(), SwitchButton()
        self.sw_sound.setChecked(self.cfg["sound"])
        self.sw_c.setChecked(self.cfg["show_c"])
        of.addRow(BodyLabel("Payout médio"), self.sp_payout)
        of.addRow(BodyLabel("Valor por operação"), self.sp_stake)
        of.addRow(BodyLabel("Prévia antes do fechamento"), self.sp_preview)
        of.addRow(BodyLabel("Alertas sonoros"), self.sw_sound)
        of.addRow(BodyLabel("Mostrar nível C"), self.sw_c)

        rf = group("Sessão e gestão de risco (0 = desligado)")
        hours = QHBoxLayout()
        self.sp_h0, self.sp_h1 = num(False), num(False)
        self.sp_h0.setRange(0, 23)
        self.sp_h1.setRange(1, 24)
        self.sp_h0.setSuffix(" h")
        self.sp_h1.setSuffix(" h")
        self.sp_h0.setValue(self.cfg["hour_start"])
        self.sp_h1.setValue(self.cfg["hour_end"])
        hours.addWidget(self.sp_h0)
        hours.addWidget(BodyLabel("até"))
        hours.addWidget(self.sp_h1)
        hours.addStretch()
        rf.addRow(BodyLabel("Horário de operação"), hours)
        self.sp_sw, self.sp_sl = num(True), num(True)
        for sp, key in ((self.sp_sw, "stop_win"), (self.sp_sl, "stop_loss")):
            sp.setRange(0, 1e6)
            sp.setPrefix("R$ ")
            sp.setValue(self.cfg[key])
        self.sp_ml, self.sp_mw = num(False), num(False)
        self.sp_ml.setRange(0, 50)
        self.sp_mw.setRange(0, 200)
        self.sp_ml.setValue(self.cfg["max_losses"])
        self.sp_mw.setValue(self.cfg["max_wins"])
        rf.addRow(BodyLabel("Stop win do dia"), self.sp_sw)
        rf.addRow(BodyLabel("Stop loss do dia"), self.sp_sl)
        rf.addRow(BodyLabel("Pausar após N perdas seguidas"), self.sp_ml)
        rf.addRow(BodyLabel("Parar após N vitórias"), self.sp_mw)

        af = group("Robô — SÓ conta DEMO (nível X)")
        self.sw_auto = SwitchButton()
        self.sw_auto.setChecked(self.cfg["auto_demo"])
        self.sp_demo = num(False)
        self.sp_demo.setRange(0, 99_999_999)
        self.sp_demo.setValue(self.cfg["demo_account"])
        self.sp_bank = num(True)
        self.sp_bank.setRange(10, 1e7)
        self.sp_bank.setPrefix("R$ ")
        self.sp_bank.setValue(self.cfg["bank_base"])
        af.addRow(BodyLabel("Operar sozinho na demo"), self.sw_auto)
        af.addRow(BodyLabel("ID da conta DEMO"), self.sp_demo)
        af.addRow(BodyLabel("Banca base (entrada 1% · stop 5%)"), self.sp_bank)

        save = PrimaryPushButton(FluentIcon.SAVE, "Salvar e aplicar")
        save.clicked.connect(self.save_settings)
        right.addWidget(save)
        hint = CaptionLabel("Breakeven com payout p: 1/(1+p) — 84% ⇒ 54,3%.")
        hint.setTextColor(QColor("#a1a1aa"), QColor("#a1a1aa"))
        right.addWidget(hint)
        right.addStretch()
        cols.addLayout(right, 2)
        scroll.setWidget(body)
        lay.addWidget(scroll)
        self.addSubInterface(w, FluentIcon.SETTING, "Configurações", NavigationItemPosition.BOTTOM)

    def _build_tray(self):
        self.tray = QSystemTrayIcon(FluentIcon.MARKET.icon(), self)
        self.setWindowIcon(FluentIcon.MARKET.icon())
        self.tray.setToolTip("HZ Sinais")
        self.tray.show()

    def _seed_news(self):
        src, dst = BUNDLE / "data" / "_news.json", USER_DATA / "_news.json"
        if src.exists() and not dst.exists():
            shutil.copy(src, dst)

    # ---------- configurações ----------
    def save_settings(self):
        self.cfg.update({
            "symbols": [s for s, cb in self.chk_pairs.items() if cb.isChecked()],
            "vetos": [v for v, sw in self.sw_vetos.items() if sw.isChecked()],
            "payout": self.sp_payout.value() / 100, "stake": self.sp_stake.value(),
            "preview_s": self.sp_preview.value(), "sound": self.sw_sound.isChecked(), "show_c": self.sw_c.isChecked(),
            "hour_start": self.sp_h0.value(), "hour_end": self.sp_h1.value(),
            "stop_win": self.sp_sw.value(), "stop_loss": self.sp_sl.value(),
            "max_losses": self.sp_ml.value(), "max_wins": self.sp_mw.value(),
            "auto_demo": self.sw_auto.isChecked(), "demo_account": int(self.sp_demo.value()),
            "bank_base": self.sp_bank.value(),
        })
        self.cfg.save()
        self._on_settings_saved()
        InfoBar.success("Configuração salva", f"{len(self.cfg['symbols'])} ativos monitorados", parent=self,
                        position=InfoBarPosition.TOP_RIGHT, duration=2500)

    def _on_settings_saved(self, announce: bool = True):
        self.t_assets.set(f"{len(self.cfg['symbols'])} · R$ {self.cfg['stake']:.0f}")
        if announce:
            self.log(f"Configuração: {', '.join(self.cfg['symbols'])}")
        self.render_sidebar()

    # ---------- ciclos ----------
    def toggle(self):
        self.running = not self.running
        self.btn.setText("Parar" if self.running else "Iniciar")
        self.btn.setIcon(FluentIcon.PAUSE if self.running else FluentIcon.PLAY)
        if self.running:
            self.log("Monitoramento iniciado (primeiro ciclo baixa o histórico, ~1 min).")
            InfoBar.info("Monitorando", "Primeiro ciclo baixa o histórico (~1 min).", parent=self,
                         position=InfoBarPosition.TOP_RIGHT, duration=3000)
            self.close_cycle()
            self.preview_timer.start()
        else:
            self.close_timer.stop()
            self.preview_timer.stop()
            self.previews.clear()
            self.render_sidebar()
            self.log("Monitoramento parado.")

    @staticmethod
    def _secs_in_bar() -> int:
        return int(datetime.now(timezone.utc).timestamp()) % BAR_S

    def _schedule_close(self):
        self.close_timer.start((BAR_S - self._secs_in_bar()) * 1000 + DELAY_AFTER_CLOSE_MS)

    def _fetch(self, mode: str, plan: dict[str, int]):
        rid = next(self.req_ids)
        self.pending[rid] = (mode, datetime.now())
        self.web.page().runJavaScript(FETCH_JS % (rid, json.dumps(plan)))

    def close_cycle(self):
        if not self.running:
            return
        if self.closing_busy:
            self.log("Ciclo anterior ainda rodando — pulando.")
            return self._schedule_close()
        self.closing_busy = True
        now = int(datetime.now(timezone.utc).timestamp())
        plan = {s: self.cache.bars_needed(s, now) for s in self.cfg["symbols"]}
        heavy = sum(n > 100 for n in plan.values())
        if heavy:
            self.log(f"Baixando histórico de {heavy} ativo(s) — só na primeira vez (depois fica salvo).")
        self._fetch("close", plan)

    def preview_cycle(self):
        if not self.running or self.closing_busy or self._secs_in_bar() < BAR_S - self.cfg["preview_s"]:
            return
        if any(m == "preview" for m, _ in self.pending.values()):
            return
        ready = [s for s in self.cfg["symbols"] if self.cache.has(s)]
        if ready:
            self._fetch("preview", {s: 3 for s in ready})

    # ---------- robô demo ----------
    def _ledger(self) -> list[dict]:
        try:
            return json.loads(AUTO_LEDGER.read_text())
        except (OSError, ValueError):
            return []

    def _ledger_save(self, rows: list[dict]):
        AUTO_LEDGER.write_text(json.dumps(rows, indent=1))

    def auto_trade(self, s: Signal):
        if not (self.cfg["auto_demo"] and self.cfg["demo_account"]):
            return
        rows = self._ledger()
        ref = f"{s.bar_utc}|{s.symbol}"
        if any(r["ref"] == ref for r in rows):
            return
        plan = StakePlan(self.cfg["bank_base"])
        bank = self.cfg["bank_base"] + sum(r.get("pnl", 0) for r in rows)
        today = datetime.now().date().isoformat()
        day_pnl = sum(r.get("pnl", 0) for r in rows if r["day"] == today)
        if not plan.can_trade(day_pnl, bank):
            self.log(f"🤖 robô parado hoje: resultado {day_pnl:+.2f} (stop −{plan.daily_stop():.2f})")
            return
        stake = plan.stake(bank)
        order = {"ref": ref, "amount": int(round(stake * 100)), "direction": 1 if s.dir == "CALL" else 0,
                 "expiration": 5, "symbol": s.symbol, "price": s.price, "account": self.cfg["demo_account"]}
        rows.append({"ref": ref, "day": today, "symbol": s.symbol, "dir": s.dir, "stake": stake, "status": "enviada"})
        self._ledger_save(rows)
        self.web.page().runJavaScript(ORDER_JS % json.dumps(order))
        self.log(f"🤖 DEMO {s.symbol} {s.dir} R$ {stake:.2f} (banca {bank:.2f}, dia {day_pnl:+.2f})")

    def _orders_poll(self):
        self.web.page().runJavaScript(ORDER_POLL_JS, 0, self._orders_done)

    def _orders_done(self, payload):
        if not payload:
            return
        rows = self._ledger()
        for r in json.loads(payload):
            for row in rows:
                if row["ref"] != r.get("ref"):
                    continue
                if r.get("status") == "success" and r.get("transaction_account") == self.cfg["demo_account"]:
                    row.update(status="aberta", tx=r.get("transaction_id"), odd=r.get("odd", 1.8))
                    self.log(f"🤖 ordem #{r.get('transaction_id')} aceita · {row['symbol']} · expira {r.get('expiration_datetime')}")
                else:
                    row.update(status="erro", erro=str(r.get("error") or r.get("message") or r)[:300])
                    self.log(f"🤖 ordem recusada {row['symbol']}: {row['erro']}")
        self._ledger_save(rows)

    def auto_settle(self, settled: list[Signal]):
        rows = self._ledger()
        done = {f"{s.bar_utc}|{s.symbol}": s.result for s in settled}
        changed = False
        for row in rows:
            res = done.get(row["ref"])
            if row.get("status") == "aberta" and res:
                odd = row.get("odd", 1.8)
                row.update(status=res, pnl=round(row["stake"] * (odd - 1), 2) if res == "WIN" else
                           -row["stake"] if res == "LOSS" else 0.0)
                changed = True
                self.log(f"🤖 {res} {row['symbol']} {row['pnl']:+.2f}")
        if changed:
            self._ledger_save(rows)

    def _ws_capture(self):
        """Grava o tráfego WebSocket da Hezilex em dados/hz_ws.json (mapear envio de ordens na demo)."""
        self.web.page().runJavaScript(WS_CAPTURE_JS, 0, self._ws_save)

    def _ws_save(self, frames):
        if frames:
            path = USER_DATA / "hz_ws.json"
            old = json.loads(path.read_text()) if path.exists() else []
            path.write_text(json.dumps(old + json.loads(frames)))

    def _poll(self):
        for rid, (mode, started) in list(self.pending.items()):
            limit = FETCH_TIMEOUT_S if self.cache.has_any() else FIRST_LOAD_TIMEOUT_S
            if (datetime.now() - started).total_seconds() > limit:
                # usa o que já chegou em vez de jogar o ciclo fora
                self.web.page().runJavaScript(f"JSON.stringify(window.__hzp && window.__hzp[{rid}])",
                                              lambda res, rid=rid, mode=mode: self._on_timeout(rid, mode, res))
                continue
            self.web.page().runJavaScript(f"window.__hz && window.__hz[{rid}]",
                                          lambda res, rid=rid: self._on_fetch(rid, res))

    def _on_timeout(self, rid: int, mode: str, partial):
        if rid not in self.pending:
            return
        prog = json.loads(partial) if partial and partial != "null" else None
        if not prog:
            self.pending.pop(rid)
            self.log(f"⚠ Timeout ({mode}) sem resposta da página — ela está na Hezilex e logada?")
            if mode == "close":
                self.closing_busy = False
                self._schedule_close()
            return
        errs = ", ".join(f"{k}: {v}" for k, v in prog["err"].items()) or "nenhum"
        self.log(f"⚠ Timeout ({mode}): {len(prog['data'])}/{prog['total']} ativos recebidos · erros: {errs}")
        prog["loginError"] = False
        self._on_fetch(rid, json.dumps(prog))

    def _on_fetch(self, rid: int, result):
        if not result or rid not in self.pending:
            return
        mode, _ = self.pending.pop(rid)
        self.web.page().runJavaScript(f"delete window.__hz[{rid}]; delete window.__hzp[{rid}]")
        payload = json.loads(result)
        if payload["loginError"]:
            self.log("⚠ Sessão expirada — faça login na plataforma.")
            InfoBar.warning("Sessão expirada", "Faça login na plataforma (página Operar).", parent=self,
                            position=InfoBarPosition.TOP_RIGHT, duration=8000)
        now = payload["now"]
        if mode == "preview":
            forming = {s: f for s, raw in payload["data"].items() if (f := CandleCache.forming(raw, now))}
            job = Job(lambda: preview_analysis(self.cache, forming))
            job.signals.done.connect(self._on_preview)
            return self.pool.start(job)

        for s, raw in payload["data"].items():
            self.cache.merge(s, raw, now)
            self.cache.save(s)
        missing = sorted(set(self.cfg["symbols"]) - set(payload["data"]))
        if missing:
            self.log(f"Sem resposta neste ciclo: {', '.join(missing)}")
        ready = [s for s in self.cfg["symbols"] if self.cache.has(s)]
        self.cycle_info = (len(payload["data"]), len(self.cfg["symbols"]))
        job = Job(lambda: close_analysis(self.cache, self.store, ready))
        job.signals.done.connect(self._on_close)
        self.pool.start(job)

    def _on_preview(self, previews: dict):
        if not self.running:
            return
        fresh = [s for s in previews if s not in self.previews and self._visible(previews[s].tier)]
        self.previews = previews
        for s in fresh:
            p = previews[s]
            self.log(f"⏳ prévio [{p.tier}] {p.symbol} {p.dir}")
            if self.cfg["sound"] and p.tier in ("X", "A", "B"):
                winsound.Beep(880, 120)
        self.render_sidebar()
        self.sync_chart()

    def _on_close(self, result):
        new, settled, errors = result
        self.closing_busy = False
        for e in errors:
            self.log(f"erro {e}")
        for s in settled:
            self.log(f"{s.result:4} {s.symbol} {s.dir} [{s.tier}] {local_dt(s.bar_utc):%H:%M}  {s.price} → {s.close_after}")
        confirmed = {s.symbol for s in new if s.tier != "VETO"}
        self.cancelled = [p for sym, p in self.previews.items() if sym not in confirmed and self._visible(p.tier)]
        self.previews = {}
        for s in sorted(new, key=lambda s: s.tier):
            if s.tier == "VETO":
                self.log(f"vetado {s.symbol} {s.dir} ({s.vetos})")
                continue
            self.log(f"✅ [{s.tier}] {s.symbol} {s.dir} @ {s.price} ({s.strategies}; {TIER_DESC[s.tier]})")
            if s.tier in ("X", "A", "B"):
                self.tray.showMessage(f"✅ {s.symbol} {s.dir} — nível {s.tier}",
                                      f"Entrar agora · expira {self._expiry(s):%H:%M} · {TIER_DESC[s.tier]}",
                                      QSystemTrayIcon.Information, 15000)
                if self.cfg["sound"]:
                    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION if s.tier in ("X", "A") else winsound.MB_OK)
        for s in new:
            if s.tier == "X":
                self.auto_trade(s)
        self.auto_settle(settled)
        self.last_cycle = datetime.now()
        got, total = getattr(self, "cycle_info", (0, 0))
        valid = sum(s.tier != "VETO" for s in new)
        self.log(f"Ciclo ok: {got}/{total} ativos · {valid} sinal(is) · {len(new) - valid} vetado(s)"
                 f" · {len(settled)} resultado(s) · {len(errors)} erro(s)")
        self.render_sidebar()
        self.refresh_pages()
        self.sync_chart()
        self._schedule_close()

    # ---------- render ----------
    def _visible(self, tier: str) -> bool:
        return tier in ("X", "A", "B") or (tier == "C" and self.cfg["show_c"])

    @staticmethod
    def _expiry(s: Signal) -> datetime:
        return local_dt(s.bar_utc) + timedelta(seconds=2 * BAR_S)  # vela do sinal + vela da operação

    def render_sidebar(self):
        while self.cards.count() > 1:
            item = self.cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        items: list[QWidget] = []
        close_at = datetime.now() + timedelta(seconds=BAR_S - self._secs_in_bar())
        for p in sorted(self.previews.values(), key=lambda s: s.tier):
            if self._visible(p.tier):
                items.append(SignalCard(p, "preview", f"Confirma no fechamento {close_at:%H:%M} · {p.strategies} · "
                                                      f"{TIER_DESC[p.tier]}"))
        df = self.store.frame()
        today = datetime.now().astimezone().date()
        cur_bar = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        cur_bar -= timedelta(minutes=cur_bar.minute % 5)
        for r in df.itertuples():
            dt = local_dt(r.bar_utc)
            if dt.date() != today or not self._visible(r.tier):
                continue
            sig = Signal(r.bar_utc, r.symbol, r.dir, r.tier, r.strategies, r.price, r.vetos, r.result or "",
                         r.close_after, r.reasons if isinstance(r.reasons, str) else "")
            exp = self._expiry(sig)
            live = not sig.result and datetime.fromisoformat(r.bar_utc) + timedelta(seconds=BAR_S) >= cur_bar
            if live:
                entry = dt + timedelta(seconds=BAR_S)
                items.append(SignalCard(sig, "confirmed", f"{sig.strategies} · {TIER_DESC[sig.tier]}",
                                        plan=f"▶ Entrada {entry:%H:%M} (até {entry + timedelta(seconds=60):%H:%M:%S})"
                                             f"  ·  vence {exp:%H:%M}  ·  ref {sig.price}"))
            else:
                after = r.close_after if r.close_after == r.close_after else "…"  # NaN != NaN
                items.append(SignalCard(sig, "done", f"{dt:%H:%M} · {sig.price} → {after}"))
            if len(items) >= MAX_CARDS:
                break
        for c in self.cancelled:
            items.append(SignalCard(c, "cancelled", "Prévio não confirmou no fechamento"))
        for i, w in enumerate(items):
            self.cards.insertWidget(i, w)
        if not items:
            empty = BodyLabel("Sem sinais ainda.\nClique em Iniciar e aguarde o fechamento das velas.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:#71717a; padding:30px;")
            self.cards.insertWidget(0, empty)

        sess = session_today(df, self.cfg)
        self.t_pnl.set(f"R$ {sess['pnl']:+.2f}", GREEN if sess["pnl"] > 0 else RED if sess["pnl"] < 0 else None)
        self.t_wl.set(f"{sess['wins']}W / {sess['losses']}L")
        done = df[df["tier"].isin(["X", "A", "B"]) & df["result"].isin(["WIN", "LOSS"])] if len(df) else df
        if len(done):
            rate = (done["result"] == "WIN").mean()
            self.t_rate.set(f"{rate:.1%} · {len(done)}", GREEN if rate > 1 / (1 + self.cfg["payout"]) else RED)
        was_stopped = self.risk_banner.isVisible()
        self.risk_banner.setVisible(bool(sess["stop"]))
        if sess["stop"]:
            color = GREEN if sess["stop"].startswith("🎯") else RED
            self.risk_banner.setText(sess["stop"])
            self.risk_banner.setStyleSheet(f"background:{color}; color:white; border-radius:8px; padding:10px; "
                                           "font-weight:700;")
            if not was_stopped:
                self.tray.showMessage("HZ Sinais", sess["stop"], QSystemTrayIcon.Warning, 15000)

    def sync_chart(self):
        """Marcas do dia no gráfico: prévio (âmbar), confirmado (verde/vermelho), resultado (WIN/LOSS)."""
        marks = []
        for p in self.previews.values():
            if self._visible(p.tier):
                marks.append({"kind": "preview", "sym": p.symbol, "dir": 1 if p.dir == "CALL" else -1, "tier": p.tier,
                              "time": int(datetime.fromisoformat(p.bar_utc).timestamp()), "price": p.price,
                              "text": f"⏳ prévio {p.tier}"})
        df = self.store.frame()
        if len(df):
            today = datetime.now().astimezone().date()
            cur_bar = int(datetime.now(timezone.utc).timestamp()) // BAR_S * BAR_S
            for r in df.itertuples():
                if not self._visible(r.tier) or local_dt(r.bar_utc).date() != today:
                    continue
                t = int(datetime.fromisoformat(r.bar_utc).timestamp())
                d = 1 if r.dir == "CALL" else -1
                if r.result:
                    marks.append({"kind": "result", "sym": r.symbol, "dir": d, "tier": r.tier, "time": t + BAR_S,
                                  "price": r.close_after, "text": f"{r.result} {r.tier}"})
                elif t + BAR_S >= cur_bar:
                    marks.append({"kind": "confirm", "sym": r.symbol, "dir": d, "tier": r.tier, "time": t,
                                  "price": r.price, "text": f"✅ {r.dir} {r.tier}"})
                if r.result:  # mantém a seta de entrada também após o resultado
                    marks.append({"kind": "confirm", "sym": r.symbol, "dir": d, "tier": r.tier, "time": t,
                                  "price": r.price, "text": f"{r.dir} {r.tier}"})
        self.web.page().runJavaScript(CHART_SYNC_JS % json.dumps(marks))

    def refresh_pages(self):
        df = self.store.frame()
        h = df
        days = {1: 0, 2: 1, 3: 7, 4: 30}.get(self.f_period.currentIndex())
        if days is not None and len(h):
            today = datetime.now().astimezone().date()
            dates = h["bar_utc"].map(lambda x: local_dt(x).date())
            h = h[dates == today - timedelta(days=1)] if self.f_period.currentIndex() == 2 else \
                h[dates >= today - timedelta(days=days)]
        if self.f_tier.currentIndex() > 0:
            h = h[h["tier"] == self.f_tier.currentText()]
        if self.f_sym.currentIndex() > 0:
            h = h[h["symbol"] == self.f_sym.currentText()]
        fill(self.hist, [[f"{local_dt(r.bar_utc):%d/%m %H:%M}", r.symbol, r.dir, r.tier, r.strategies, r.price,
                          r.close_after, r.result or "…", r.vetos, r.reasons if isinstance(r.reasons, str) else ""]
                         for r in h.head(3000).itertuples()], {3: TIER_COLORS, 7: RESULT_COLORS})

        p, stake = self.cfg["payout"], self.cfg["stake"]
        fill(self.st_tier, [[r.tier, r.n, f"{r.acerto:.1%}",
                             f"{TIER_EXPECTED[r.tier]:.1%}" if r.tier in TIER_EXPECTED else "—",
                             f"{r.acerto * p - (1 - r.acerto):+.3f}",
                             f"R$ {r.n * stake * (r.acerto * p - (1 - r.acerto)):+,.2f}"]
                            for r in winrate_table(df, "tier").itertuples()], {0: TIER_COLORS})
        traded = df[df["tier"].isin(["X", "A", "B"])]
        fill(self.st_sym, [[r.symbol, r.n, f"{r.acerto:.1%}"] for r in winrate_table(traded, "symbol").itertuples()])
        if len(traded):
            traded = traded.assign(hora=traded["bar_utc"].map(lambda x: local_dt(x).hour))
            fill(self.st_hour, [[f"{r.hora:02d}h", r.n, f"{r.acerto:.1%}"]
                                for r in winrate_table(traded, "hora").sort_values("hora").itertuples()])
        self._refresh_calendar()

    def _refresh_calendar(self):
        path = USER_DATA / "_news.json"
        rows = []
        if path.exists():
            now = datetime.now().astimezone()
            for ts, cur, impact, name in sorted(json.loads(path.read_text())):
                when = datetime.fromtimestamp(ts, timezone.utc).astimezone()
                if now - timedelta(hours=2) <= when <= now + timedelta(days=2):
                    rows.append([f"{when:%a %d/%m %H:%M}", cur, "Alto" if impact == "high" else "Médio", name or "—"])
        if not rows:
            rows = [["—", "—", "—", "Sem eventos carregados para este período (calendário precisa ser atualizado)"]]
        fill(self.cal, rows, {2: {"Alto": RED, "Médio": AMBER}})

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar sinais", str(USER_DATA / "sinais.csv"), "CSV (*.csv)")
        if path:
            self.store.frame().to_csv(path, index=False, sep=";", decimal=",")
            InfoBar.success("Exportado", path, parent=self, position=InfoBarPosition.TOP_RIGHT, duration=3000)

    def _tick(self):
        rem = BAR_S - self._secs_in_bar()
        in_preview = rem <= self.cfg["preview_s"]
        self.countdown.setText(f"{rem // 60}:{rem % 60:02d}")
        self.countdown.setStyleSheet(f"color:{AMBER};" if in_preview else "")
        if not self.running:
            self.status.setText("Parado — faça login na plataforma e clique em Iniciar")
            return
        state = "baixando/analisando…" if self.closing_busy else ("janela de prévia" if in_preview else "monitorando")
        self.status.setText(state + (f" · último ciclo {self.last_cycle:%H:%M:%S}" if self.last_cycle else ""))

    def log(self, msg: str):
        line = f"{datetime.now():%d/%m %H:%M:%S}  {msg}"
        self.logview.appendPlainText(line)
        self.log_file.write(line + "\n")
        self.log_file.flush()

    def closeEvent(self, e):
        self.log("App fechado.")
        self.log_file.close()
        super().closeEvent(e)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("HZ Sinais")
    setTheme(Theme.DARK)
    setThemeColor(ACCENT)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
