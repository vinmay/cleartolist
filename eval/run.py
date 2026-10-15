"""Full evaluation: retrieval ablation + verdict metrics, children's run and mixed run.

    uv run python -m eval.dataset          # build queries (once)
    uv run python -m eval.run              # run everything, resumable
    uv run python -m eval.run --report     # just rebuild the tables from saved results

Per query, Gemma reads the photo once; the read is reused by the text and fused modes so the
ablation compares retrieval signals, not two different reads. Every query hides its held-out
image and all copies of it from the image index (see eval/dataset.py).

Outputs (committed): eval/results.md, eval/results.json. Raw per-query rows: eval/work/rows.jsonl.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from eval.dataset import WORK
from index.models import load_image
from read.gemma import VISION_MODEL, OllamaVision, read_item
from retrieve.search import retrieve
from verify.pipeline import scan
from verify.verdict import Verdict

ROWS = WORK / "rows.jsonl"
OUT_MD = Path("eval/results.md")
OUT_JSON = Path("eval/results.json")
FLAG = {Verdict.CONFIRMED.value, Verdict.LIKELY.value}
VERIFY_K = 3  # verify the top 3 fused candidates


def run_query(q: dict[str, Any], vision: OllamaVision) -> dict[str, Any]:
    img = load_image(q["path"])
    t = time.perf_counter()
    item = read_item(img, vision)
    read_s = time.perf_counter() - t
    row: dict[str, Any] = {k: q[k] for k in ("path", "label", "recall_id", "category", "run", "source")}
    row["read"] = item.to_dict()
    row["read_s"] = round(read_s, 2)
    for mode in ("image", "text", "fused"):
        res = retrieve(img, mode=mode, k=10, exclude_keys=q["exclude"], read=item, vision=vision)
        ids = [c.recall_id for c in res.candidates]
        row[f"{mode}_ids"] = ids
        if mode == "fused":
            row["exact_fired"] = any(
                e.startswith(("model:", "upc:")) for c in res.candidates for e in c.exact
            )
    t = time.perf_counter()
    s = scan(img, mode="fused", verify=True, k=VERIFY_K, exclude_keys=q["exclude"], read=item, vision=vision)
    row["scan_s"] = round(time.perf_counter() - t + read_s, 2)
    row["verdict"] = s.verdict.value
    # matches[0] is the recall the verdict refers to (the verifier's pick or a code hit), if any
    refers = s.matches[0]["recall_id"] if s.matches and s.verdict != Verdict.NO_MATCH else None
    row["verdict_recall_id"] = refers
    row["verified_top_id"] = refers
    row["verifier"] = s.choice.__dict__ if s.choice else None
    row["candidates"] = [{"recall_id": m["recall_id"], "exact": m["exact"], "image_score": m["image_score"]}
                         for m in s.matches]
    return row


def rank(ids: list[int], target: int | None) -> int:
    return ids.index(target) + 1 if target in ids else 99


def pct(n: int, d: int) -> str:
    return f"{n / d:.0%}" if d else "n/a"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [r for r in rows if r["label"] == "pos"]
    neg = [r for r in rows if r["label"] == "neg"]
    out: dict[str, Any] = {"n_pos": len(pos), "n_neg": len(neg)}
    for mode in ("image", "text", "fused"):
        rk = [rank(r[f"{mode}_ids"], r["recall_id"]) for r in pos]
        out[f"{mode}_top1"] = sum(x <= 1 for x in rk) / len(rk) if rk else None
        out[f"{mode}_top3"] = sum(x <= 3 for x in rk) / len(rk) if rk else None
    # fused + verifier: the strongest verified candidate is the answer
    out["verified_top1"] = (sum(r["verified_top_id"] == r["recall_id"] for r in pos) / len(pos)) if pos else None
    for v in ("CONFIRMED", "LIKELY"):
        flagged = [r for r in rows if r["verdict"] == v]
        correct = [r for r in flagged if r["label"] == "pos" and r["verdict_recall_id"] == r["recall_id"]]
        out[f"{v.lower()}_n"] = len(flagged)
        out[f"{v.lower()}_precision"] = len(correct) / len(flagged) if flagged else None
    hits = [r for r in pos if r["verdict"] in FLAG and r["verdict_recall_id"] == r["recall_id"]]
    out["flag_recall"] = len(hits) / len(pos) if pos else None
    out["false_alarm_rate"] = sum(r["verdict"] in FLAG for r in neg) / len(neg) if neg else None
    out["unsure_rate_pos"] = sum(r["verdict"] == "UNSURE" for r in pos) / len(pos) if pos else None
    out["unsure_rate_neg"] = sum(r["verdict"] == "UNSURE" for r in neg) / len(neg) if neg else None
    out["exact_fired_pos"] = sum(r["exact_fired"] for r in pos) / len(pos) if pos else None
    lat = [r["scan_s"] for r in rows]
    out["latency_median_s"] = statistics.median(lat) if lat else None
    out["latency_p90_s"] = sorted(lat)[int(0.9 * (len(lat) - 1))] if lat else None
    return out


def f(x: Any) -> str:
    if x is None:
        return "n/a"
    return f"{x:.0%}" if isinstance(x, float) and x <= 1 else str(x)


def report(rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    runs = {run: [r for r in rows if r["run"] == run] for run in ("children", "mixed", "label", "owner")}
    runs = {k: v for k, v in runs.items() if v}
    summ = {run: summarize(rs) for run, rs in runs.items()}
    cats: dict[str, dict[str, Any]] = {}
    for run, rs in runs.items():
        by: dict[str, list] = defaultdict(list)
        for r in rs:
            by[r["category"]].append(r)
        for cat, crs in sorted(by.items()):
            cats[f"{run}/{cat}"] = summarize(crs)

    names = list(summ)
    L = ["# ClearToList evaluation results", "",
         f"Generated {meta['generated']} on {meta['gpu']}. Vision model `{meta['vision_model']}`, image model "
         f"`{meta['image_model']}`, text model `{meta['text_model']}`.", "",
         "Positives are held-out CPSC recall photos with simulated phone-photo degradation; the held-out photo "
         "and every copy of it are removed from the index for that query. Negatives are licensed same-category "
         "product photos from Wikimedia Commons (see `eval/negatives_manifest.json`).", "",
         "## Retrieval ablation (correct recall in top-k)", "",
         "| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for label, key in [("positives / negatives", None), ("image only, top-1", "image_top1"),
                       ("image only, top-3", "image_top3"), ("text only (Gemma read), top-1", "text_top1"),
                       ("text only (Gemma read), top-3", "text_top3"), ("fused, top-1", "fused_top1"),
                       ("fused, top-3", "fused_top3"), ("fused + verifier, top-1", "verified_top1")]:
        if key is None:
            L.append(f"| {label} | " + " | ".join(f"{summ[n]['n_pos']} / {summ[n]['n_neg']}" for n in names) + " |")
        else:
            L.append(f"| {label} | " + " | ".join(f(summ[n][key]) for n in names) + " |")
    L += ["", "## Verdicts (fused + verifier)", "", "| | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for label, key in [("CONFIRMED precision", "confirmed_precision"), ("CONFIRMED count", "confirmed_n"),
                       ("LIKELY precision", "likely_precision"), ("LIKELY count", "likely_n"),
                       ("positives flagged with the right recall", "flag_recall"),
                       ("false-alarm rate on negatives (CONFIRMED or LIKELY)", "false_alarm_rate"),
                       ("UNSURE rate, positives", "unsure_rate_pos"), ("UNSURE rate, negatives", "unsure_rate_neg"),
                       ("model number / UPC read and matched", "exact_fired_pos"),
                       ("latency per scan, median (s)", "latency_median_s"),
                       ("latency per scan, p90 (s)", "latency_p90_s")]:
        L.append(f"| {label} | " + " | ".join(
            f"{summ[n][key]:.1f}" if key.startswith("latency") and summ[n][key] is not None else f(summ[n][key])
            for n in names) + " |")
    L += ["", "## Per category", "",
          "| run / category | pos | neg | image top-3 | fused top-3 | flagged right | false alarms |",
          "|---|---|---|---|---|---|---|"]
    for k, s in cats.items():
        L.append(f"| {k} | {s['n_pos']} | {s['n_neg']} | {f(s['image_top3'])} | {f(s['fused_top3'])} | "
                 f"{f(s['flag_recall'])} | {f(s['false_alarm_rate'])} |")
    L += ["", "Categories with fewer than 10 positives are indicative only.", ""]
    OUT_MD.write_text("\n".join(L))
    OUT_JSON.write_text(json.dumps({"meta": meta, "runs": summ, "categories": cats}, indent=1))
    print("\n".join(L))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--runs", default="children,mixed,label,owner")
    ap.add_argument("--dev", action="store_true", help="run on the tuning split; results are not written to eval/")
    a = ap.parse_args()
    global ROWS, OUT_MD, OUT_JSON
    if a.dev:
        ROWS, OUT_MD, OUT_JSON = WORK / "dev_rows.jsonl", WORK / "dev_results.md", WORK / "dev_results.json"
    queries = json.loads((WORK / ("dev_queries.json" if a.dev else "queries.json")).read_text())
    queries = [q for q in queries if q["run"] in a.runs.split(",")]
    if a.limit:
        queries = queries[: a.limit]
    done = {}
    if ROWS.exists():
        for line in ROWS.read_text().splitlines():
            r = json.loads(line)
            done[r["path"]] = r
    if not a.report:
        vision = OllamaVision()
        with ROWS.open("a") as fh:
            for i, q in enumerate(queries):
                if q["path"] in done:
                    continue
                try:
                    r = run_query(q, vision)
                except Exception as e:  # keep going; report the failure count
                    print(f"[{i + 1}/{len(queries)}] {q['path']}: ERROR {e}", file=sys.stderr)
                    continue
                done[q["path"]] = r
                fh.write(json.dumps(r) + "\n")
                fh.flush()
                print(f"[{i + 1}/{len(queries)}] {q['run']}/{q['category']} {q['label']} -> {r['verdict']} "
                      f"(fused rank {rank(r['fused_ids'], r['recall_id']) if r['recall_id'] else '-'}, "
                      f"{r['scan_s']}s)", file=sys.stderr)
    want = {q["path"] for q in queries}
    rows = [r for p, r in done.items() if p in want]
    import torch

    from index.models import DEFAULT_IMAGE_MODEL, DEFAULT_TEXT_MODEL

    meta = {"generated": time.strftime("%Y-%m-%d"), "vision_model": VISION_MODEL, "image_model": DEFAULT_IMAGE_MODEL,
            "text_model": DEFAULT_TEXT_MODEL,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
            "n_queries": len(queries), "n_completed": len(rows)}
    report(rows, meta)


if __name__ == "__main__":
    main()
