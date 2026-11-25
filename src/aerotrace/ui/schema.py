"""The `aerotrace-check` JSON contract (version 1), and a shape check for it.

The producer (`aerotrace check`, item 5 Part C) fills this in. Renderers read it and never recompute it.
The full description, with an example, is in docs/plans/launch-ui.md section 1. In short:

- `status` of a node says what the change does to it; `certainty` says whether the link to the change
  is real (`exact`: the compiler resolved every step; `probable`: a step is a pointer, virtual call or
  same-name match; `unknown`: the path leaves code AeroTrace can follow). `link` is the raw label from
  `analysis.graph.CONFIDENCE_ORDER`.
- `breaks` means "will not build or link" and nothing else.
- Counts come from `summary` and `proof` only, because lists may be capped.
- Ids are opaque, except two forms Part C makes for things the index can't have:
  `removed:{file}:{line}:{name}` (a function the change removes, as in analysis/diff.py) and
  `new:{file}:{line}:{name}` (a function that is new in a patch that is not applied yet).

- `verdict` (`breaks`, `unknown`, `clear`) and `unchecked` come from analysis/verdict.py: UNKNOWN when a
  part of the change was not checked and nothing breaks (A-04).

`check()` only checks shape and consistency. It refuses data that would show a wrong all-clear.
"""

from __future__ import annotations

from typing import Any

from aerotrace.analysis.certainty import LEVELS, MEANING
from aerotrace.analysis.verdict import VERDICTS, decide

FORMAT = "aerotrace-check"
VERSION = 1

RUN_STATUSES = ("ok", "no_index", "index_error", "nothing_to_check")
# Order matters: it is the rank used for display and layout.
STATUSES = ("changed", "new", "removed", "breaks", "affected", "unknown", "no_effect")
CHANGE_STATUSES = ("changed", "new", "removed")
KINDS = ("function", "field", "global", "static_local")
EDGE_KINDS = ("calls", "reads", "writes")
CERTAINTIES = LEVELS  # exact, probable, unknown
SCOPES = ("change", "index")
NOT_FOLLOWED_KEYS = (
    "outside",
    "not_indexed",
    "deleted",
    "mismatched",
    "unreadable",
    "new_files",
    "build_files",
)
SUMMARY_KEYS = (
    "breaks_exact",
    "breaks_probable",
    "affected_direct",
    "affected_further",
    "unknown",
    "no_effect",
    "not_followed",
)
CHANGE_KEYS = (
    "files",
    "other_files",
    "changed_functions",
    "new_functions",
    "removed_functions",
    "changed_declarations",
)
PROOF_KEYS = ("index_files", "index_functions", "depth")
# Optional (added in 0.1): how much of the build was checked. All four come together.
COVERAGE_KEYS = ("build_files", "build_files_checked", "build_files_with_errors", "outside_build")

CERTAINTY_MEANING = {c: MEANING[c] for c in CERTAINTIES}


class ContractError(ValueError):
    """The JSON does not follow the aerotrace-check contract."""


def _fail(msg: str) -> None:
    raise ContractError(f"aerotrace-check data: {msg}")


def _int(d: dict, key: str, where: str) -> int:
    v = d.get(key)
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        _fail(f"{where}.{key} must be a whole number >= 0, got {v!r}")
    return v  # type: ignore[return-value]


def _str_list(v: Any, where: str) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        _fail(f"{where} must be a list of strings")
    return v


def node_word(status: str, certainty: str) -> str:
    """The status word shown next to every node. A probable break is a 'may break'."""
    if status == "breaks" and certainty != "exact":
        return "MAY BREAK"
    return {
        "changed": "CHANGED",
        "new": "NEW",
        "removed": "REMOVED",
        "breaks": "BREAKS",
        "affected": "AFFECTED",
        "unknown": "UNKNOWN",
        "no_effect": "NO EFFECT FOUND",
    }[status]


