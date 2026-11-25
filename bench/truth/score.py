"""Score every tool's answers against the truth (rules: docs/benchmark/accuracy-cases-protocol.md).

Usage: python3 -I bench/truth/score.py <work-dir> <run-dir>
Reads <run-dir>/truth.json, <run-dir>/might_break.json (a copy of bench/truth/might_break.json), <run-dir>/raw/<case>/<tool>.json and, if
present, <run-dir>/tokens.json. Writes <run-dir>/results.json.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sites import Sites, old_changed_lines  # noqa: E402

TOOLS = ["aerotrace", "clangd", "cscope", "ripgrep"]
FN_KINDS = {"add_param", "remove_fn", "int_to_void", "ptr_param", "ptr_remove", "behaviour"}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogateescape")).hexdigest()


def own_site(case: dict) -> str | None:
    return case["symbol"] if case["kind"] in FN_KINDS else None


def to_sites(
    sites: Sites, hits: list[list], dropped_lines: dict[str, set[int]], own: str | None
) -> list[str]:
    out = set()
    for f, ln in hits:
        if ln in dropped_lines.get(f, set()):
            continue
        s = sites.site(f, ln)
        if s != own:
            out.add(s)
    return sorted(out)


def score(truth: set[str], answer: list[str]) -> dict:
    a = set(answer)
    return {
        "found": len(a & truth),
        "missed": sorted(truth - a),
        "false_alarms": len(a - truth),
        "answer_sites": len(a),
        "truth_sites": len(truth),
    }


def main() -> None:
    work, run = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    truth = {c["id"]: c for c in json.loads((run / "truth.json").read_text())["cases"]}
    might = json.loads((run / "might_break.json").read_text())["cases"]  # a copy kept with the run
    tokens = json.loads((run / "tokens.json").read_text()) if (run / "tokens.json").exists() else {}
    cases = json.loads((HERE.parent / "cases" / "cases.json").read_text())["cases"]
    site_maps = {r: Sites(work / r / "base") for r in ("redis", "sqlite", "curl")}
    rows = []
    for c in cases:
        t = truth[c["id"]]
        own = own_site(c)
        tsites = set(might[c["id"]]["sites"]) if c["kind"] == "behaviour" else set(t["break_sites"]) - {own}
        patch = (HERE.parent / "cases" / "patches" / f"{c['id']}.diff").read_text(errors="surrogateescape")
        dropped = old_changed_lines(patch)
        row = {
            "id": c["id"],
            "repo": c["repo"],
            "kind": c["kind"],
            "level": c["difficulty"],
            "symbol": c["symbol"],
            "truth": sorted(tsites),
            "link_checked": t.get("link_checked"),
            "tools": {},
        }
        for tool in TOOLS:
            raw = json.loads((run / "raw" / c["id"] / f"{tool}.json").read_text())
            if "error" in raw and "hits" not in raw:
                row["tools"][tool] = {"error": raw["error"]}
                continue
            hits = raw["hits"]
            if tool == "aerotrace" and c["kind"] == "behaviour":
                hits = [[n["file"], ln] for n in raw["nodes"] if n["status"] in ("breaks", "affected")
                        for ln in (n.get("lines") or [n["line"]])]  # fmt: skip
            ans = to_sites(site_maps[c["repo"]], hits, dropped, own)
            res = score(tsites, ans)
            res.update(
                {
                    "seconds": raw.get("seconds"),
                    "answer": ans,
                    "tokens": tokens.get(sha(raw.get("text", ""))),
                    "text_sha256": sha(raw.get("text", "")),
                }
            )
            if tool == "aerotrace":
                anywhere = to_sites(site_maps[c["repo"]], raw["hits_anywhere"], dropped, own)
                res["anywhere"] = score(tsites, anywhere)
                n_breaks = sum(1 for n in raw["nodes"] if n["status"] == "breaks")
                res["breaks_beyond_text_cap"] = max(
                    0, n_breaks - 15
                )  # the MCP text shows at most 15 per list
                res["omitted"] = raw.get("omitted")
            row["tools"][tool] = res
        rows.append(row)

    def agg(sel: list[dict], tool: str, view: str | None = None) -> dict:
        found = truth_n = fa = ans = 0
        secs, toks = [], []
        for r in sel:
            x = r["tools"][tool]
            if "error" in x:
                continue
            y = x[view] if view else x
            found += y["found"]
            truth_n += y["truth_sites"]
            fa += y["false_alarms"]
            ans += y["answer_sites"]
            secs.append(x["seconds"])
            if x.get("tokens") is not None:
                toks.append(x["tokens"])
        return {
            "cases": len(sel),
            "found": found,
            "truth_sites": truth_n,
            "recall": round(found / truth_n, 4) if truth_n else None,
            "false_alarms": fa,
            "precision": round(found / ans, 4) if ans else None,
            "median_seconds": round(statistics.median(secs), 4) if secs else None,
            "tokens_total": sum(toks) if len(toks) == len(sel) else None,
        }

    groups = {}
    for repo in ("all", "redis", "sqlite", "curl"):
        for level in ("all", "easy", "hard", "might_break"):
            sel = [
                r
                for r in rows
                if (repo == "all" or r["repo"] == repo) and (level == "all" or r["level"] == level)
            ]
            if level == "all":
                sel = [r for r in sel if r["level"] != "might_break"]  # build breaks only; might-break apart
            if not sel:
                continue
            g = {t: agg(sel, t) for t in TOOLS}
            g["aerotrace_anywhere"] = agg(sel, "aerotrace", "anywhere")
            groups[f"{repo}/{level}"] = g
    sel = [r for r in rows if r["level"] == "hard" and r["id"] != "curl-09"]  # extra view, see RESULTS.md
    groups["all/hard_without_curl-09"] = {t: agg(sel, t) for t in TOOLS}
    groups["all/hard_without_curl-09"]["aerotrace_anywhere"] = agg(sel, "aerotrace", "anywhere")
    # Extra view: the hard cases where AeroTrace named at least one break. It shows how much of the
    # false-alarm gap comes from the cases where AeroTrace named none (and so could have none).
    sel = [r for r in sel if r["tools"]["aerotrace"].get("answer_sites")]
    groups["all/hard_aerotrace_named_a_break"] = {t: agg(sel, t) for t in TOOLS}
    groups["all/hard_aerotrace_named_a_break"]["aerotrace_anywhere"] = agg(sel, "aerotrace", "anywhere")
    out = {"protocol": "docs/benchmark/accuracy-cases-protocol.md", "groups": groups, "cases": rows}
    (run / "results.json").write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    g = groups["all/all"]
    for t in [*TOOLS, "aerotrace_anywhere"]:
        print(
            f"{t:20} recall {g[t]['recall']}  found {g[t]['found']}/{g[t]['truth_sites']}  false alarms {g[t]['false_alarms']}"
        )


if __name__ == "__main__":
    main()
