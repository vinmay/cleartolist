"""Display policy: show children's products their three closest recalls ("closest recalls to check") instead of a
bare NO MATCH FOUND. Decided on the dev split; Run 3b applies it once to the saved Run 3 test rows.

    uv run python -m eval.display_policy      # writes eval/display_policy.md

Rule fixed before looking: adopt only if "right recall shown to the user" rises by at least 15 points on dev. Both
runs include the post-test brand check on CONFIRMED (eval/confirm_fix.py). Only the verdict and display steps are
recomputed (same Gemma reads, same shortlists), so this measures the policy, not new retrieval.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from eval.dataset import WORK
from ingest.cpsc import DB_PATH
from retrieve.exact import brand_conflict
from verify.verdict import Verdict, decide, has_code, show_closest

OUT = Path("eval/display_policy.md")
_CAT: dict[int, str] = {}


def outcome(r: dict, policy: bool) -> tuple[str, bool, bool]:
    """(verdict, anything shown to the user, right recall shown)."""
    sl = r["shortlist"]
    bc = [has_code(c["exact"]) and brand_conflict(str(DB_PATH), r["read"]["brand"], c["recall_id"]) for c in sl]
    v, i = decide([c["exact"] for c in sl], [c["image_score"] for c in sl], r["text_led"], brand_conflict=bc)
    ids = [c["recall_id"] for c in sl]
    if v == Verdict.CONFIRMED:
        return v.value, True, ids[i] == r["recall_id"]
    if v == Verdict.POSSIBLE:
        return v.value, True, r["recall_id"] in ids
    closest = policy and show_closest(v, bool(r["read"].get("child_product")), _CAT.get(ids[0]) if ids else None)
    return v.value, closest, closest and r["recall_id"] in ids


def stats(rows: list[dict], policy: bool) -> dict[str, dict[str, float | None]]:
    out = {}
    for run in ("children", "mixed", "label"):
        pos = [r for r in rows if r["run"] == run and r["label"] == "pos"]
        neg = [r for r in rows if r["run"] == run and r["label"] == "neg"]
        if not pos:
            continue
        out[run] = {
            "pos": len(pos), "neg": len(neg),
            "right_shown": sum(outcome(r, policy)[2] for r in pos) / len(pos),
            "lookalikes_shown": sum(outcome(r, policy)[1] for r in neg) / len(neg) if neg else None,
            "lookalikes_alarm": sum(outcome(r, policy)[0] == "CONFIRMED" for r in neg) / len(neg) if neg else None,
        }
    return out


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}"


def section(title: str, cur: dict, new: dict) -> list[str]:
    L = [f"### {title}", "", "| run | positives / negatives | right recall shown: current → new | "
         "lookalikes shown recalls (any kind): current → new | false alarms (CONFIRMED on lookalikes) |",
         "|---|---|---|---|---|"]
    for run in cur:
        c, n = cur[run], new[run]
        L.append(f"| {run} | {c['pos']} / {c['neg']} | {pct(c['right_shown'])} → {pct(n['right_shown'])} | "
                 f"{pct(c['lookalikes_shown'])} → {pct(n['lookalikes_shown'])} | {pct(n['lookalikes_alarm'])} |")
    return L + [""]


def main() -> None:
    _CAT.update(sqlite3.connect(DB_PATH).execute("SELECT recall_id, category FROM recalls").fetchall())
    dev = [json.loads(x) for x in (WORK / "dev_rows.jsonl").read_text().splitlines()]
    test = [json.loads(x) for x in (WORK / "rows.jsonl").read_text().splitlines()]
    dc, dn = stats(dev, False), stats(dev, True)
    gain = dn["children"]["right_shown"] - dc["children"]["right_shown"]
    adopt = gain >= 0.15
    L = ["# Display policy: closest recalls to check (Run 3b)", "",
         "Policy: when neither CONFIRMED nor POSSIBLE MATCH triggers and the item is a children's product (Gemma "
         "marks it as one, or its closest recall is a children's recall), show the three closest recalls in grey as "
         "\"closest recalls to check\": suggestions, not matches, with no alarm. The verdict stays NO MATCH FOUND.", "",
         "Adoption rule, fixed before looking: right recall shown must rise by at least 15 points on dev "
         f"(children's products). On dev it rose {gain * 100:.0f} points, so the policy is "
         f"{'adopted' if adopt else 'not adopted'}.", ""]
    L += section("Dev split (decision)", dc, dn)
    if adopt:
        L += section("Test: Run 3 (current policy) → Run 3b (new policy), computed once from the saved Run 3 rows",
                     stats(test, False), stats(test, True))
    L += ["\"Lookalikes shown recalls\" counts any recall shown for a negative (CONFIRMED, POSSIBLE MATCH or closest "
          "recalls). Closest recalls are not an alarm: the page says they are suggestions, not matches.", ""]
    OUT.write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
