# trade — análise M5 Hezilex

Ferramenta de **análise e alerta**. Não executa ordens.

## Forward test (alerta ao vivo)
1. Terminal: `python src/live.py` (porta 127.0.0.1:8765)
2. Brave, aba `app.hezilex.com/traderoom` logada → F12 → Console → colar `src/browser_loop.js`
   (se pedir "acessar rede local", permitir)
3. A cada fechamento de M5 o terminal mostra os sinais. Log com resultado automático: `reports/forward.csv`

Níveis (acerto fora da amostra no backtest; breakeven ~54–55%):
| Nível | Regra | Acerto | Sinais/dia |
|---|---|---|---|
| A | reversão (S5/S2b/S2) + vetos ok + vela gigante recente | ~66% | ~8 |
| B | reversão + vetos ok + H4 a favor | ~58% | ~15 |
| C | reversão + vetos ok | ~56% | resto |
| VETO | overlap Londres/NY, notícia US/FF alta, fim de hora (:50/:55) | — | não operar |

Expiração: 1 vela M5 (entra logo após o fechamento da vela do sinal).

## Backtest
- Dados: `data/*.json` (baixados via browser), notícias `data/_news.json` (ForexFactory)
- `python src/backtest.py` → `reports/backtest.md`

## Limitações
4 meses de dados; entrada simulada no fechamento (sem atraso); amostra do nível A pequena (IC ±6pp).
Validar no demo 2–4 semanas antes de dinheiro real.
