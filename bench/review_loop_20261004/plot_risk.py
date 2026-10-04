"""Reproducible figure of the frozen R1/R3 risk-return results."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
r1 = json.loads((HERE / "r1_results.json").read_text())["results"]
r3 = json.loads((HERE / "r3_results.json").read_text())
colors = {"R2b": "#747c87", "R2b_vol10": "#276b97", "daily_budget": "#c56c46"}
labels = {"R2b": "Original R2b", "R2b_vol10": "R1: 10% volatility target", "daily_budget": "R3: daily loss budget"}
fig, ax = plt.subplots(figsize=(8, 5), layout="constrained")
for window, marker in (("early_reused", "o"), ("recent_reused", "s")):
    for name in colors:
        result = r3[f"{window}_cost0.0012"] if name == "daily_budget" else r1[f"{window}_cost0.0012_{name}"]
        point = (abs(result["maxDD_pct"]), result["CAGR_pct"])
        ax.scatter(*point, s=85, color=colors[name], marker=marker,
                   label=labels[name] if window == "early_reused" else None)
        offset = (5, -12) if name == "daily_budget" and window == "early_reused" else (5, 6)
        ax.annotate("2018-20" if window == "early_reused" else "2021-26", point,
                    xytext=offset, textcoords="offset points", fontsize=9)
ax.axvline(5, color="#ae3c3c", linestyle="--", linewidth=1)
ax.axhline(12, color="#ae3c3c", linestyle="--", linewidth=1)
ax.set(xlim=(0, 45), ylim=(-5, 60), xlabel="Maximum daily-close marked drawdown (%)",
       ylabel="Net annualized return (%)", title="Return and loss budget: fixed-rule comparisons")
ax.grid(alpha=.18)
ax.legend(loc="lower right", fontsize=9)
fig.text(.01, .005, "Reused history; fee 0.10% + slippage allowance 0.02% per side. Daily drawdown omits intraday lows.",
         fontsize=8, color="#606770")
fig.savefig(HERE / "r1_r3_risk_return.png", dpi=170)