def check(data: Any) -> dict:
    """Return `data` if it follows the contract, else raise ContractError with a plain reason."""
    if not isinstance(data, dict):
        _fail("must be a JSON object")
    if data.get("format") != FORMAT:
        _fail(f"format must be {FORMAT!r}, got {data.get('format')!r}")
    ver = data.get("version")
    if not isinstance(ver, int) or isinstance(ver, bool):
        _fail("version must be a whole number")
    if ver > VERSION:
        _fail(f"version {ver} was made by a newer AeroTrace; this one reads version {VERSION}")
    if ver != VERSION:
        _fail(f"version {ver} is not supported (expected {VERSION})")
    if not isinstance(data.get("tool"), str):
        _fail("tool must be a string")
    status = data.get("status")
    if status not in RUN_STATUSES:
        _fail(f"status must be one of {', '.join(RUN_STATUSES)}, got {status!r}")
    msg = data.get("message")
    if msg is not None and not isinstance(msg, str):
        _fail("message must be a string or null")
    if status in ("no_index", "index_error") and not msg:
        _fail(f"status {status!r} needs a message")
    change = data.get("change")
    if change is not None:
        _check_change(change)
    _check_verdict_shape(data)
    if status in ("no_index", "index_error") and (data["verdict"] != "unknown" or not data["unchecked"]):
        _fail(f"status {status!r} checked nothing, so verdict must be 'unknown' and unchecked true")
    if status == "nothing_to_check":
        gaps = sum(len(change["not_followed"][k]) for k in NOT_FOLLOWED_KEYS) if change else 0
        _check_verdict(data, 0, gaps)
    if status != "ok":
        # Nothing was analysed, so nothing may be listed; but what the change skipped is still shown.
        if status == "nothing_to_check" and change is None:
            _fail("change is required when status is 'nothing_to_check'")
        for key in ("nodes", "edges"):
            if data.get(key):
                _fail(f"status {status!r} can't list {key}")
        return data
    if change is None:
        _fail("change is required when status is 'ok'")
    for key in ("summary", "nodes", "edges", "sections", "proof", "omitted"):
        if key not in data:
            _fail(f"{key} is required when status is 'ok'")
    summary = data["summary"]
    if not isinstance(summary, dict):
        _fail("summary must be an object")
    for k in SUMMARY_KEYS:
        _int(summary, k, "summary")
    proof = data["proof"]
    if not isinstance(proof, dict):
        _fail("proof must be an object")
    for k in PROOF_KEYS:
        _int(proof, k, "proof")
    if not isinstance(proof.get("stopped_early"), bool):
        _fail("proof.stopped_early must be true or false")
    if any(k in proof for k in COVERAGE_KEYS):
        for k in COVERAGE_KEYS:
            _int(proof, k, "proof")
        if proof["build_files_checked"] > proof["build_files"]:
            _fail("proof.build_files_checked can't be more than proof.build_files")
    if "outside_build_examples" in proof:
        ex = proof["outside_build_examples"]
        if not isinstance(ex, list) or not all(isinstance(f, str) for f in ex) or ex != sorted(ex):
            _fail("proof.outside_build_examples must be a sorted list of paths")
        if len(ex) > min(3, proof.get("outside_build", 0)) or (proof.get("outside_build") and not ex):
            _fail("proof.outside_build_examples must name up to 3 of the files outside the build")
    if "outside_build_files" in proof:
        files = proof["outside_build_files"]
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            _fail("proof.outside_build_files must be a list of paths")
        if len(files) != proof.get("outside_build") or files != sorted(files):
            _fail("proof.outside_build_files must be sorted and match proof.outside_build")
    if not isinstance(data["omitted"], dict):
        _fail("omitted must be an object")
    _int(data["omitted"], "nodes", "omitted")

    ids = _check_nodes(data["nodes"])
    _check_edges(data["edges"], ids)
    blind = _check_sections(data["sections"], ids)
    listed = set(data["sections"]["functions"]) | set(data["sections"]["data"])
    for n in data["nodes"]:
        if n["status"] not in CHANGE_STATUSES and n["id"] not in listed:
            _fail(f"node {n['id']!r} is in no section (put it in sections.functions or sections.data)")
        if n["status"] in CHANGE_STATUSES and n["id"] in listed:
            _fail(
                f"node {n['id']!r} is part of the change; it is listed under 'What changed', not in a section"
            )
    _check_counts(data, summary, blind)
    return data


