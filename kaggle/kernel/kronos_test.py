# Kronos (NeoQuasar) — prevê 3 velas M5 à frente e mede acerto de direção no período de teste do HZ.
import subprocess, sys, os, glob, time
subprocess.run("git clone -q https://github.com/shiyu-coder/Kronos.git /kaggle/working/Kronos", shell=True, check=True)
subprocess.run(f"{sys.executable} -m pip install -q einops huggingface_hub safetensors", shell=True)
sys.path.insert(0, "/kaggle/working/Kronos")
import numpy as np, pandas as pd, torch
from model import Kronos, KronosTokenizer, KronosPredictor

tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
mdl = Kronos.from_pretrained("NeoQuasar/Kronos-small")
pred = KronosPredictor(mdl, tok, device="cuda:0", max_context=512)
CTX, H, N, S = 400, 3, 1200, 5
rng = np.random.default_rng(7)
out = []
for f in sorted(glob.glob("/kaggle/input/**/*.csv", recursive=True)):
    print("arquivo", f, flush=True)
    sym = os.path.basename(f)[:-4]
    df = pd.read_csv(f)
    df["ts"] = pd.to_datetime(df["t"], unit="s")
    df["amount"] = df["volume"] * df["close"]
    idx = np.sort(rng.choice(np.arange(CTX + 900, len(df) - H), N, replace=False))
    t0 = time.time()
    for k, i in enumerate(idx):
        x = df.iloc[i - CTX + 1:i + 1]
        fut = df["ts"].iloc[i + 1:i + 1 + H]
        ups = []
        for _ in range(S):
            p = pred.predict(df=x[["open", "high", "low", "close", "volume", "amount"]].reset_index(drop=True),
                             x_timestamp=x["ts"].reset_index(drop=True), y_timestamp=fut.reset_index(drop=True),
                             pred_len=H, T=1.0, top_p=0.9, sample_count=1, verbose=False)
            ups.append(p["close"].iloc[-1] > x["close"].iloc[-1])
        out.append(dict(t=int(df["t"].iloc[i]), sym=sym, up_frac=float(np.mean(ups)),
                        y=int(np.sign(df["close"].iloc[i + H] - df["close"].iloc[i]))))
        if k % 200 == 0:
            print(sym, k, f"{time.time() - t0:.0f}s", flush=True)
r = pd.DataFrame(out)
r.to_csv("/kaggle/working/kronos_pred.csv", index=False)
r = r[r.y != 0]
for th in (0.5, 0.6, 0.8, 1.0):
    k = (r.up_frac >= th) | (r.up_frac <= 1 - th) if th > .5 else r.up_frac != .5
    dr = np.where(r.up_frac > .5, 1, -1)
    print(f"concordancia>={th}: n={k.sum()} acerto={(dr[k] == r.y[k]).mean():.4f}")
