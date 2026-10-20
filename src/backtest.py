"""Backtest das estratégias + análise de filtros/score com validação fora da amostra.

Uso: python src/backtest.py   -> gera reports/backtest.md
"""
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from features import DATA, build  # noqa: E402
from strategies import STRATEGIES  # noqa: E402

REPORTS = pathlib.Path(__file__).resolve().parent.parent / "reports"
TRAIN_FRACTION = 0.7
MIN_SAMPLES = 150
WEEKDAYS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def trade_features(t: pd.DataFrame) -> pd.DataFrame:
    """Fatores candidatos a filtro/score, todos booleanos e relativos à direção do trade."""
    t = t.reset_index(drop=True)  # alinhamento seguro com índice duplicado
    d = t["dir"]
    f = pd.DataFrame(index=t.index)
    f["m15_a_favor"] = t["trend_m15"] == d
    f["h1_a_favor"] = t["trend_h1"] == d
    f["h4_a_favor"] = t["trend_h4"] == d
    f["estrutura_a_favor"] = t["structure"] * d > 0.5
    f["fib_zona_ouro"] = (t["fib_dir"] == d) & t["fib_retr"].between(0.45, 0.70)
    f["fib_raso_<0.38"] = (t["fib_dir"] == d) & (t["fib_retr"] < 0.382)
    f["fim_de_hora"] = t["end_of_hour"]
    f["vela_gigante_recente"] = t["big_candle_recent"]
    f["janela_noticia_US"] = t["ny_news_window"] | t["fomc_window"]
    f["noticia_alta_FF"] = t["news_high"]
    f["noticia_media_FF"] = t["news_medium"]
    f["abertura_londres"] = t["london_open_30"]
    f["abertura_ny"] = t["ny_open_30"]
    f["overlap_lon_ny"] = t["overlap_lon_ny"]
    f["sessao_asia"] = t["asia_session"]
    f["vol_alta_atr>1.5"] = t["atr_pct"] > 1.5
    f["vol_baixa_atr<0.7"] = t["atr_pct"] < 0.7
    f["adx>25_tendencia"] = t["adx"] > 25
    f["fim_de_semana"] = t["weekday"] >= 5
    return f


def collect_trades() -> pd.DataFrame:
    symbols = sorted(p.stem for p in DATA.glob("*.json") if not p.stem.startswith(("_", "test")))
    rows = []
    for s in symbols:
        df = build(s)
        for name, fn in STRATEGIES.items():
            sig = fn(df)
            t = df[sig != 0].copy()
            t["dir"] = sig[sig != 0]
            t["strategy"] = name
            rows.append(t)
        print(f"{s}: {len(df)} barras", file=sys.stderr)
    trades = pd.concat(rows)
    trades = trades[trades["next_move"] != 0]  # empate = devolução, fora da conta
    trades["win"] = (trades["next_move"] == trades["dir"]).astype(int)
    # índice (timestamp) se repete entre ativos/estratégias: concat posicional, nunca join
    feats = trade_features(trades).add_prefix("f_")
    return pd.concat([trades, feats.set_axis(trades.index)], axis=1)


def ev(wins: pd.Series, payout: pd.Series) -> float:
    """Retorno esperado por R$1 apostado."""
    return float((wins * payout - (1 - wins)).mean()) if len(wins) else float("nan")


def summary(t: pd.DataFrame) -> str:
    if len(t) == 0:
        return "n=0"
    return f"n={len(t)} · acerto {t['win'].mean():.1%} · EV {ev(t['win'], t['payout']):+.3f}"


