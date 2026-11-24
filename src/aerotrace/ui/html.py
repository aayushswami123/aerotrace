"""`aerotrace check --html`: one self-contained HTML file that makes no network request.

The CSS and JS in `assets/` are inlined, and a Content-Security-Policy with their sha256 hashes stops the
browser from loading or running anything else. The facts are inlined as JSON. Output is deterministic:
the same data gives the same bytes. Every fact is also in a plain HTML list, so the report reads without
JavaScript and with a screen reader; the graph is an extra view.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from html import escape
from importlib import resources
from pathlib import Path

from aerotrace.ui.schema import (
    CERTAINTIES,
    CERTAINTY_MEANING,
    EDGE_KINDS,
    KINDS,
    STATUSES,
    check,
    node_word,
)
from aerotrace.ui.terminal import (
    ICONS,
    clean,
    coverage_lines,
    detail,
    headline,
    not_followed_lines,
    proof_line,
    title,
)

_PLACEHOLDER = re.compile(r"\{\{([A-Z]+)\}\}")
_SLASHES = re.compile(r"/(?=/)|(?<=/)/")
OPEN_LIMIT = 50  # list sections with more items than this start closed

# Status -> (shape, icon) as SVG path data. Colour is never the only signal: each status has its own
# shape, its own icon, and a word (schema.node_word). The JS draws nodes only from this table.
SHAPES = {
    "changed": ("M0,-13L13,0L0,13L-13,0Z", "M-3,-3H3V3H-3Z"),
    "new": ("M0,-13L13,0L0,13L-13,0Z", "M-5,0H5M0,-5V5"),
    "removed": ("M0,-13L13,0L0,13L-13,0Z", "M-5,-5L5,5M5,-5L-5,5"),
    "breaks": ("M-10,-10H10V10H-10Z", "M-5,-5L5,5M5,-5L-5,5"),
    "affected": ("M0,-12L12,10H-12Z", "M0,-4V3M0,6V7"),
    "unknown": ("M-10,0A10,10 0 1,0 10,0A10,10 0 1,0 -10,0Z", "M-3,-4C-3,-8 3,-8 3,-4C3,-1 0,-1 0,2M0,5V6"),
    "no_effect": ("M-6,0A6,6 0 1,0 6,0A6,6 0 1,0 -6,0Z", "M-3,0H3"),
}


def _asset(name: str) -> str:
    text = (resources.files("aerotrace.ui") / "assets" / name).read_text(encoding="utf-8")
    return text.replace("\r\n", "\n")


def _sha256(text: str) -> str:
    return base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


def _script_json(obj: object) -> str:
    """JSON that is safe inside <script type="application/json">: no '<', '>', '&' and no '//'."""
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    s = s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return _SLASHES.sub(r"\\/", s)  # '//' only appears inside strings, where '\/' means '/'.


def _e(s: object) -> str:
    return escape(str(s), quote=True)


def _icon(key: str) -> str:
    return ICONS[key][0].strip()


def _compact(data: dict) -> dict:
    """The data the JS needs, as arrays (about half the size of the contract objects)."""
    nodes = data.get("nodes") or []
    index = {n["id"]: i for i, n in enumerate(nodes)}
    files = sorted({n["file"] for n in nodes})
    file_index = {f: i for i, f in enumerate(files)}
    return {
        "certainties": list(CERTAINTIES),
        "edge_kinds": list(EDGE_KINDS),
        "edges": [
            [index[e["from"]], index[e["to"]], EDGE_KINDS.index(e["kind"]), CERTAINTIES.index(e["certainty"])]
            for e in data.get("edges") or []
        ],
        "files": files,
        "icons": {s: _icon(s) for s in STATUSES},
        "kinds": list(KINDS),
        "meaning": CERTAINTY_MEANING,
        "nodes": [
            [
                n["name"],
                KINDS.index(n["kind"]),
                file_index[n["file"]],
                n["line"],
                STATUSES.index(n["status"]),
                CERTAINTIES.index(n["certainty"]),
                n["link"] or "",
                n["why"],
                n["distance"],
                index[n["via"]] if n.get("via") is not None else -1,
                n["lines"],
                node_word(n["status"], n["certainty"]),
                n.get("via_name") or "",
            ]
            for n in nodes
        ],
        "omitted": (data.get("omitted") or {}).get("nodes", 0),
        "shapes": [list(SHAPES[s]) for s in STATUSES],
        "statuses": list(STATUSES),
    }


def _header(data: dict) -> str:
    icon, lines = headline(data)
    out = [
        f'<h1 class="head h-{icon}"><span class="hicon" aria-hidden="true">{_e(_icon(icon))}</span> '
        f"{_e(lines[0])}</h1>"
    ]
    out += [f"<p>{_e(line)}</p>" for line in lines[1:]]
    if data["status"] == "ok":
        cov = coverage_lines(data)
        out += [
            f'<p class="proof">{_e(line)}</p>' for line in (cov[:1] if icon in ("clear", "info") else cov)
        ]
        files = data["proof"].get("outside_build_files")
        if files:
            items = "".join(f"<li>{_e(f)}</li>" for f in files)
            out.append(
                f"<details><summary>Files outside this build ({len(files):,})</summary><ul>{items}</ul></details>"
            )
        out.append(f'<p class="proof">{_e(proof_line(data))}</p>')
        left = data["omitted"]["nodes"]
        if left:
            out.append(f"<p>{left:,} more nodes were left out of this report to keep it small.</p>")
    return "\n".join(out)


def _item(i: int, status: str, icon: str, word: str, head: str, where: str, why: str) -> str:
    return (
        f'<li data-i="{i}"><button class="it s-{status}"><i aria-hidden="true">{_e(icon)}</i> <em>{_e(word)}</em> '
        f"<b>{_e(head)}</b> <span>{_e(where)}</span><small>{_e(why)}</small></button></li>"
    )


def _node_item(i: int, n: dict) -> str:
    word = node_word(n["status"], n["certainty"])
    return _item(i, n["status"], _icon(n["status"]), word, title(n), f"{n['file']}:{n['line']}", detail(n))


def _details(heading: str, count: int, items: list[str]) -> str:
    open_ = " open" if count <= OPEN_LIMIT else ""
    return (
        f"<details{open_}><summary>{_e(heading)} ({count:,})</summary><ul>\n"
        + "\n".join(items)
        + "\n</ul></details>"
    )


def _gap_section(data: dict) -> str:
    gaps = not_followed_lines(data)
    if not gaps:
        return ""
    items = [f'<li class="plain"><i aria-hidden="true">?</i> {_e(g)}</li>' for g in gaps]
    return _details("Changes AeroTrace could not follow", len(gaps), items)


def _list(data: dict) -> str:
    if data["status"] == "nothing_to_check":
        return _gap_section(data)
    if data["status"] != "ok":
        return ""
    nodes = data["nodes"]
    index = {n["id"]: i for i, n in enumerate(nodes)}
    sec = data["sections"]
    parts = ['<p id="listnote" class="note" hidden></p>']
    changed = [i for i, n in enumerate(nodes) if n["distance"] == 0]
    if changed:
        parts.append(_details("What changed", len(changed), [_node_item(i, nodes[i]) for i in changed]))
    for key, heading in (("functions", "Functions"), ("data", "Data")):
        ids = sec[key]
        if ids:
            parts.append(_details(heading, len(ids), [_node_item(index[x], nodes[index[x]]) for x in ids]))
    if gaps := _gap_section(data):
        parts.append(gaps)
    tests = sec.get("tests_to_run")
    if tests is None:
        parts.append('<p class="note">Tests: not known yet (no test map in the index).</p>')
    elif not tests:
        parts.append('<p class="note">Tests to run: no test found that reaches this change.</p>')
    else:
        items = []
        for t in tests:
            covers = ", ".join(nodes[index[c]]["name"] for c in t["covers"])
            items.append(
                f'<li class="plain"><b>{_e(t["name"])}</b>'
                + (f" covers {_e(covers)}" if covers else "")
                + "</li>"
            )
        parts.append(_details("Tests to run", len(tests), items))
    untested = sec.get("untested_changes")
    if untested:
        items = []
        for u in untested:
            n = nodes[index[u["node"]]]
            items.append(
                _item(
                    index[u["node"]],
                    "no_test",
                    _icon("no_test"),
                    "NO TEST",
                    title(n),
                    f"{n['file']}:{n['line']}",
                    u["why"],
                )
            )
        parts.append(_details("Untested changes", len(untested), items))
    for scope, heading in (("change", "Blind spots in this change"), ("index", "Limits of the index")):
        spots = [b for b in sec["blind_spots"] if b["scope"] == scope]
        if spots:
            items = [f'<li class="plain"><i aria-hidden="true">?</i> {_e(b["text"])}</li>' for b in spots]
            parts.append(_details(heading, len(spots), items))
    return "\n".join(parts)


_LEGEND = (
    ("changed", "CHANGED / NEW / REMOVED", "diamond"),
    ("breaks", "BREAKS (will not build or link)", "square"),
    ("affected", "AFFECTED", "triangle"),
    ("unknown", "UNKNOWN (could not follow)", "dashed circle"),
    ("no_effect", "NO EFFECT FOUND", "small circle"),
)


def _graph(data: dict) -> str:
    if data["status"] != "ok" or not data["nodes"]:
        why = "nothing was checked" if data["status"] in ("no_index", "index_error") else "nothing to draw"
        return f'<section id="graphpane" class="empty"><p class="note">No graph: {why}.</p></section>'
    legend = "".join(
        f'<li><svg class="lg" viewBox="-14 -14 28 28" aria-hidden="true" focusable="false">'
        f'<g class="node s-{s}"><path class="shape" d="{SHAPES[s][0]}"/><path class="icon" d="{SHAPES[s][1]}"/></g></svg> {_e(label)} <span class="sr">({shape})</span></li>'
        for s, label, shape in _LEGEND
    )
    return (
        '<section id="graphpane" aria-label="Graph">\n'
        '<div class="gtools">'
        '<button type="button" id="expand">Expand all</button>'
        '<button type="button" id="focusbtn" aria-pressed="false">Focus</button>'
        '<button type="button" id="fit">Fit</button>'
        '<button type="button" id="zin" aria-label="Zoom in">+</button>'
        '<button type="button" id="zout" aria-label="Zoom out">-</button>'
        "</div>\n"
        '<svg id="graph" role="application" tabindex="0" aria-roledescription="impact graph" '
        'aria-label="Impact graph. Arrow keys move between nodes, Home goes to the change, F focuses, '
        'E expands, Escape shows all. The list on the left has the same facts." aria-describedby="announce">'
        '<g id="edges"><path id="e-calls" class="edge"/><path id="e-data" class="edge data"/>'
        '<path id="e-hl" class="edge hl"/></g><g id="nodes"></g><g id="bubbles"></g></svg>\n'
        f'<ul class="legend" aria-label="Legend">{legend}</ul>\n'
        "</section>"
    )


def render(data: dict) -> str:
    """The whole HTML report as one string. Same data, same bytes."""
    check(data)
    data = clean(data)  # type: ignore[assignment]  # bidi and control characters shown as \uXXXX
    css, js = _asset("report.css"), _asset("report.js")
    csp = (
        f"default-src 'none'; script-src 'sha256-{_sha256(js)}'; style-src 'sha256-{_sha256(css)}'; "
        "base-uri 'none'; form-action 'none'"
    )
    _, head = headline(data)
    parts = {
        "CSP": _e(csp),
        "TITLE": _e("AeroTrace: " + head[0]),
        "CSS": css,
        "HEADER": _header(data),
        "LIST": _list(data),
        "GRAPH": _graph(data),
        "DATA": _script_json(_compact(data)),
        "JS": js,
    }
    return _PLACEHOLDER.sub(lambda m: parts[m.group(1)], _asset("report.html"))


def write_html(data: dict, out: Path, root: Path) -> str:
    """Write the report to `out` and return one line saying what happened.

    Refuses to write inside the repo's `.aerotrace/` folder (that belongs to the index), and to any name
    that does not end in .html or .htm, so it can never overwrite source code or other files.
    """
    out_abs, index_dir = out.resolve(), (root / ".aerotrace").resolve()
    if out_abs.is_dir():
        raise ValueError(f"{out} is a folder. Give a file name, for example {out / 'aerotrace-report.html'}.")
    if out_abs.suffix.lower() not in (".html", ".htm"):
        raise ValueError(
            f"Won't write the report to {out}: the name must end in .html, for example report.html."
        )
    if out_abs == index_dir or index_dir in out_abs.parents:
        raise ValueError(f"Won't write the report inside {index_dir}; that folder belongs to the index.")
    text = render(data)
    existed = out_abs.exists()
    out_abs.parent.mkdir(parents=True, exist_ok=True)
    out_abs.write_bytes(text.encode("utf-8"))
    size = len(text.encode("utf-8"))
    what = "Replaced the existing HTML file" if existed else "Wrote"
    return f"{what} {out} ({size / 1024:,.0f} KB). Open it in any browser; it works offline."
