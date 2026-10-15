"""Evaluation: retrieval ablation, shortlist accuracy and verdicts (CONFIRMED / POSSIBLE MATCH / NO MATCH).

    uv run python -m eval.dataset            # build the test queries (once)
    uv run python -m eval.dataset --dev      # build the dev (tuning) split
    uv run python -m eval.run --dev [--verifier-experiment]   # tune on dev
    uv run python -m eval.run                # the test run, once, after rules are frozen
    uv run python -m eval.run --report       # rebuild the tables from saved rows

Per query, Gemma reads the photo once; the read is reused by the image/text/fused ablation so it compares
retrieval signals, not two different reads. Every query hides its held-out image and all copies of it from
the image index (see eval/dataset.py). Each row stores the gate inputs (closest-photo similarity, text-led,
code hits), so eval/gate.py can choose the POSSIBLE MATCH gate on dev rows without re-running anything.

Outputs (committed): eval/results.md, eval/results.json. Raw per-query rows: eval/work/rows.jsonl.
Earlier runs with the old LIKELY/UNSURE verdicts: eval/results_run1.md, eval/results_run2.md.
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

from eval.dataset import NEG_DEV_MANIFEST, NEG_DIR, NEG_MANIFEST, WORK
from index.models import load_image
from read.gemma import VISION_MODEL, OllamaVision, read_item
from retrieve.search import retrieve
from verify.pipeline import SHORTLIST_K
from verify.verdict import POSSIBLE_IF_TEXT_LED, POSSIBLE_IMAGE_SIM, Verdict, decide

ROWS = WORK / "rows.jsonl"
OUT_MD = Path("eval/results.md")
OUT_JSON = Path("eval/results.json")


def verifier_promote(img: Any, item: Any, exclude: list[str], vision: OllamaVision) -> dict[str, Any]:
    """Dev-only experiment: the Gemma verifier picks one of the fused top 5; the pick moves to the front."""
    from ingest.cpsc import DB_PATH, connect
    from verify.gemma import choose, recall_context
    from verify.pipeline import pick_recall_image

    res = retrieve(img, mode="fused", k=5, exclude_keys=exclude, read=item, vision=vision)
    conn = connect(DB_PATH)
    cands = []
    for c in res.candidates:
        ctx = recall_context(conn, c.recall_id)
        p = pick_recall_image(c.best_image, ctx["images"], set(exclude))
        if p:
            cands.append((c.recall_id, p, ctx))
    t = time.perf_counter()
    ans = choose(vision, img, [(p, ctx) for _, p, ctx in cands], item)
    pick = cands[ans["index"]][0] if ans["index"] is not None else None
    ids = [rid for rid, _, _ in cands]
    promoted = ([pick] + [r for r in ids if r != pick]) if pick else ids
    return {"verifier_pick": pick, "verifier_confidence": ans.get("confidence"), "verifier_ids": promoted[:SHORTLIST_K],
            "verifier_s": round(time.perf_counter() - t, 2)}


def run_query(q: dict[str, Any], vision: OllamaVision, experiment: bool = False) -> dict[str, Any]:
    img = load_image(q["path"])
    t = time.perf_counter()
    item = read_item(img, vision)
    read_s = time.perf_counter() - t
    row: dict[str, Any] = {k: q[k] for k in ("path", "label", "recall_id", "category", "run", "source")}
    row["read"] = item.to_dict()
    row["read_s"] = round(read_s, 2)
    for mode in ("image", "text"):
        res = retrieve(img, mode=mode, k=10, exclude_keys=q["exclude"], read=item, vision=vision)
        row[f"{mode}_ids"] = [c.recall_id for c in res.candidates]
    # The scan as the app runs it: fused retrieval, then the verdict on the top-3 shortlist. (k=10 only so the
    # ablation can report top-1/top-3 from the same call; the shortlist is the first 3.)
    t = time.perf_counter()
    res = retrieve(img, mode="fused", k=10, exclude_keys=q["exclude"], read=item, vision=vision)
    row["fused_ids"] = [c.recall_id for c in res.candidates]
    row["text_led"] = res.text_led
    row["shortlist"] = [{"recall_id": c.recall_id, "exact": c.exact, "image_score": c.image_score}
                        for c in res.candidates[:SHORTLIST_K]]
    v, idx = decide([c["exact"] for c in row["shortlist"]], [c["image_score"] for c in row["shortlist"]],
                    res.text_led)
    row["scan_s"] = round(time.perf_counter() - t + read_s, 2)  # what a user waits for: read + retrieve + decide
    row["verdict"] = v.value
    row["confirmed_id"] = row["shortlist"][idx]["recall_id"] if v == Verdict.CONFIRMED else None
    if experiment:
        row.update(verifier_promote(img, item, q["exclude"], vision))
    return row


def rank(ids: list[int], target: int | None) -> int:
    return ids.index(target) + 1 if target in ids else 99


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [r for r in rows if r["label"] == "pos"]
    neg = [r for r in rows if r["label"] == "neg"]
    out: dict[str, Any] = {"n_pos": len(pos), "n_neg": len(neg)}
    for mode in ("image", "text", "fused"):
        rk = [rank(r[f"{mode}_ids"], r["recall_id"]) for r in pos]
        out[f"{mode}_top1"] = sum(x <= 1 for x in rk) / len(rk) if rk else None
        out[f"{mode}_top3"] = sum(x <= 3 for x in rk) / len(rk) if rk else None
    conf = [r for r in rows if r["verdict"] == Verdict.CONFIRMED.value]
    out["confirmed_n"] = len(conf)
    out["confirmed_right"] = sum(r["label"] == "pos" and r["confirmed_id"] == r["recall_id"] for r in conf)
    out["confirmed_precision"] = out["confirmed_right"] / len(conf) if conf else None

    def shown(r: dict[str, Any]) -> bool:  # the user is shown the right recall: in the shortlist, or confirmed
        return r["verdict"] != Verdict.NO_MATCH.value and r["recall_id"] in [c["recall_id"] for c in r["shortlist"]]

    out["shown_right"] = sum(shown(r) for r in pos) / len(pos) if pos else None
    out["possible_rate_pos"] = sum(r["verdict"] == Verdict.POSSIBLE.value for r in pos) / len(pos) if pos else None
    out["no_match_rate_pos"] = sum(r["verdict"] == Verdict.NO_MATCH.value for r in pos) / len(pos) if pos else None
    out["false_alarm_rate"] = sum(r["verdict"] == Verdict.CONFIRMED.value for r in neg) / len(neg) if neg else None
    out["possible_rate_neg"] = sum(r["verdict"] == Verdict.POSSIBLE.value for r in neg) / len(neg) if neg else None
    out["code_read_matched_pos"] = (
        sum(any(e.startswith(("model:", "upc:")) for c in r["shortlist"] for e in c["exact"]) for r in pos) / len(pos)
        if pos else None)
    lat = [r["scan_s"] for r in rows]
    out["latency_median_s"] = statistics.median(lat) if lat else None
    out["latency_p90_s"] = sorted(lat)[int(0.9 * (len(lat) - 1))] if lat else None
    if any("verifier_ids" in r for r in pos):
        vr = [r for r in pos if "verifier_ids" in r]
        out["verifier_top3"] = sum(r["recall_id"] in r["verifier_ids"] for r in vr) / len(vr)
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
    head = "| | " + " | ".join(names) + " |"
    sep = "|---|" + "---|" * len(names)
    L = [f"# ClearToList evaluation results{meta.get('title', '')}", "",
         f"Generated {meta['generated']} on {meta['gpu']}. Vision model `{meta['vision_model']}`, image model "
         f"`{meta['image_model']}`, text model `{meta['text_model']}`. POSSIBLE MATCH gate (chosen on the dev "
         f"split): closest shortlisted recall photo has SigLIP 2 cosine >= {meta['gate_image_sim']}"
         + (", or the photo is a label whose words led the search" if meta["gate_text_led"] else "") + ".", "",
         "Positives are held-out CPSC recall photos with simulated phone-photo degradation; the held-out photo "
         "and every copy of it are removed from the index for that query. Negatives are licensed same-category "
         "product photos from Wikimedia Commons (see the negatives manifest; audited entries marked `excluded` "
         "are left out).", "",
         "## Retrieval (correct recall in top-k)", "", head, sep]
    for label, key in [("positives / negatives", None), ("image only, top-1", "image_top1"),
                       ("image only, top-3", "image_top3"), ("text only (Gemma read), top-1", "text_top1"),
                       ("text only (Gemma read), top-3", "text_top3"), ("fused, top-1", "fused_top1"),
                       ("**fused, top-3 (the shortlist)**", "fused_top3")]:
        if key is None:
            L.append(f"| {label} | " + " | ".join(f"{summ[n]['n_pos']} / {summ[n]['n_neg']}" for n in names) + " |")
        else:
            L.append(f"| {label} | " + " | ".join(f(summ[n][key]) for n in names) + " |")
    if any("verifier_top3" in summ[n] for n in names):
        L.append("| verifier promotes 1 of top 5, top-3 | " + " | ".join(
            f(summ[n].get("verifier_top3")) for n in names) + " |")
    L += ["", "## Verdicts", "", head, sep]
    for label, key in [("CONFIRMED count", "confirmed_n"), ("CONFIRMED right", "confirmed_right"),
                       ("CONFIRMED precision", "confirmed_precision"),
                       ("false-alarm rate on negatives (CONFIRMED)", "false_alarm_rate"),
                       ("POSSIBLE MATCH rate on negatives", "possible_rate_neg"),
                       ("positives shown the right recall (CONFIRMED or in a POSSIBLE MATCH shortlist)", "shown_right"),
                       ("POSSIBLE MATCH rate on positives", "possible_rate_pos"),
                       ("NO MATCH FOUND rate on positives", "no_match_rate_pos"),
                       ("model number / UPC read and matched", "code_read_matched_pos"),
                       ("latency per scan, median (s)", "latency_median_s"),
                       ("latency per scan, p90 (s)", "latency_p90_s")]:
        L.append(f"| {label} | " + " | ".join(
            f"{summ[n][key]:.1f}" if key.startswith("latency") and summ[n][key] is not None else f(summ[n][key])
            for n in names) + " |")
    L += ["", "## Per category", "",
          "| run / category | pos | neg | image top-3 | shortlist (fused top-3) | shown right | "
          "POSSIBLE on negatives | CONFIRMED on negatives |", "|---|---|---|---|---|---|---|---|"]
    for k, s in cats.items():
        L.append(f"| {k} | {s['n_pos']} | {s['n_neg']} | {f(s['image_top3'])} | {f(s['fused_top3'])} | "
                 f"{f(s['shown_right'])} | {f(s['possible_rate_neg'])} | {f(s['false_alarm_rate'])} |")
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
    ap.add_argument("--verifier-experiment", action="store_true",
                    help="dev only: also test whether the verifier improves the top-3 shortlist")
    a = ap.parse_args()
    global ROWS, OUT_MD, OUT_JSON
    if a.dev:
        ROWS, OUT_MD, OUT_JSON = WORK / "dev_rows.jsonl", WORK / "dev_results.md", WORK / "dev_results.json"
    elif a.verifier_experiment:
        sys.exit("--verifier-experiment is for the dev split only")
    queries = json.loads((WORK / ("dev_queries.json" if a.dev else "queries.json")).read_text())
    queries = [q for q in queries if q["run"] in a.runs.split(",")]
    # Negatives removed by the audit ("excluded" in the manifest) never count.
    dropped = {str(NEG_DIR / it["file"]) for mf in (NEG_MANIFEST, NEG_DEV_MANIFEST) if mf.exists()
               for it in json.loads(mf.read_text())["items"] if it.get("excluded")}
    queries = [q for q in queries if q["path"] not in dropped]
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
                    r = run_query(q, vision, a.verifier_experiment)
                except Exception as e:  # keep going; report the failure count
                    print(f"[{i + 1}/{len(queries)}] {q['path']}: ERROR {e}", file=sys.stderr)
                    continue
                done[q["path"]] = r
                fh.write(json.dumps(r) + "\n")
                fh.flush()
                print(f"[{i + 1}/{len(queries)}] {q['run']}/{q['category']} {q['label']} -> {r['verdict']} "
                      f"(shortlist rank {rank(r['fused_ids'], r['recall_id']) if r['recall_id'] else '-'}, "
                      f"{r['scan_s']}s)", file=sys.stderr)
    want = {q["path"] for q in queries}
    rows = [r for p, r in done.items() if p in want]
    import torch

    from index.models import DEFAULT_IMAGE_MODEL, DEFAULT_TEXT_MODEL

    meta = {"generated": time.strftime("%Y-%m-%d"), "vision_model": VISION_MODEL, "image_model": DEFAULT_IMAGE_MODEL,
            "text_model": DEFAULT_TEXT_MODEL,
            # Benchmark hardware is described generically on purpose (no device names in published results).
            "gpu": f"a {round(torch.cuda.get_device_properties(0).total_memory / 2**30)} GB consumer GPU"
            if torch.cuda.is_available() else "CPU",
            "gate_image_sim": POSSIBLE_IMAGE_SIM, "gate_text_led": POSSIBLE_IF_TEXT_LED,
            "n_queries": len(queries), "n_completed": len(rows),
            "title": " (dev split)" if a.dev else ": Run 3"}
    report(rows, meta)


if __name__ == "__main__":
    main()
