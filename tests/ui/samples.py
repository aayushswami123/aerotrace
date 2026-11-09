"""Sample `aerotrace-check` data (version 1) for the renderers.

The names are Redis-flavoured but made up: this is display test data, not golden facts about Redis.
`sample()` is also written to tests/fixtures/ui/check-v1-sample.json, the contract file for Part C.
`big(n)` makes a large, deterministic graph for size and speed checks.
"""

from __future__ import annotations

import copy
import random

NOT_FOLLOWED_EMPTY = {
    "outside": [],
    "not_indexed": [],
    "deleted": [],
    "mismatched": [],
    "unreadable": [],
    "new_files": [],
}

CERTAINTY_RANK = ("exact", "probable", "unknown")
STATUS_RANK = ("changed", "new", "removed", "breaks", "affected", "unknown", "no_effect")


def _node(
    nid,
    name,
    file,
    line,
    status,
    certainty,
    link,
    why,
    distance,
    via=None,
    via_name=None,
    kind="function",
    lines=(),
):
    return {
        "id": nid,
        "name": name,
        "kind": kind,
        "file": file,
        "line": line,
        "status": status,
        "certainty": certainty,
        "link": link,
        "why": why,
        "distance": distance,
        "via": via,
        "via_name": via_name,
        "lines": list(lines),
    }


def _sorted(nodes):
    return sorted(
        nodes,
        key=lambda n: (
            STATUS_RANK.index(n["status"]),
            CERTAINTY_RANK.index(n["certainty"]),
            n["distance"],
            n["file"],
            n["line"],
            n["id"],
        ),
    )


def base(status="ok", **over):
    d = {
        "format": "aerotrace-check",
        "version": 1,
        "tool": "aerotrace 0.1.0",
        "status": status,
        "message": None,
        "change": {
            "mode": "git",
            "base": "HEAD",
            "files": 1,
            "other_files": 0,
            "changed_functions": 1,
            "new_functions": 0,
            "removed_functions": 0,
            "changed_declarations": 0,
            "not_followed": copy.deepcopy(NOT_FOLLOWED_EMPTY),
        },
        "summary": {
            "breaks_exact": 0,
            "breaks_probable": 0,
            "affected_direct": 0,
            "affected_further": 0,
            "unknown": 0,
            "no_effect": 0,
            "not_followed": 0,
        },
        "nodes": [],
        "edges": [],
        "sections": {
            "functions": [],
            "data": [],
            "tests_to_run": [],
            "untested_changes": [],
            "blind_spots": [],
        },
        "omitted": {"nodes": 0},
        "proof": {"index_files": 117, "index_functions": 5064, "depth": 5, "stopped_early": False},
    }
    d.update(over)
    return d


T, S, N, H = "src/t_string.c", "src/server.c", "src/networking.c", "src/server.h"
GG = "fn:src/t_string.c:getGenericCommand"
CSL = "removed:src/t_string.c:95:checkStringLength"
BUFPOS = "var:src/server.h:client::bufpos"
A2B = "fn:src/networking.c:_addReplyToBuffer"
GET = "fn:src/t_string.c:getCommand"
CALL = "fn:src/server.c:call"


