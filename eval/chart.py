"""Render the retrieval ablation chart from eval/results.json -> eval/ablation.png.

Grouped horizontal bars: correct recall in the top 3, per retrieval signal, children's vs mixed run.
The full numbers (and the table view) are in eval/results.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
SERIES = {"children": ("#2a78d6", "Children's products"), "mixed": ("#eb6834", "Other CPSC categories")}
STAGES = [("image_top3", "Image only (SigLIP 2)"), ("text_top3", "Text only (Gemma read)"),
          ("fused_top3", "Fused (image + text + exact)")]


def main(src: Path = Path("eval/results.json"), out: Path = Path("eval/ablation.png")) -> None:
    res = json.loads(src.read_text())["runs"]
    runs = [r for r in SERIES if r in res]
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    bar_h, gap = 0.3, 0.04  # bars stay thin; the band's leftover is air
    for i, (key, _) in enumerate(STAGES):
        for j, run in enumerate(runs):
            v = res[run][key] or 0
            y = len(STAGES) - 1 - i + (0.5 - j) * (bar_h + gap) - (bar_h + gap) / 2 + bar_h / 2
            color = SERIES[run][0]
            # 4px-ish rounded data-end, square at the baseline: a rounded box plus a square patch at x=0
            ax.add_patch(FancyBboxPatch((0, y - bar_h / 2), v, bar_h, boxstyle="round,pad=0,rounding_size=0.012",
                                        mutation_aspect=8, linewidth=0, facecolor=color))
            ax.add_patch(plt.Rectangle((0, y - bar_h / 2), min(v, 0.02), bar_h, linewidth=0, facecolor=color))
            ax.text(v + 0.012, y, f"{v:.0%}", va="center", ha="left", fontsize=9, color=INK)
    ax.set_yticks(range(len(STAGES)))
    ax.set_yticklabels([s[1] for s in reversed(STAGES)], fontsize=9.5, color=INK)
    ax.set_xlim(0, 1.0)
    ax.set_ylim(-0.6, len(STAGES) - 0.4)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=8.5, color=INK2)
    ax.xaxis.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    n = {r: res[r]["n_pos"] for r in runs}
    ax.set_title("Correct recall in the top 3 results", loc="left", fontsize=12, color=INK, pad=22,
                 fontweight="semibold")
    ax.text(0, 1.02, "Held-out CPSC recall photos with simulated phone-photo degradation; "
            + ", ".join(f"{SERIES[r][1].lower()} n={n[r]}" for r in runs),
            transform=ax.transAxes, fontsize=8, color=INK2)
    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=SERIES[r][0]) for r in runs]
    ax.legend(handles, [SERIES[r][1] for r in runs], loc="lower right", frameon=False, fontsize=8.5,
              labelcolor=INK)
    fig.tight_layout()
    fig.savefig(out, facecolor=SURFACE)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
