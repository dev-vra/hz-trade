# Técnica cripto — mineração 1 ano (Binance M5/M15, 21/10/2025–20/10/2026)
Scripts: src/dl_binance.py, src/mine.py (varre ~milhares de regras, treino 8m / teste 4m), src/tech.py, src/tech2.py.
Resultados brutos: replica/mine_5min.csv, replica/mine_15min.csv.

## Regra principal — "Exaustão Verde" (M5, PUT, expiração 5 min)
Vela M5 fechada VERDE, corpo > 2×ATR(14), sendo a 3ª+ verde seguida → PUT na abertura da próxima.
- Binance 8 ativos: treino 59,8% / teste 61,1% (n=710), ~5,8/dia, 82 de 122 dias com ≥4 sinais. 10/13 meses ≥55%.
- + vetos HZ: teste 66,5% (n=466), 3,8/dia.  + BTC também explodiu na mesma vela: 65,2%, 2,2/dia.
- Hezilex (120 dias): ETH 61%, SOL 66%, XRP 65%, ADA 57%, BTC 53% → operar ETH/SOL/XRP(/ADA), evitar BTC e BNB.
- Espelho (vermelha gigante → CALL) NÃO funciona em M5 (54%).
## Complemento M15 (CALL, expiração 15 min)
3+ velas M15 vermelhas, corpo ≥0,7 ATR, Estocástico <20 → CALL: treino 58,3% / teste 58,9%, 8,6/dia.
## Descartado
- Filtro de horário fixo: escolhido no treino caiu de 67% p/ 60% no teste (overfit).
- Nossa reversão S5/S2b/S2 em M15 cripto: 55% (≈ breakeven); só tier A 59% c/ 1,4/dia.
- BTC lidera alts (alt seguir BTC): ~57%, instável (meses 46–70%).
Breakeven payout 82% = 54,9%.
