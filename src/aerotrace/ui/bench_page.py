"""The benchmark results page: one self-contained, offline HTML file built from a benchmark run.

`build(run_dir) -> dict` reads results.json, truth.json and setup-*.json from a run folder and computes
every number the page shows. `render(data) -> str` turns that into HTML. Nothing is typed by hand.
Same run in, same bytes out. The page loads nothing: fonts are inlined, and a Content-Security-Policy
with sha256 hashes allows only its own style and script. All tables are plain HTML; the chart is drawn
on top by the script.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from html import escape
from importlib import resources
from pathlib import Path

TOOLS = ["aerotrace", "clangd", "cscope", "ripgrep"]
TOOL_NAME = {"aerotrace": "AeroTrace", "clangd": "clangd", "cscope": "cscope", "ripgrep": "ripgrep"}
TOOL_HOW = {
    "aerotrace": "claimed breaks from `aerotrace check --patch case.diff --json`",
    "clangd": "find-references at the symbol's definition (background index)",
    "cscope": "`cscope -d -L0 <name>` (find this C symbol)",
    "ripgrep": "`rg -n -w <name>` over the build's files",
}
REPOS = ["redis", "sqlite", "curl"]
REPO_NAME = {"redis": "Redis", "sqlite": "SQLite", "curl": "curl"}
VIEWS = {
    "all": ("All build cases", lambda r: r["level"] != "might_break"),
    "easy": ("Easy", lambda r: r["level"] == "easy"),
    "hard": ("Hard", lambda r: r["level"] == "hard"),
    "hard_x": ("Hard, without curl-09", lambda r: r["level"] == "hard" and r["id"] != "curl-09"),
}
VIEW_GROUP = {"all": "all/all", "easy": "all/easy", "hard": "all/hard", "hard_x": "all/hard_without_curl-09"}
MACRO_CASE = "curl-09"


def _median(xs: list[float]) -> float | None:
    return sorted(xs)[len(xs) // 2] if xs else None


def _agg(rows: list[dict], tool: str) -> dict:
    ok = [r for r in rows if "error" not in r["tools"][tool]]
    toks = [r["tools"][tool].get("tokens") for r in ok]
    return {
        "cases": len(rows),
        "found": sum(r["tools"][tool]["found"] for r in ok),
        "truth": sum(r["tools"][tool]["truth_sites"] for r in ok),
        "false_alarms": sum(r["tools"][tool]["false_alarms"] for r in ok),
        "seconds": _median([r["tools"][tool]["seconds"] for r in ok]),
        "tokens": sum(toks) if ok and all(t is not None for t in toks) else None,
        "errors": len(rows) - len(ok),
    }


def build(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    res = json.loads((run_dir / "results.json").read_text())
    truth = json.loads((run_dir / "truth.json").read_text())
    setups = {r: json.loads((run_dir / f"setup-{r}.json").read_text()) for r in REPOS}
    rows = res["cases"]
    views = {}
    for key, (label, keep) in VIEWS.items():
        sel = [r for r in rows if keep(r)]
        views[key] = {
            "label": label,
            "cases": len(sel),
            "easy": sum(r["level"] == "easy" for r in sel),
            "hard": sum(r["level"] == "hard" for r in sel),
            "total": {t: _agg(sel, t) for t in TOOLS},
            "repos": {rp: {t: _agg([r for r in sel if r["repo"] == rp], t) for t in TOOLS} for rp in REPOS},
        }
    build_rows = [r for r in rows if r["level"] != "might_break"]
    ties = [r["id"] for r in build_rows if all(r["tools"][t]["found"] == len(r["truth"]) for t in TOOLS)]
    fewer = [
        r["id"]
        for r in build_rows
        if r["tools"]["aerotrace"]["found"] < max(r["tools"][t]["found"] for t in TOOLS if t != "aerotrace")
    ]
    more = [
        r["id"]
        for r in build_rows
        if r["tools"]["aerotrace"]["found"] > max(r["tools"][t]["found"] for t in TOOLS if t != "aerotrace")
    ]
    might = [r for r in rows if r["level"] == "might_break"]
    macro = next(r for r in rows if r["id"] == MACRO_CASE)
    capped = sorted(
        (r["id"], r["tools"]["aerotrace"]["breaks_beyond_text_cap"])
        for r in rows
        if r["tools"]["aerotrace"].get("breaks_beyond_text_cap")
    )
    cases = [
        {
            "id": r["id"],
            "repo": r["repo"],
            "kind": r["kind"],
            "level": r["level"],
            "breaks": len(r["truth"]),
            "tools": {t: {k: r["tools"][t][k] for k in ("found", "false_alarms")} for t in TOOLS},
            "whole": {k: r["tools"]["aerotrace"]["anywhere"][k] for k in ("found", "false_alarms")},
        }
        for r in rows
    ]
    s = truth["setup"]
    return {
        "run": run_dir.name,
        "machine": f"{s['cpu']}, macOS {s['macos']}, {s['machine']}",
        "compiler": s["cc"],
        "versions": setups["redis"]["versions"],
        "aerotrace_commit": setups["redis"]["aerotrace_commit"][:7],
        "setup_seconds": {
            r: {k: setups[r][f"{k}_seconds"] for k in ("aerotrace_index", "clangd_index", "cscope_db")}
            for r in REPOS
        },
        "views": views,
        "might_break": {"cases": len(might), "total": {t: _agg(might, t) for t in TOOLS}},
        "macro_case": {
            "id": MACRO_CASE,
            "breaks": len(macro["truth"]),
            "tools": {t: macro["tools"][t]["found"] for t in TOOLS},
        },
        "whole_answer": {
            "found": sum(r["tools"]["aerotrace"]["anywhere"]["found"] for r in build_rows),
            "false_alarms": sum(r["tools"]["aerotrace"]["anywhere"]["false_alarms"] for r in build_rows),
        },
        "ties": ties,
        "fewer": fewer,
        "more": more,
        "build_cases": len(build_rows),
        "capped": capped,
        "tokens_counted": any(r["tools"][t].get("tokens") is not None for r in rows for t in TOOLS),
        "cases": cases,
    }


# ---------------------------------------------------------------- HTML


def _asset(rel: str) -> bytes:
    return (resources.files("aerotrace.ui") / "web" / rel).read_bytes()


def _tokens_css() -> str:
    css = _asset("tokens.css").decode("utf-8")

    def inline(m: re.Match[str]) -> str:
        data = base64.b64encode(_asset(m.group(1))).decode("ascii")
        return f'url("data:font/woff2;base64,{data}")'

    return re.sub(r'url\("(fonts/[^"]+\.woff2)"\)', inline, css)


def _n(x: int) -> str:
    return f"{x:,}"


def _pct(found: int, truth: int) -> str:
    return "n/a" if not truth else f"{100 * found / truth:.0f}%"


def _secs(x: float | None) -> str:
    if x is None:
        return "n/a"
    return f"{x * 1000:.0f} ms" if x < 1 else f"{x:.1f} s"


def _headline(d: dict) -> list[str]:
    e, hx = d["views"]["easy"], d["views"]["hard_x"]
    at, others = hx["total"]["aerotrace"], [hx["total"][t] for t in TOOLS[1:]]
    ea = e["total"]["aerotrace"]
    easy_all = all(e["total"][t]["found"] == e["total"][t]["truth"] for t in TOOLS)
    first = (
        f"Easy cases ({e['cases']}): every tool found all {_n(ea['truth'])} breaks."
        if easy_all
        else f"Easy cases ({e['cases']}): AeroTrace found {_n(ea['found'])} of {_n(ea['truth'])} breaks."
    )
    other_txt = ", ".join(
        f"{TOOL_NAME[t]} {_n(o['found'])} with {_n(o['false_alarms'])}"
        for t, o in zip(TOOLS[1:], others, strict=True)
    )
    second = (
        f"Hard cases without the macro case {d['macro_case']['id']} ({hx['cases']}): AeroTrace found "
        f"{_n(at['found'])} of {_n(at['truth'])} with {_n(at['false_alarms'])} false alarms; {other_txt}."
    )
    return [first, second]


def _might_line(mb: dict) -> str:
    a = mb["aerotrace"]
    if all(mb[t]["found"] == mb[t]["truth"] for t in TOOLS):
        return f"every tool found all {_n(a['truth'])}; AeroTrace adds {_n(a['false_alarms'])} callers further up"
    return ", ".join(f"{TOOL_NAME[t]} {_n(mb[t]['found'])} of {_n(mb[t]['truth'])}" for t in TOOLS)


def _summary_table(v: dict, caption: str) -> str:
    head = "<tr><th>Tool</th><th class=num>Breaks found</th><th class=num>Recall</th><th class=num>False alarms</th><th class=num>Median time</th><th class=num>Tokens</th></tr>"
    body = "".join(
        f"<tr><th scope=row>{TOOL_NAME[t]}</th><td class=num>{_n(x['found'])} / {_n(x['truth'])}</td>"
        f"<td class=num>{_pct(x['found'], x['truth'])}</td><td class=num>{_n(x['false_alarms'])}</td>"
        f"<td class=num>{_secs(x['seconds'])}</td><td class=num>{'not counted' if x['tokens'] is None else _n(x['tokens'])}</td></tr>"
        for t in TOOLS
        for x in [v["total"][t]]
    )
    return f"<div class=scroll><table><caption>{escape(caption)}</caption><thead>{head}</thead><tbody>{body}</tbody></table></div>"


def _case_table(d: dict) -> str:
    head = (
        "<tr><th>Case</th><th>Kind</th><th>Level</th><th class=num>Breaks</th>"
        + "".join(f"<th class=num>{TOOL_NAME[t]}</th>" for t in TOOLS)
        + "</tr>"
    )
    rows = []
    for c in d["cases"]:
        cells = "".join(
            f"<td class=num{' at' if t == 'aerotrace' else ''}>{c['tools'][t]['found']}/{c['breaks']}"
            f"<span class=fa> · {c['tools'][t]['false_alarms']} fa</span></td>"
            for t in TOOLS
        )
        rows.append(
            f"<tr><td class=mono>{escape(c['id'])}</td><td class=mono>{escape(c['kind'])}</td>"
            f"<td>{escape(c['level'].replace('_', ' '))}</td><td class=num>{c['breaks']}</td>{cells}</tr>"
        )
    return f"<table class=cases><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"


def render(d: dict) -> str:
    css = _tokens_css() + _asset("bench-page.css").decode("utf-8")
    js = _asset("bench-page.js").decode("utf-8")
    data_json = json.dumps(d, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    sha = lambda s: base64.b64encode(hashlib.sha256(s.encode("utf-8")).digest()).decode()  # noqa: E731
    csp = (
        f"default-src 'none'; style-src 'sha256-{sha(css)}'; script-src 'sha256-{sha(js)}'; "
        "font-src data:; base-uri 'none'; form-action 'none'"
    )
    raw = f"../../bench/results/{d['run']}/raw/"
    v = d["versions"]
    head1, head2 = _headline(d)
    seg = "".join(
        f'<button type=button data-view="{k}" aria-pressed="{"true" if k == "all" else "false"}">{escape(x["label"])}</button>'
        for k, x in d["views"].items()
    )
    tables = "".join(
        f"<details{' open' if k == 'all' else ''}><summary>{escape(x['label'])}: {x['cases']} cases "
        f"({x['easy']} easy, {x['hard']} hard)</summary>{_summary_table(x, x['label'])}</details>"
        for k, x in d["views"].items()
    )
    mb = d["might_break"]["total"]
    mac = d["macro_case"]
    setup_rows = "".join(
        f"<li>{REPO_NAME[r]}: AeroTrace index {s['aerotrace_index']} s, clangd index {s['clangd_index']} s, "
        f"cscope database {s['cscope_db']} s</li>"
        for r, s in d["setup_seconds"].items()
    )
    fewer = ", ".join(d["fewer"]) or "none"
    more = ", ".join(d["more"]) or "none"
    capped = ", ".join(f"{i} ({n})" for i, n in d["capped"]) or "none"
    tokens_note = (
        ""
        if d["tokens_counted"]
        else "<p class=note><b>Tokens not counted yet.</b> The token counts need the Anthropic token-counting "
        "API, which this run didn't have access to. Every token cell says so until they're counted.</p>"
    )
    body = f"""<main class=wrap>
