# -*- coding: utf-8 -*-
"""
治理策略对照驱动：对 5 种 intervention_mode × REPS 次重复，依次跑模拟，
记录每次运行的 run_dir 与 mode 到 governance_manifest.json（供 governance_analysis.py 分析）。

用法：python driver_governance.py [REPS]
"""
import subprocess, json, os, sys, glob, time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.abspath(os.path.join(HERE, "..", ".."))
ROOT = os.path.abspath(os.path.join(SRC, ".."))
ENV_DATA = os.path.join(HERE, "env_data.json")
RUNS = os.path.join(HERE, "runs")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
CFG = os.path.join(ROOT, "config", "rumor_config.json")
MODEL_CFG = os.path.join(ROOT, "config", "model_config.json")

MODES = ["none", "delete", "command_debunk", "evidence_debunk", "boost_visibility"]
REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 3


def set_mode(mode: str):
    d = json.load(open(ENV_DATA, encoding="utf-8"))
    d["sim"]["intervention_mode"] = mode
    with open(ENV_DATA, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=4)


def run_once() -> str:
    before = set(glob.glob(os.path.join(RUNS, "2*")))
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        [PY, "main.py", "--config", CFG, "--model_config", MODEL_CFG, "--mode", "single"],
        cwd=SRC, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    after = set(glob.glob(os.path.join(RUNS, "2*")))
    new = sorted(after - before)
    return new[-1] if new else ""


def main():
    manifest = []
    total = len(MODES) * REPS
    i = 0
    for mode in MODES:
        set_mode(mode)
        for rep in range(REPS):
            i += 1
            t0 = time.time()
            rd = run_once()
            manifest.append({"mode": mode, "rep": rep, "run_dir": rd})
            print(f"[{i}/{total}] mode={mode} rep={rep} -> {os.path.basename(rd)} ({time.time()-t0:.0f}s)", flush=True)
    set_mode("none")  # 还原默认
    out = os.path.join(HERE, "governance_manifest.json")
    json.dump(manifest, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"manifest saved: {out}")


if __name__ == "__main__":
    main()
