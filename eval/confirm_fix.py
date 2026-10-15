"""Post-test fix to CONFIRMED: a brand read off the item that contradicts the recall vetoes a code match.

    uv run python -m eval.confirm_fix      # writes eval/confirm_fix.md

Chosen on the dev split after Run 3 showed 2 of 4 wrong CONFIRMED verdicts in the mixed run. Only the verdict step is
recomputed from the saved rows (same reads, same shortlists); nothing else about Run 3 changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval.dataset import WORK
from ingest.cpsc import DB_PATH
from retrieve.exact import brand_conflict
from verify.verdict import Verdict, decide, has_code

OUT = Path("eval/confirm_fix.md")


def verdict(r: dict, veto: bool) -> tuple[str, int | None]:
    sl = r["shortlist"]
    bc = [veto and has_code(c["exact"]) and brand_conflict(str(DB_PATH), r["read"]["brand"], c["recall_id"])
          for c in sl]
    v, i = decide([c["exact"] for c in sl], [c["image_score"] for c in sl], r["text_led"], brand_conflict=bc)
    return v.value, (sl[i]["recall_id"] if v == Verdict.CONFIRMED else None)


def table(rows: list[dict], name: str) -> list[str]:
    runs = [x for x in ("children", "mixed", "label") if any(r["run"] == x for r in rows)]
    L = [f"### {name}", "", "| | " + " | ".join(f"{x} before | {x} after" for x in runs) + " |",
         "|---|" + "---|---|" * len(runs)]
    stats = {}
    for x in runs:
        rs = [r for r in rows if r["run"] == x]
        for veto in (False, True):
            out = [(r, *verdict(r, veto)) for r in rs]
            conf = [(r, v, c) for r, v, c in out if v == "CONFIRMED"]
            neg = [o for o in out if o[0]["label"] == "neg"]
            stats[x, veto] = (len(conf), sum(r["label"] == "pos" and c == r["recall_id"] for r, _, c in conf),
                              sum(v == "CONFIRMED" for _, v, _ in neg), len(neg))
    for label, k in (("CONFIRMED", 0), ("CONFIRMED right", 1), ("CONFIRMED on negatives", 2)):
        L.append(f"| {label} | " + " | ".join(f"{stats[x, False][k]} | {stats[x, True][k]}" for x in runs) + " |")
    L.append("")
    changed = [(r, verdict(r, False), verdict(r, True)) for r in rows if verdict(r, False) != verdict(r, True)]
    for r, b, a in changed:
        L.append(f"- {r['run']} {r['label']} (true recall {r['recall_id']}): {b[0]} (recall {b[1]}) -> {a[0]}; "
                 f"brand read: \"{r['read']['brand']}\"")
    L.append("")
    return L


def main() -> None:
    dev = [json.loads(x) for x in (WORK / "dev_rows.jsonl").read_text().splitlines()]
    test = [json.loads(x) for x in (WORK / "rows.jsonl").read_text().splitlines()]
    L = ["# Post-test fix: brand check on CONFIRMED", "",
         "Run 3 had 2 wrong CONFIRMED verdicts out of 4 in the mixed run. Causes: a short token on a battery label "
         "(\"EOS 100D\", a compatible camera) matched an unrelated recall's model number; and a model number "
         "(Intertex VP-33) that appears in two recalls of the same product line, so the code was right but the recall "
         "picked was the other one.", "",
         "Rule chosen on the dev split: when a brand is read off the item and the code-matched recall's manufacturer "
         "and title don't mention it, the code match doesn't confirm (the verdict falls back to POSSIBLE MATCH or "
         "NO MATCH FOUND by the photo gate). On dev it removed both wrong CONFIRMED verdicts and kept all correct "
         "ones. It was then applied to the saved Run 3 rows: only the verdict step is recomputed.", ""]
    L += table(dev, "Dev split (where the rule was chosen)") + table(test, "Test (Run 3 rows, recomputed once)")
    L += ["The shared-model-number case (VP-33) is not fixed by this rule: the item does carry a recalled model "
          "number, but the number belongs to two recalls. No rule for it could be validated on dev (no such case "
          "there).", ""]
    OUT.write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