<header class=hero>
<p class=caps>Recall · False alarms · Time · Tokens</p>
<h1>What does a C change break? Four tools, 30 changes, checked against the compiler.</h1>
<p class=lead>{escape(head1)} {escape(head2)}</p>
<p class=lead>Each tool was asked the question an agent asks before an edit, the way the tool is normally used. Answers were scored against what the compiler and linker report.</p>
{tokens_note}
</header>

<section aria-labelledby=chart-h>
<h2 id=chart-h class=sr>Chart</h2>
<div class=controls>
<div class=seg role=group aria-label="Which cases">{seg}</div>
<div class=seg role=group aria-label="Measure"><button type=button data-metric=found aria-pressed=true>Breaks found</button><button type=button data-metric=fa aria-pressed=false>False alarms</button></div>
<button type=button class=toggle role=switch aria-checked=true id=others><span class=sw aria-hidden=true></span>Show other tools</button>
</div>
<p class=mix id=mix></p>
<div id=legend class=legend></div>
<div id=chart class=chart></div>
<noscript><p class=note>The chart needs JavaScript. Every number is in the tables below.</p></noscript>
</section>

<section class=cards aria-label="Wins, ties and losses">
<div class=card><h3>Ties <span class=mono>{len(d["ties"])} of {d["build_cases"]}</span></h3><p>Build cases where every tool found every break.</p></div>
<div class=card><h3>AeroTrace finds fewer <span class=mono>{len(d["fewer"])} of {d["build_cases"]}</span></h3><p class=mono>{escape(fewer)}</p></div>
<div class=card><h3>AeroTrace finds more <span class=mono>{len(d["more"])} of {d["build_cases"]}</span></h3><p class=mono>{escape(more)}</p></div>
</section>