def sample() -> dict:
    nodes = [
        _node(GG, "getGenericCommand", T, 380, "changed", "exact", None, "you changed 6 lines in it", 0),
        _node(CSL, "checkStringLength", T, 95, "removed", "exact", None, "this change removes it", 0),
        _node(BUFPOS, "client.bufpos", H, 1201, "changed", "exact", None, "its declaration changed", 0, kind="field"),
        _node("fn:src/t_string.c:setrangeCommand", "setrangeCommand", T, 612, "breaks", "exact", "calls_removed",
              "still calls checkStringLength, which this change removes", 1, CSL, "checkStringLength (removed)", lines=[619]),
        _node("fn:src/t_string.c:appendCommand", "appendCommand", T, 688, "breaks", "exact", "calls_removed",
              "still calls checkStringLength, which this change removes", 1, CSL, "checkStringLength (removed)", lines=[701]),
        _node(GET, "getCommand", T, 398, "breaks", "exact", "exact",
              "passes 1 argument; getGenericCommand now takes 2", 1, GG, "getGenericCommand", lines=[399]),
        _node("fn:src/t_string.c:getexCommand", "getexCommand", T, 421, "breaks", "probable", "ambiguous",
              "calls a getGenericCommand (2 functions have that name); if it is this one, it passes 1 argument and it now takes 2",
              1, GG, "getGenericCommand", lines=[455]),
        _node(A2B, "_addReplyToBuffer", N, 420, "affected", "exact", "uses_changed",
              "reads and writes client.bufpos, whose declaration changed", 1, BUFPOS, "client.bufpos", lines=[431, 440]),
        _node("fn:src/t_string.c:getdelCommand", "getdelCommand", T, 470, "affected", "probable", "via_pointer",
              "calls a function pointer of the same type as getGenericCommand", 1, GG, "getGenericCommand", lines=[476]),
        _node("fn:src/networking.c:_addReplyProtoToList", "_addReplyProtoToList", N, 460, "affected", "exact", "exact",
              "calls _addReplyToBuffer", 2, A2B, "_addReplyToBuffer", lines=[466]),
        _node(CALL, "call", S, 3780, "affected", "probable", "via_pointer",
              "calls getCommand through c->cmd->proc, a function pointer", 2, GET, "getCommand", lines=[3851]),
        _node("var:src/server.h:redisServer::stat_net_output_bytes", "server.stat_net_output_bytes", H, 1890, "affected",
              "probable", "data", "_addReplyToBuffer writes it; 4 functions read it", 2, A2B, "_addReplyToBuffer",
              kind="global", lines=[445]),
        _node("fn:src/server.c:processCommand", "processCommand", S, 4221, "affected", "probable", "exact",
              "calls call, 3 steps from the change", 3, CALL, "call", lines=[4590]),
        _node("var:src/server.h:client::reply", "client.reply", H, 1205, "unknown", "unknown", "data",
              "_addReplyToBuffer passes it to listAddNodeTail as void *; what happens next is not known", 2, A2B,
              "_addReplyToBuffer", kind="field", lines=[452]),
        _node("fn:src/module.c:RM_Call", "RM_Call", "src/module.c", 6620, "unknown", "unknown", "via_pointer",
              "reaches call from a module; AeroTrace cannot see which modules are loaded", 3, CALL, "call", lines=[6702]),
        _node("fn:src/db.c:lookupKeyReadOrReply", "lookupKeyReadOrReply", "src/db.c", 180, "no_effect", "exact", "exact",
              "getGenericCommand calls it; a change in the caller does not change it", 1, GG, "getGenericCommand"),
    ]  # fmt: skip
    nodes = _sorted(nodes)
    edges = [
        ("fn:src/t_string.c:setrangeCommand", CSL, "calls", "exact"),
        ("fn:src/t_string.c:appendCommand", CSL, "calls", "exact"),
        (GET, GG, "calls", "exact"),
        ("fn:src/t_string.c:getexCommand", GG, "calls", "probable"),
        ("fn:src/t_string.c:getdelCommand", GG, "calls", "probable"),
        (A2B, BUFPOS, "reads", "exact"),
        (A2B, BUFPOS, "writes", "exact"),
        ("fn:src/networking.c:_addReplyProtoToList", A2B, "calls", "exact"),
        (CALL, GET, "calls", "probable"),
        (A2B, "var:src/server.h:redisServer::stat_net_output_bytes", "writes", "exact"),
        ("fn:src/server.c:processCommand", CALL, "calls", "exact"),
        (A2B, "var:src/server.h:client::reply", "reads", "unknown"),
        ("fn:src/module.c:RM_Call", CALL, "calls", "unknown"),
        (GG, "fn:src/db.c:lookupKeyReadOrReply", "calls", "exact"),
    ]
    edges = [{"from": a, "to": b, "kind": k, "certainty": c} for a, b, k, c in sorted(edges)]
    data_kinds = ("field", "global", "static_local")
    sections = {
        "functions": [n["id"] for n in nodes if n["distance"] > 0 and n["kind"] == "function"],
        "data": [n["id"] for n in nodes if n["distance"] > 0 and n["kind"] in data_kinds],
        "tests_to_run": [
            {"name": "tests/unit/type/incr.tcl", "covers": [GET], "why": "calls getCommand"},
            {
                "name": "tests/unit/type/string.tcl",
                "covers": ["fn:src/t_string.c:appendCommand", GET, "fn:src/t_string.c:setrangeCommand"],
                "why": "calls appendCommand, getCommand and setrangeCommand",
            },
        ],
        "untested_changes": [{"node": GG, "why": "no test reaches it"}],
        "blind_spots": [
            {"text": "3 calls through function pointers in src/t_string.c were matched by type only.",
             "scope": "change", "node": None},
            {"text": "Macro uses are not followed anywhere in the index.", "scope": "index", "node": None},
        ],
    }  # fmt: skip
    d = base(nodes=nodes, edges=edges, sections=sections)
    d["change"].update(
        files=2, other_files=1, changed_functions=1, removed_functions=1, changed_declarations=1
    )
    d["change"]["not_followed"]["outside"] = [
        {"file": T, "lines": [40, 41, 42, 43, 44], "other_side_lines": []}
    ]
    d["summary"].update(
        breaks_exact=3,
        breaks_probable=1,
        affected_direct=2,
        affected_further=4,
        unknown=2,
        no_effect=1,
        not_followed=2,
    )
    return d


def nothing() -> dict:
    """One changed function, nothing reaches it, and no part of the change was skipped."""
    d = base(
        nodes=[
            _node(GG, "getGenericCommand", T, 380, "changed", "exact", None, "you changed 2 lines in it", 0)
        ],
    )
    d["sections"]["tests_to_run"] = [
        {"name": "tests/unit/type/string.tcl", "covers": [GG], "why": "calls getGenericCommand"}
    ]
    d["sections"]["blind_spots"] = [
        {"text": "Macro uses are not followed anywhere in the index.", "scope": "index", "node": None}
    ]
    return d