def _check_change(change: Any) -> None:
    if not isinstance(change, dict):
        _fail("change must be an object")
    if change.get("mode") not in ("git", "patch"):
        _fail("change.mode must be 'git' or 'patch'")
    if change.get("base") is not None and not isinstance(change.get("base"), str):
        _fail("change.base must be a string or null")
    for k in CHANGE_KEYS:
        _int(change, k, "change")
    nf = change.get("not_followed")
    if not isinstance(nf, dict):
        _fail("change.not_followed must be an object")
    for k in NOT_FOLLOWED_KEYS:
        if k == "outside":
            items = nf.get(k)
            if not isinstance(items, list):
                _fail("change.not_followed.outside must be a list")
            for o in items:
                if not isinstance(o, dict) or not isinstance(o.get("file"), str):
                    _fail("change.not_followed.outside items need a file")
                for side in ("lines", "other_side_lines"):
                    v = o.get(side)
                    if not isinstance(v, list) or not all(isinstance(n, int) for n in v):
                        _fail(f"change.not_followed.outside[].{side} must be a list of line numbers")
        else:
            _str_list(nf.get(k), f"change.not_followed.{k}")


def _check_nodes(nodes: Any) -> set[str]:
    if not isinstance(nodes, list):
        _fail("nodes must be a list")
    ids: set[str] = set()
    for n in nodes:
        if not isinstance(n, dict):
            _fail("every node must be an object")
        nid = n.get("id")
        if not isinstance(nid, str) or not nid:
            _fail("every node needs a string id")
        if nid in ids:
            _fail(f"node id {nid!r} appears twice")
        ids.add(nid)
        for k in ("name", "file", "why"):
            if not isinstance(n.get(k), str):
                _fail(f"node {nid!r}: {k} must be a string")
        if n.get("kind") not in KINDS:
            _fail(f"node {nid!r}: kind must be one of {', '.join(KINDS)}")
        _int(n, "line", f"node {nid!r}")
        _int(n, "distance", f"node {nid!r}")
        st, cert = n.get("status"), n.get("certainty")
        if st not in STATUSES:
            _fail(f"node {nid!r}: status must be one of {', '.join(STATUSES)}")
        if cert not in CERTAINTIES:
            _fail(f"node {nid!r}: certainty must be one of {', '.join(CERTAINTIES)}")
        if st == "breaks" and cert == "unknown":
            _fail(f"node {nid!r}: a break must be exact or probable, never unknown")
        if (st == "unknown") != (cert == "unknown"):
            _fail(f"node {nid!r}: status 'unknown' and certainty 'unknown' go together")
        if (st in CHANGE_STATUSES) != (n["distance"] == 0):
            _fail(f"node {nid!r}: changed, new and removed nodes have distance 0, and only they do")
        if n.get("link") is not None and not isinstance(n.get("link"), str):
            _fail(f"node {nid!r}: link must be a string or null")
        for k in ("via", "via_name"):
            if n.get(k) is not None and not isinstance(n.get(k), str):
                _fail(f"node {nid!r}: {k} must be a string or null")
        lines = n.get("lines")
        if not isinstance(lines, list) or not all(isinstance(x, int) for x in lines):
            _fail(f"node {nid!r}: lines must be a list of line numbers")
    for n in nodes:
        if n.get("via") is not None and n["via"] not in ids:
            _fail(f"node {n['id']!r}: via {n['via']!r} is not a node (drop via for left-out nodes)")
    return ids


def _check_edges(edges: Any, ids: set[str]) -> None:
    if not isinstance(edges, list):
        _fail("edges must be a list")
    for e in edges:
        if not isinstance(e, dict):
            _fail("every edge must be an object")
        for k in ("from", "to"):
            if e.get(k) not in ids:
                _fail(f"edge {k} {e.get(k)!r} is not a node (drop edges to left-out nodes)")
        if e.get("kind") not in EDGE_KINDS:
            _fail(f"edge kind must be one of {', '.join(EDGE_KINDS)}")
        if e.get("certainty") not in CERTAINTIES:
            _fail(f"edge certainty must be one of {', '.join(CERTAINTIES)}")


