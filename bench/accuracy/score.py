"""Score AeroTrace's raw answers against the hand truth.

Usage: python3 -I bench/accuracy/score.py sample.json truth.json <raw-dir> > score.json

callers, exact: answers labelled `exact` vs direct (non-pointer) callers.
callers, all:   every depth-1 answer, any label, vs direct + pointer callers.
field users, exact: functions with certainty `exact` vs every function that reads or writes the field.
field users, all:   every listed function, any certainty, vs the same truth.
Readers and writers are also scored on their own (truth "rw" counts as both).
Precision and recall are totals over items (sum of matches / sum of sizes).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def fid(d: dict) -> str:
    return f"{d['file']}:{d['name']}"


def tally(rows: list[tuple[set, set]]) -> dict:
    hit = sum(len(t & h) for t, h in rows)
    nt = sum(len(t) for t, _ in rows)
    nh = sum(len(h) for _, h in rows)
    return {
        "matches": hit,
        "answers": nt,
        "truth": nh,
        "precision": round(hit / nt, 3) if nt else None,
        "recall": round(hit / nh, 3) if nh else None,
    }


def main() -> None:
    sample = json.loads(Path(sys.argv[1]).read_text())
    truth = json.loads(Path(sys.argv[2]).read_text())
    raw = Path(sys.argv[3])

    def load(x: dict) -> dict:
        safe = x["id"].replace("/", "_").replace(":", "__")
        return json.loads((raw / f"{x['repo']}-{safe}.json").read_text())

    rows: dict[str, list] = {}
    misses, false_alarms, items = [], [], []
    for x in sample["functions"]:
        rec, h = load(x), truth["functions"][x["id"]]
        res = [r for r in (rec["answer"] or {}).get("results", []) if r["distance"] == 1]
        t_exact = {fid(r) for r in res if r["confidence"] == "exact"}
        t_all = {fid(r) for r in res}
        h_exact = set(h["callers"])
        h_all = h_exact | set(h.get("pointer_callers", []))
        key = f"{x['repo']}/callers"
        rows.setdefault(key + "/exact", []).append((t_exact, h_exact))
        rows.setdefault(key + "/all", []).append((t_all, h_all))
        lines = {fid(r): r["lines"] for r in res}
        for m in sorted(h_all - t_all):
            misses.append({"item": x["id"], "kind": "callers", "missed": m})
        for f in sorted(t_all - h_all):
            label = next(r["confidence"] for r in res if fid(r) == f)
            false_alarms.append(
                {"item": x["id"], "kind": "callers", "answer": f, "label": label, "lines": lines[f]}
            )
        for f in sorted((t_exact - h_exact) & h_all):
            false_alarms.append(
                {
                    "item": x["id"],
                    "kind": "callers (exact only)",
                    "answer": f,
                    "note": "pointer caller labelled exact",
                }
            )
        items.append(
            {
                "id": x["id"],
                "total": (rec["answer"] or {}).get("total"),
                "omitted": (rec["answer"] or {}).get("omitted"),
            }
        )
    for x in sample["fields"]:
        rec, h = load(x), truth["fields"][x["id"]]
        uses = (rec["answer"] or {}).get("uses", [])
        t_exact = {fid(u["function"]) for u in uses if u["certainty"] == "exact"}
        t_all = {fid(u["function"]) for u in uses}
        t_r = {fid(u["function"]) for u in uses if u["reads"]}
        t_w = {fid(u["function"]) for u in uses if u["writes"]}
        users = h["users"]
        h_all = set(users)
        h_r = {f for f, k in users.items() if "r" in k}
        h_w = {f for f, k in users.items() if "w" in k or k == "addr"}
        key = f"{x['repo']}/fields"
        rows.setdefault(key + "/exact", []).append((t_exact, h_all))
        rows.setdefault(key + "/all", []).append((t_all, h_all))
        rows.setdefault(key + "/readers", []).append((t_r, h_r))
        rows.setdefault(key + "/writers", []).append((t_w, h_w))
        for m in sorted(h_all - t_all):
            misses.append({"item": x["id"], "kind": "field users", "missed": m})
        for f in sorted(t_all - h_all):
            false_alarms.append({"item": x["id"], "kind": "field users", "answer": f})
        for f in sorted((h_r - t_r) & t_all):
            misses.append({"item": x["id"], "kind": "readers only", "missed": f})
        for f in sorted((h_w - t_w) & t_all):
            misses.append({"item": x["id"], "kind": "writers only", "missed": f})
        for f in sorted((t_r - h_r) & h_all):
            false_alarms.append({"item": x["id"], "kind": "readers only", "answer": f})
        for f in sorted((t_w - h_w) & h_all):
            false_alarms.append({"item": x["id"], "kind": "writers only", "answer": f})
        a = rec["answer"] or {}
        items.append(
            {
                "id": x["id"],
                "exit": rec["exit"],
                "total": a.get("uses_total"),
                "omitted": a.get("uses_omitted"),
            }
        )
    out = {
        "scores": {k: tally(v) for k, v in sorted(rows.items())},
        "combined": {
            k: tally([r for kk, v in rows.items() if kk.endswith(k) for r in v])
            for k in (
                "callers/exact",
                "callers/all",
                "fields/exact",
                "fields/all",
                "fields/readers",
                "fields/writers",
            )
        },
        "misses": misses,
        "false_alarms": false_alarms,
        "items": items,
    }
    json.dump(out, sys.stdout, indent=1, default=sorted)
    print()


if __name__ == "__main__":
    main()