def unclear() -> dict:
    """Nothing found, but part of the change could not be followed: never an all-clear."""
    d = nothing()
    d["change"]["not_followed"]["outside"] = [{"file": T, "lines": [40, 41, 42], "other_side_lines": []}]
    d["change"]["not_followed"]["not_indexed"] = ["src/extra.c"]
    d["summary"]["not_followed"] = 2
    d["sections"]["tests_to_run"] = None
    d["sections"]["untested_changes"] = None
    return d


def affected_only() -> dict:
    d = nothing()
    d["nodes"].append(
        _node(
            GET,
            "getCommand",
            T,
            398,
            "affected",
            "exact",
            "exact",
            "calls getGenericCommand",
            1,
            GG,
            "getGenericCommand",
        )
    )
    d["edges"] = [{"from": GET, "to": GG, "kind": "calls", "certainty": "exact"}]
    d["sections"]["functions"] = [GET]
    d["summary"]["affected_direct"] = 1
    return d


def no_index() -> dict:
    return base(
        "no_index",
        change=None,
        message="Run `aerotrace index` first (Redis takes about a minute), then run `aerotrace check` again.",
    )


def index_error() -> dict:
    return base(
        "index_error",
        change=None,
        message="The index file .aerotrace/index.db is damaged. Run `aerotrace index` to rebuild it.",
    )


def nothing_to_check() -> dict:
    d = base("nothing_to_check")
    d["change"].update(files=0, changed_functions=0, other_files=2)
    return d


def nothing_to_check_gaps() -> dict:
    """No function or declaration could be placed, but C files changed: never an all-clear."""
    d = nothing_to_check()
    d["change"].update(files=2, other_files=0)
    d["change"]["not_followed"]["not_indexed"] = ["src/extra.c"]
    d["change"]["not_followed"]["mismatched"] = ["src/t_string.c"]
    return d


def hostile() -> dict:
    """Names and reasons that try to break out of HTML, JSON and the terminal."""
    d = sample()
    bad = [
        "</script><script>window.__pwned=1</script>",
        'operator<<"&amp;<!--',
        "x' onmouseover='window.__pwned=2",
        "naïve::日本語",
        "a//b http://evil.example/x.js",
    ]
    for n, name in zip(d["nodes"], bad * 4, strict=False):
        n["name"] = name
        n["why"] = name + " <img src=x onerror=window.__pwned=3>"
    return d


# ---------------------------------------------------------------- big graphs


def big(n: int, seed: int = 9) -> dict:
    """A deterministic graph with `n` nodes (1 changed) and about 3 edges per node."""
    rng = random.Random(seed)
    files = [f"src/mod{i:03d}/file{j}.c" for i in range(40) for j in range(5)]
    nodes = [_node("fn:0", "changedFunction", files[0], 100, "changed", "exact", None, "you changed it", 0)]
    by_dist: dict[int, list[str]] = {0: ["fn:0"]}
    for i in range(1, n):
        d = min(1 + int(rng.random() ** 2 * 6), 6)
        while d - 1 not in by_dist:
            d -= 1
        parent = rng.choice(by_dist[d - 1])
        r = rng.random()
        if r < 0.03:
            status, cert = "breaks", "exact"
        elif r < 0.04:
            status, cert = "unknown", "unknown"
        elif r < 0.08:
            status, cert = "no_effect", "exact"
        else:
            status, cert = "affected", rng.choice(("exact", "probable"))
        kind = "global" if rng.random() < 0.05 else "function"
        nid = f"fn:{i}"
        nodes.append(
            _node(nid, f"function_{i:05d}_name", rng.choice(files), rng.randint(1, 9000), status, cert,
                  "exact" if cert == "exact" else "via_pointer", f"calls function_{parent} on this path", d,
                  parent, f"parent_{parent}", kind=kind, lines=[rng.randint(1, 9000)])
        )  # fmt: skip
        by_dist.setdefault(d, []).append(nid)
    ids = [x["id"] for x in nodes]
    edges = set()
    for x in nodes[1:]:
        edges.add((x["id"], x["via"], "calls", x["certainty"]))
        for _ in range(2):
            edges.add((x["id"], rng.choice(ids), rng.choice(("calls", "reads")), "exact"))
    nodes = _sorted(nodes)
    d = base(
        nodes=nodes, edges=[{"from": a, "to": b, "kind": k, "certainty": c} for a, b, k, c in sorted(edges)]
    )
    d["sections"]["functions"] = [x["id"] for x in nodes if x["distance"] and x["kind"] == "function"]
    d["sections"]["data"] = [x["id"] for x in nodes if x["distance"] and x["kind"] != "function"]
    s = d["summary"]
    for x in nodes:
        if x["status"] == "breaks":
            s["breaks_exact"] += 1
        elif x["status"] == "affected":
            s["affected_direct" if x["distance"] == 1 else "affected_further"] += 1
        elif x["status"] in ("unknown", "no_effect"):
            s[x["status"]] += 1
    return d