<section aria-labelledby=tables-h>
<h2 id=tables-h>Every view, as a table</h2>
{tables}
</section>

<section class=also aria-labelledby=also-h>
<h2 id=also-h class=caps>Also run</h2>
<ul>
<li><a href="#might">Might-break cases ({d["might_break"]["cases"]}): {_might_line(mb)} <span aria-hidden=true>→</span></a></li>
<li><a href="#macro">The macro case {escape(mac["id"])}: {_n(mac["breaks"])} breaks, AeroTrace finds {mac["tools"]["aerotrace"]}, the others {mac["tools"]["clangd"]} <span aria-hidden=true>→</span></a></li>
<li><a href="{escape(raw)}">Raw answers of every tool for every case <span aria-hidden=true>→</span></a></li>
<li><a href="RESULTS.md">Full write-up, limits, and AeroTrace bugs found <span aria-hidden=true>→</span></a></li>
<li><span class=dim>Agent case studies: not run yet</span></li>
</ul>
</section>

<section id=might aria-labelledby=might-h>
<h2 id=might-h>Might-break cases</h2>
<p>Nothing fails to build; the function returns a different value. Truth: the direct callers that use the value, checked by hand.</p>
{_summary_table(d["might_break"], "Might-break cases")}
</section>

<section id=macro aria-labelledby=macro-h>
<h2 id=macro-h>The macro case {escape(mac["id"])}</h2>
<p>A function-like macro in a header gets a new parameter. It is used inside other macros, so the compiler reports {_n(mac["breaks"])} functions. clangd, cscope and ripgrep point at the macro definitions that cause it, which the rules don't count as breaks. AeroTrace says it couldn't follow the change. Shown apart so one case doesn't decide the headline.</p>
</section>