def split(t: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    cut = t.index.sort_values()[int(len(t) * TRAIN_FRACTION)]
    return t[t.index < cut], t[t.index >= cut]


def factor_table(train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    base_tr, base_te = train["win"].mean(), test["win"].mean()
    rows = []
    for col in [c for c in train.columns if c.startswith("f_")]:
        for val in (True, False):
            tr, te = train[train[col] == val], test[test[col] == val]
            if len(tr) < MIN_SAMPLES:
                continue
            rows.append({
                "fator": f"{col[2:]}={'S' if val else 'N'}",
                "n_treino": len(tr), "lift_treino": tr["win"].mean() - base_tr,
                "n_teste": len(te), "lift_teste": te["win"].mean() - base_te if len(te) else np.nan,
            })
    out = pd.DataFrame(rows)
    out["consistente"] = np.sign(out["lift_treino"]) == np.sign(out["lift_teste"])
    return out.sort_values("lift_treino", ascending=False)


def bucket_table(train: pd.DataFrame, test: pd.DataFrame, col: str) -> pd.DataFrame:
    g = lambda d: d.groupby(col)["win"].agg(["mean", "size"])  # noqa: E731
    out = g(train).join(g(test), lsuffix="_treino", rsuffix="_teste")
    return out[out["size_treino"] >= MIN_SAMPLES // 3]


def fit_score(train: pd.DataFrame, factors: pd.DataFrame) -> dict[str, float]:
    """Peso = lift em pontos percentuais (treino), só de fatores consistentes treino/teste-free:
    usa metade do treino para estimar e outra metade para confirmar o sinal (evita usar o teste)."""
    a, b = train.iloc[: len(train) // 2], train.iloc[len(train) // 2:]
    weights = {}
    for col in [c for c in train.columns if c.startswith("f_")]:
        la = a.loc[a[col], "win"].mean() - a["win"].mean()
        lb = b.loc[b[col], "win"].mean() - b["win"].mean()
        if a[col].sum() >= MIN_SAMPLES / 2 and b[col].sum() >= MIN_SAMPLES / 2 and np.sign(la) == np.sign(lb):
            weights[col] = round(100 * (la + lb) / 2, 2)
    return weights


def apply_score(t: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    return sum(t[c].astype(float) * w for c, w in weights.items()) if weights else pd.Series(0.0, t.index)


def fmt(df: pd.DataFrame) -> str:
    return df.to_markdown(floatfmt=".3f")


def main() -> None:
    trades = collect_trades().sort_index()
    out = ["# Backtest Hezilex — M5, expiração 5 min", "",
           f"Período: {trades.index.min():%Y-%m-%d} → {trades.index.max():%Y-%m-%d} · "
           f"treino {TRAIN_FRACTION:.0%} inicial / teste {1 - TRAIN_FRACTION:.0%} final (fora da amostra)",
           "Empate = devolução (excluído). Breakeven ≈ 54,1% (payout 85%) / 54,9% (82%).", ""]

    for name in STRATEGIES:
        t = trades[trades["strategy"] == name]
        train, test = split(t)
        out += [f"## {name}", f"- Total: {summary(t)}", f"- Treino: {summary(train)}", f"- Teste: {summary(test)}", ""]

        factors = factor_table(train, test)
        out += ["### Fatores (lift sobre a taxa base)", fmt(factors.set_index("fator")), ""]

        out += ["### Por hora (UTC)", fmt(bucket_table(train, test, "hour_utc")), ""]
        wd = bucket_table(train, test, "weekday")
        wd.index = [WEEKDAYS[i] for i in wd.index]
        out += ["### Por dia da semana", fmt(wd), ""]
        out += ["### Por ativo", fmt(bucket_table(train, test, "symbol")), ""]

        weights = fit_score(train, factors)
        test = test.assign(score=apply_score(test, weights))
        out += ["### Score (pesos aprendidos no treino, avaliado no teste)",
                "Pesos: " + ", ".join(f"`{k[2:]}` {v:+.1f}" for k, v in sorted(weights.items(), key=lambda x: -x[1])), ""]
        lines = []
        for q in (0.0, 0.5, 0.75, 0.9):
            thr = test["score"].quantile(q)
            lines.append(f"| score ≥ p{int(q * 100)} ({thr:+.1f}) | {summary(test[test['score'] >= thr])} |")
        out += ["| corte | resultado no teste |", "|---|---|", *lines, ""]

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "backtest.md").write_text("\n".join(out), encoding="utf-8")
    trades.to_parquet(REPORTS / "trades.parquet")
    print("ok -> reports/backtest.md", file=sys.stderr)


if __name__ == "__main__":
    main()
