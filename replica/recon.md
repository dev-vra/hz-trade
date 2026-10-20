# Recon — Nexor Academy (ianexor.com.br)

Data: 2026-10-19 · Plataforma: web (SPA) · Conta: própria do usuário, teste grátis DEMO (3 dias)
Escopo: entender a lógica de sinais/robô e o que aproveitar no **HZ Sinais**. Não é para clonar o produto.
Método: leitura de páginas a velocidade humana. Sem leitura de bundles JS e sem log de rede. 1 análise gerada (EURUSD M5).
Robô **não** foi iniciado (executaria operações).

## Fontes
| fonte | URL |
|---|---|
| Dashboard | https://ianexor.com.br/ |
| Análise IA | https://ianexor.com.br/analise |
| Nexor Auto (robô) | https://ianexor.com.br/auto |
| Corretora (iframe PumaBroker) | https://ianexor.com.br/corretora |
| Histórico | https://ianexor.com.br/historico |
| Planos (R$ 85/mês) | https://ianexor.com.br/planos |
| Aulas | https://ianexor.com.br/aulas |

## Modelo de negócio (inferido)
- Assinatura única: R$ 85/mês. Free: 2 análises/dia, teste DEMO de 3 dias.
- Corretora parceira **PumaBroker** embutida via iframe (`trade.pumabroker.com`). Saldo exibido como "SALDO PUMA". Provável afiliação/revshare.
- Gamificação: "placas físicas" por R$ 100K–5M em saques, ranking por indicações, grupo VIP no Telegram.
- Aula "Quanto investir": empurra banca acima de R$ 500 e compara com renda fixa (marketing).

## A estratégia: "placar de confluência"
Sete leituras somadas com pesos. A direção é o sinal do placar total (±11):

| leitura | peso | regra observada (EURUSD M5) |
|---|---|---|
| Médias móveis (EMA 9/21/50) | ±2 | cruzamento/alinhamento; "lateral" = 0 |
| Estrutura SMC (BOS/CHoCH) | ±3 | "BOS de alta: fechamento rompeu o último topo" = +3 |
| Order block | ±2 | preço em OB de alta/baixa |
| Bollinger | ±1 | %B=100 (na banda superior) deu **0**, sem ler reversão |
| RSI(14) | ±1 | RSI 60 = "força compradora" = +1 (leitura de **momentum**) |
| MACD | ±1 | histograma positivo e crescendo = +1 |
| FVG | ±1 | preço dentro de FVG de baixa = −1 |

- Confluência % = placar/11 (+4 → 36% "moderada").
- Saída: CALL/PUT na próxima vela, expiração de 1 vela, preço de referência, suporte/resistência, contagem regressiva para a entrada.
- Robô "Nexor Auto": segue a "fonte de sinais MT4". Entrada única, sem gale e sem soros. Stop win/loss em R$, payout mínimo, parar após N losses ou N wins. Sessões recomendadas: 7h e 21h30 (seg–qui).
- **Inconsistência:** a análise mostrou "36% moderada", mas o dashboard e o histórico registraram "ALTA · 65%".

### Avaliação contra o nosso backtest
O placar é **seguidor de momentum/tendência**: RSI alto = compra, BOS = compra, MACD crescendo = compra.
No nosso backtest de 4 meses e 18 ativos em M5, essa família perde: S1 49%, S4 45,5%, S9 49%.
A reversão (S5/S2b) é que ficou acima do breakeven. Hipótese: o placar Nexor fica **abaixo de 50%** no M5. Testável.

## Telas
| ID | tela | rota | propósito | componentes | estados |
|---|---|---|---|---|---|
| S01 | Dashboard | / | resumo: plano, acerto, sequência, histórico robô, últimas análises, calendário, prêmios | cards KPI, lista, toggle DEMO/REAL | vazio, 1 análise pendente |
| S02 | Análise IA | /analise | escolher ativo + TF (M1/M5/M15) e gerar análise | select, segmented TF, botão, limite diário | antes de gerar |
| S03 | Resultado da análise | /analise (após gerar) | placar, direção, gráfico com S/R e EMAs, plano de entrada com countdown | gauge %, badges, gráfico, tabela de placar, níveis | concluída |
| S04 | Nexor Auto | /auto | ligar/desligar robô, configuração de risco, performance, atividade, log | cards, inputs numéricos, tabela, log | parado |
| S05 | Corretora | /corretora | PumaBroker embutida com robô flutuante | iframe, recarregar/tela cheia/abrir fora | aviso de cookies 3rd-party |
| S06 | Histórico | /historico | análises e operações filtradas por período | tabs, filtros de data, KPIs | 1 pendente |
| S07 | Planos | /planos | assinatura | card de preço | — |
| S08 | Aulas | /aulas | vídeos | cards de vídeo | 2 aulas |
| S09–S14 | Gamificação, Grupo VIP, Prêmios, Calendário, Produtos, Perfil | /indicacoes /telegram /premios /calendario /produtos /perfil | não visitadas | — | — |

## Fluxos
- **F01** Gerar análise: S02 (ativo → TF → Gerar) → S03 · 3 cliques
- **F02** Ligar robô: S04 (configurar → Iniciar) · 2 cliques + inputs (não executado)
- **F03** Operar manual: S03 (sinal + countdown) → S05 (corretora embutida)

## O que vale reaproveitar no HZ Sinais
1. **Placar ponderado transparente** com explicação em texto por indicador ("RSI 60 — força compradora"). Dá para mostrar no card *por que* o sinal saiu.
2. **Plano de entrada**: horário de entrada e de vencimento, preço de referência e countdown "ENTRADA EM 00:48". Nosso card só tem parte disso.
3. **Gestão de risco da sessão**: stop win/loss em R$, parar após N losses ou N wins, payout mínimo. Vale como alerta ("meta do dia batida, pare").
4. **Níveis S/R e mini-gráfico** no card do sinal.
5. **Sessões/horários recomendados** configuráveis.
6. **Calendário econômico do dia** no painel (já temos os dados do FF).
7. Corretora embutida + painel flutuante: o HZ Sinais já faz isso.

## Não aproveitar / fora de escopo
- Execução automática de ordens (decisão do projeto: entrada manual).
- Afiliação com corretora, gamificação de saques e indicações.
- Lógica de momentum como gatilho principal, porque contradiz nosso backtest (testar antes).

## Tamanho
Para o que interessa (itens 1–6 no HZ Sinais): **S**, um fim de semana.
Partes difíceis: SMC/BOS e order blocks sem repaint; FVG; calibrar pesos do placar via backtest.