<section aria-labelledby=cases-h>
<h2 id=cases-h>Every case</h2>
<p class=dim>found / breaks · false alarms (fa). AeroTrace's whole answer, AFFECTED places included, finds {_n(d["whole_answer"]["found"])} with {_n(d["whole_answer"]["false_alarms"])} false alarms over the build cases.</p>
<div class=scroll>{_case_table(d)}</div>
</section>

<footer class=foot>
<p><b>How each tool was run.</b> {" ".join(f"{TOOL_NAME[t]}: {escape(TOOL_HOW[t])}." for t in TOOLS)} Truth: each patch applied to a built copy of the pinned release, rebuilt with every error reported; every compile and link error is a break. A break is counted per function (or table).</p>
<p><b>Versions.</b> {escape(v["aerotrace"])} (commit {escape(d["aerotrace_commit"])}), {escape(v["clangd"])}, {escape(v["cscope"])}, {escape(v["rg"])}. Compiler: {escape(d["compiler"])}. <b>Machine.</b> {escape(d["machine"])}. <b>Time</b> is the median of 3 runs per query; one-off setup:</p>
<ul>{setup_rows}</ul>
<p>The MCP text of AeroTrace's answer shows at most 15 items per list; in {escape(capped)} it hides that many claimed breaks behind "N more". Recall is scored on the full answer.</p>
<p><b>Reproduce</b> (from an AeroTrace checkout): <a href="../../bench/truth/README.md">bench/truth/README.md</a>. Cases: <a href="../../bench/cases/CASES.md">bench/cases/CASES.md</a>. Rules, fixed before any tool ran: <a href="accuracy-cases-protocol.md">accuracy-cases-protocol.md</a>. Run: <span class=mono>{escape(d["run"])}</span>.</p>
</footer>
</main>"""
    return (
        "<!doctype html>\n<html lang=en>\n<head>\n<meta charset=utf-8>\n"
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">\n'
        '<meta name=viewport content="width=device-width,initial-scale=1">\n'
        "<title>AeroTrace accuracy benchmark</title>\n"
        f"<style>{css}</style>\n</head>\n<body>\n{body}\n"
        f'<script type="application/json" id=data>{data_json}</script>\n'
        f"<script>{js}</script>\n</body>\n</html>\n"
    )


def write(run_dir: Path, out: Path) -> Path:
    out.write_text(render(build(run_dir)), encoding="utf-8")
    return out
