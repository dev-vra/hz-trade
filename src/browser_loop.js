// Colar no Console da aba app.hezilex.com/traderoom (logado). Envia candles M5 FECHADOS
// ao motor local (src/live.py) logo após cada fechamento de vela. Parar: clearTimeout(window.__liveTimer)
(() => {
  const SYMBOLS = ['EURUSD', 'GBPUSD', 'USDJPY', 'AUDJPY', 'EURJPY', 'EURGBP', 'AUDCAD', 'USDCAD', 'NZDUSD',
    'CADJPY', 'CHFJPY', 'GBPJPY', 'USDCHF', 'EURAUD', 'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT'];
  const BAR = 300, BARS_NEEDED = 3000, DELAY_AFTER_CLOSE_MS = 4000;
  const CONCURRENCY = 4;
  const MAX_WINDOW_S = 86400 * 18;  // janela > ~20 dias devolve HTTP 500
  const gen = window.__liveGen = (window.__liveGen || 0) + 1;  // reinjetar mata o loop antigo

  const cache = window.__liveCache = window.__liveCache || {};  // histórico baixa 1x; depois só incremental

  async function udf(symbol, now, count) {
    const url = `/publicapi/tradingview/udf-history?symbol=${symbol}&resolution=5&from=${now - Math.min(BAR * count * 3, MAX_WINDOW_S)}` +
      `&to=${now}&countback=${count}&site=app.hezilex.com`;
    const r = await fetch(url).then(x => x.json());
    return r.s === 'ok' ? r : null;
  }

  async function closedCandles(symbol, now) {
    const r = await udf(symbol, now, cache[symbol] ? 12 : BARS_NEEDED);
    if (!r) return null;
    const c = cache[symbol] || { t: [], o: [], h: [], l: [], c: [] };
    r.t.forEach((t, i) => {
      if (t + BAR > now) return;  // descarta a vela em formação
      const j = c.t.indexOf(t);
      if (j >= 0) { for (const k of ['o', 'h', 'l', 'c']) c[k][j] = r[k][i]; return; }
      if (c.t.length && t < c.t[c.t.length - 1]) return;
      for (const k of ['t', 'o', 'h', 'l', 'c']) c[k].push(r[k][i]);
    });
    for (const k of ['t', 'o', 'h', 'l', 'c']) c[k] = c[k].slice(-BARS_NEEDED);
    cache[symbol] = c;
    return c;
  }

  async function cycle() {
    if (gen !== window.__liveGen) return;
    const now = Math.floor(Date.now() / 1000);
    const payload = {};
    const queue = [...SYMBOLS];
    const worker = async () => {  // carga inicial pesada derruba o servidor (HTTP 500) se tudo em paralelo
      for (let s; (s = queue.shift());) {
        for (let attempt = 0; attempt < 2 && !payload[s]; attempt++) {
          try { const c = await closedCandles(s, now); if (c) payload[s] = c; } catch (e) { console.warn('[live]', s, e); }
        }
      }
    };
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
    try {
      const sigs = await fetch('http://127.0.0.1:8765/', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      }).then(r => r.json());
      window.__liveLast = { at: new Date().toISOString(), n: Object.keys(payload).length, sigs };
      sigs.forEach(s => console.log(`%c[${s.tier}] ${s.symbol} ${s.dir}`, 'font-size:16px;color:#0f0', s));
    } catch (e) { window.__liveLast = { at: new Date().toISOString(), err: String(e) }; }
    schedule();
  }

  function schedule() {
    const ms = BAR * 1000;
    const wait = ms - (Date.now() % ms) + DELAY_AFTER_CLOSE_MS;
    window.__liveTimer = setTimeout(cycle, wait);
  }

  clearTimeout(window.__liveTimer);
  cycle();  // roda já uma vez para validar
})();