def _check_sections(sections: Any, ids: set[str]) -> list[dict]:
    if not isinstance(sections, dict):
        _fail("sections must be an object")
    listed: set[str] = set()
    for k in ("functions", "data"):
        for nid in _str_list(sections.get(k), f"sections.{k}"):
            if nid not in ids:
                _fail(f"sections.{k}: {nid!r} is not a node")
            if nid in listed:
                _fail(f"sections: {nid!r} is listed twice")
            listed.add(nid)
    tests = sections.get("tests_to_run")
    if tests is not None:
        if not isinstance(tests, list):
            _fail("sections.tests_to_run must be a list or null")
        for t in tests:
            if not isinstance(t, dict) or not isinstance(t.get("name"), str):
                _fail("sections.tests_to_run items need a name")
            for nid in _str_list(t.get("covers"), "sections.tests_to_run[].covers"):
                if nid not in ids:
                    _fail(f"sections.tests_to_run: {nid!r} is not a node")
    untested = sections.get("untested_changes")
    if untested is not None:
        if not isinstance(untested, list):
            _fail("sections.untested_changes must be a list or null")
        for u in untested:
            if not isinstance(u, dict) or u.get("node") not in ids or not isinstance(u.get("why"), str):
                _fail("sections.untested_changes items need a node that exists and a why")
    blind = sections.get("blind_spots")
    if not isinstance(blind, list):
        _fail("sections.blind_spots must be a list")
    for b in blind:
        if not isinstance(b, dict) or not isinstance(b.get("text"), str):
            _fail("sections.blind_spots items need a text")
        if b.get("scope") not in SCOPES:
            _fail("sections.blind_spots[].scope must be 'change' or 'index'")
        if not isinstance(b.get("unchecked"), bool) or (b["unchecked"] and b["scope"] != "change"):
            _fail("sections.blind_spots[].unchecked must be true or false, and false for scope 'index'")
        if b.get("node") is not None and b["node"] not in ids:
            _fail(f"sections.blind_spots: {b['node']!r} is not a node")
    return blind


def _check_counts(data: dict, summary: dict, blind: list[dict]) -> None:
    """The headline must never be more hopeful than the lists."""
    seen = dict.fromkeys(
        ("breaks_exact", "breaks_probable", "affected_direct", "affected_further", "unknown", "no_effect"), 0
    )
    for n in data["nodes"]:
        st = n["status"]
        if st == "breaks":
            seen["breaks_exact" if n["certainty"] == "exact" else "breaks_probable"] += 1
        elif st == "affected":
            seen["affected_direct" if n["distance"] == 1 else "affected_further"] += 1
        elif st in seen:
            seen[st] += 1
    left_out = data["omitted"]["nodes"]
    for k, n in seen.items():
        if n > summary[k]:
            _fail(f"summary says {summary[k]} {k.replace('_', ' ')}, but nodes list {n}")
        if not left_out and n != summary[k]:
            _fail(
                f"summary says {summary[k]} {k.replace('_', ' ')}, but nodes list {n} and none were left out"
            )
    found = sum(summary[k] for k in seen)
    if not found and left_out:
        _fail(f"{left_out} nodes were left out, but the summary counts none (a wrong all-clear)")
    if not found and data["proof"]["stopped_early"]:
        _fail("the walk stopped early, but the summary counts nothing (a wrong all-clear)")
    nf = data["change"]["not_followed"]
    gaps = sum(len(nf[k]) for k in NOT_FOLLOWED_KEYS) + sum(
        b["scope"] == "change" and b["unchecked"] for b in blind
    )
    if gaps and not summary["not_followed"]:
        _fail("parts of the change were not followed, but summary.not_followed is 0 (a wrong all-clear)")
    _check_verdict(data, summary["breaks_exact"] + summary["breaks_probable"], gaps + summary["unknown"])


def _check_verdict_shape(data: dict) -> None:
    if data.get("verdict") not in VERDICTS:
        _fail(f"verdict must be one of {', '.join(VERDICTS)}, got {data.get('verdict')!r}")
    if not isinstance(data.get("unchecked"), bool):
        _fail("unchecked must be true or false")
    _int(data, "unchecked_parts", "data")
    reason = data.get("unchecked_reason")
    if reason is not None and not isinstance(reason, str):
        _fail("unchecked_reason must be a string or null")


def _check_verdict(data: dict, breaks: int, parts: int) -> None:
    """A-04: `unchecked` whenever a part was not checked (a skipped part, a blind spot of this change that is
    not a standing limit, an UNKNOWN node); the verdict follows from it and the breaks."""
    if data["unchecked"] != bool(parts):
        _fail(f"{parts} parts were not checked, but unchecked is {str(data['unchecked']).lower()}")
    if data["unchecked_parts"] != parts:
        _fail(f"unchecked_parts says {data['unchecked_parts']}, but {parts} parts were not checked")
    if parts and not data["unchecked_reason"]:
        _fail("unchecked_reason must name the first part that was not checked")
    want = decide(breaks, data["unchecked"])
    if data["verdict"] != want:
        if data["verdict"] == "clear":
            _fail(f"verdict is 'clear', but it must be {want!r} (a wrong all-clear)")
        _fail(f"verdict is {data['verdict']!r}, but the counts say {want!r}")
