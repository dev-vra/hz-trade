# Recon — algoritmorusso.com (2026-10-19 23:50)

## Arquitetura
- SPA React (Vite, `/assets/index-*.js`), backend **Supabase** (`bzukxehtegnhdlpvootf.supabase.co`).
- Tabelas REST: platform_settings, admin_plans, user_roles, profiles, trade_analyses, bonus_credits.
- Edge functions: `functions/v1/market-data` (velas), `functions/v1/analyze_trade` (gera análise via LLM).
- Gráfico: widget TradingView embed (só visual, não alimenta a análise).
- Analytics/tracking: `~api/analytics`, `__l5e/trackevents`, `~flock.js`.
- Menu: Dashboard, Histórico, Planos, Análise Print, Comunidade, Cursos, Configurações. Plano Pro R$147,90/mês; 5 créditos grátis.

## Fluxo "Analisar mercado"
1. market-data → 60 velas M5.
2. Indicadores calculados (RSI, MACD, EMA20/50, ATR, BB, StochRSI, S/R, sombras, sessão).
3. Indicadores + velas → LLM (`ai_payload`) que escreve decisão, "força", "confiança", textos, previsões horas/dias/semanas.
4. Grava em `trade_analyses` (campo `result` = null; não há auditoria automática de acerto).

## Análise testada (crédito 2/5)
- ADA/USD M5, Opções Binárias → **PUT**, expiração M15, entrada 00:00, "força 82%", "confluência 9/10".
- **Dados de entrada quebrados**: as 60 velas têm o/h/l/c = 0.22 (preço arredondado a 2 casas), última vela 02:05Z (45 min antes da análise).
- Com série plana: ATR=0, MACD=0, BB bandwidth=0; o "RSI 99.01" é artefato de divisão por ~0. A LLM interpretou como "exaustão compradora" e deu PUT 82%.
- Stop loss 0.00000, TP1=TP2=TP3=entrada, "0 eventos de notícias", alvos 0.22/0.21/0.20.
- Os percentuais (força, confiança, precisão) são gerados pela LLM, não por estatística histórica.

## Conclusão
O sinal é texto de LLM sobre indicadores; para ADA a entrada era uma série constante, então esse sinal não tem base. Não há backtest nem placar de acerto publicado.
Pendente: conferir PUT 00:00–00:15 (horário de Brasília) nas velas reais da Hezilex/Binance.

## EUR/USD (crédito 3/5)
- PUT, força 73%. Velas de 15/10 10:50 (4 dias atrasadas), arredondadas para 1.12, ATR=0, BB bandwidth=0, RSI 88. Mesmo bug do ADA.

## Backtest das métricas deles (src/russo.py, 19 ativos M5, ~120 dias; LTC sem dados)
Regras: reversão = RSI>70 + StochRSI>80 + %B>1 (e o espelho); "rsi" = só RSI extremo; tendência = MACD+EMA; votação 5 indicadores ≥4.
- Tendência (MACD+EMA): 48% → perde.
- Só RSI: 53–55% → no breakeven.
- Reversão tripla M15 sem filtro 57,4% / com vetos 58,9% / vetos+vela gigante (tier A) 66,9%.
- Mas 570 dos 1835 sinais tier A são da reabertura 18h BRT (91%, artefato de spread). Sem 18–19h: 57,1%, EV +0,046/entrada, ~10 sinais/dia.
- Votação ≥4: praticamente nunca dispara (indicadores de reversão e de tendência se contradizem).
Saída: replica/russo_backtest.csv
