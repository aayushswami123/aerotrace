"""Risk levels for a proposed edit, and the repo's own rules in `.aerotrace/rules.toml`.

Levels come from the `aerotrace-check` summary (docs/plans/hook.md, section 3):
  high   = at least one exact break;
  medium = a probable break, or `medium_affected` or more affected functions and data;
  low    = anything else (the hook stays silent, unless a part of the edit was not checked: A-09).
Rules can only make the answer stricter (warn -> block); they never hide a warning.
"""

from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from aerotrace.analysis import verdict as verdict_mod

LEVELS = ("low", "medium", "high")
ACTIONS = ("warn", "block")
RULES_FILE = Path(".aerotrace") / "rules.toml"
LIST_CAP = 20  # lines in the high-level list
TEST_CAP = 10
# Files that are C tables or includes but not C/C++ sources: AeroTrace does not parse them, so an edit
# is never "checked". (`.inc` is already a header extension of the index.)
TABLE_EXTS = (".def", ".tbl")


@dataclass(frozen=True)
class Rule:
    name: str
    paths: tuple[str, ...]  # globs on repo-relative POSIX paths; empty = every file
    level: str  # the lowest level at which the rule fires
    action: str  # warn | block


@dataclass
class Rules:
    medium_affected: int = 10
    timeout_seconds: float = 5.0
    rules: list[Rule] = field(default_factory=list)


def _rule(i: int, r: object) -> Rule:
    if not isinstance(r, dict):
        raise ValueError(f"rule {i} is not a table")
    unknown = sorted(set(r) - {"name", "paths", "level", "action"})
    if unknown:
        raise ValueError(f"rule {i}: unknown key '{unknown[0]}'")
    name = r.get("name", f"rule {i}")
    paths = r.get("paths", [])
    level = r.get("level", "high")
    action = r.get("action", "warn")
    if not isinstance(name, str):
        raise ValueError(f"rule {i}: name must be a string")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ValueError(f"rule '{name}': paths must be a list of strings")
    if level not in LEVELS:
        raise ValueError(f"rule '{name}': level must be one of {', '.join(LEVELS)}")
    if action not in ACTIONS:
        raise ValueError(f"rule '{name}': action must be warn or block")
    return Rule(name, tuple(paths), level, action)


def parse_rules(text: str) -> Rules:
    """Raises ValueError (with a short reason) when the file is not valid."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"not valid TOML ({e})") from e
    unknown = sorted(set(data) - {"thresholds", "rule"})
    if unknown:
        raise ValueError(f"unknown key '{unknown[0]}'")
    out = Rules()
    th = data.get("thresholds", {})
    if not isinstance(th, dict):
        raise ValueError("thresholds must be a table")
    for k, v in th.items():
        if k == "medium_affected":
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise ValueError("thresholds.medium_affected must be a whole number of 1 or more")
            out.medium_affected = v
        elif k == "timeout_seconds":
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v <= 25:
                raise ValueError("thresholds.timeout_seconds must be a number above 0 and at most 25")
            out.timeout_seconds = float(v)
        else:
            raise ValueError(f"unknown key 'thresholds.{k}'")
    rules = data.get("rule", [])
    if not isinstance(rules, list):
        raise ValueError("use [[rule]] for rules")
    out.rules = [_rule(i, r) for i, r in enumerate(rules, 1)]
    return out


def load_rules(root: Path) -> tuple[Rules, str]:
    """(rules, warning). A missing file gives the defaults; a bad file gives the defaults and a warning."""
    path = root / RULES_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Rules(), ""
    except (OSError, UnicodeDecodeError) as e:
        return Rules(), f"AeroTrace ignored {RULES_FILE.as_posix()}: it could not be read ({e})."
    try:
        return parse_rules(text), ""
    except ValueError as e:
        return Rules(), f"AeroTrace ignored {RULES_FILE.as_posix()}: {e}. Using the defaults (warn only)."


def level(contract: dict, rules: Rules) -> str:
    s = contract.get("summary") or {}
    if s.get("breaks_exact"):
        return "high"
    if s.get("breaks_probable"):
        return "medium"
    if s.get("affected_direct", 0) + s.get("affected_further", 0) >= rules.medium_affected:
        return "medium"
    return "low"


def matches(rule: Rule, rel: str) -> bool:
    return not rule.paths or any(fnmatch.fnmatchcase(rel, p) for p in rule.paths)


def blocking(rules: Rules, rel: str, lvl: str) -> list[Rule]:
    """Rules that block an edit of `rel` at level `lvl`, in file order."""
    at = LEVELS.index(lvl)
    return [r for r in rules.rules if r.action == "block" and matches(r, rel) and at >= LEVELS.index(r.level)]


# ---------------------------------------------------------------- the text the agent sees


def _where(n: dict) -> str:
    return f"{n['file']}:{n['line']}" if n.get("line") else n["file"]


def _counts(s: dict) -> str:
    parts = []
    if s.get("breaks_exact"):
        parts.append(f"{s['breaks_exact']} sure break(s)")
    if s.get("breaks_probable"):
        parts.append(f"{s['breaks_probable']} probable break(s)")
    affected = s.get("affected_direct", 0) + s.get("affected_further", 0)
    if affected:
        parts.append(f"{affected} affected function(s) or data")
    if s.get("unknown"):
        parts.append(f"{s['unknown']} unknown")
    return ", ".join(parts) or "no effects found"


def _limits(contract: dict) -> list[str]:
    v = verdict_mod.of(contract)
    if not v.unchecked:
        return []
    why = f": {v.reason}" if v.reason else ""
    return [f"{v.parts} part(s) of this edit could not be checked{why} (run `aerotrace check` for the list)."]


def unchecked_text(contract: dict) -> str:
    """One line for an edit with nothing above the threshold but a part not checked or not followed
    (the hook is never silent when it could not check). "" when every part was checked."""
    v = verdict_mod.of(contract)
    if not v.unchecked:
        return ""
    why = f" ({v.reason}{f', and {v.parts - 1} more' if v.parts > 1 else ''})" if v.reason else ""
    return f"AeroTrace could not check this edit{why}. Build before you go on."


def not_code_text(rel: str) -> str:
    """One line for an edit to a file that is not C/C++ but changes what is built (a build file, a C
    table file). "" for any other file: those are not AeroTrace's business."""
    from aerotrace.analysis.diff import is_build_file

    if rel.lower().endswith(TABLE_EXTS):
        what = f"{rel} is a C table file it does not follow"
    elif is_build_file(rel):
        what = f"a build file changed: {rel}"
    else:
        return ""
    return f"AeroTrace could not check this edit ({what}). Build before you go on."


def short_text(contract: dict) -> str:
    """Medium level: a few lines."""
    s = contract["summary"]
    nodes = [n for n in contract["nodes"] if n["distance"] and n["status"] in ("breaks", "affected")]
    top = ", ".join(f"{n['name']} ({_where(n)})" for n in nodes[:3])
    head = "may break code" if s.get("breaks_probable") or s.get("breaks_exact") else "affects code"
    lines = [f"AeroTrace: this edit {head}: {_counts(s)}."]
    if top:
        lines.append(f"Most at risk: {top}.")
    lines += _limits(contract)
    lines.append("Run `aerotrace check` (or the MCP check tool) on the change for the full list.")
    return "\n".join(lines)


def full_text(contract: dict, tests: list[str] | None, tests_note: str = "") -> str:
    """High level: the changed things, what breaks or is affected, data, and the tests to run."""
    s = contract["summary"]
    lines = [f"AeroTrace: this edit breaks code: {_counts(s)}."]
    nodes = contract["nodes"]
    changed = [n for n in nodes if n["distance"] == 0]
    rest = [n for n in nodes if n["distance"] and n["status"] in ("breaks", "affected", "unknown")]
    shown = 0
    if changed:
        lines.append("Changed:")
        for n in changed[:LIST_CAP]:
            lines.append(f"  {n['name']} ({_where(n)}): {n['why']}")
            shown += 1
    fns = [n for n in rest if n["kind"] == "function"]
    dat = [n for n in rest if n["kind"] != "function"]
    for title, group in (("Functions:", fns), ("Data:", dat)):
        if group and shown < LIST_CAP:
            lines.append(title)
            for n in group[: LIST_CAP - shown]:
                tag = f"{n['status']}, {n['certainty']}" if n["status"] == "breaks" else n["status"]
                lines.append(f"  [{tag}] {n['name']} ({_where(n)}): {n['why']}")
                shown += 1
    left = len(changed) + len(rest) - shown + (contract.get("omitted") or {}).get("nodes", 0)
    if left > 0:
        lines.append(f"  ... and {left} more (run `aerotrace check` for the full list).")
    if tests:
        lines.append("Tests to run:")
        lines += [f"  {t}" for t in tests[:TEST_CAP]]
        if len(tests) > TEST_CAP:
            lines.append(f"  ... and {len(tests) - TEST_CAP} more.")
    elif tests_note:
        lines.append(f"Tests: {tests_note}")
    lines += _limits(contract)
    return "\n".join(lines)


def blocked_text(text: str, rules: list[Rule]) -> str:
    names = ", ".join(f"'{r.name}'" for r in rules)
    return (
        f"{text}\nBlocked by rule {names} in {RULES_FILE.as_posix()}. Ask the user before making this edit, "
        "or change the edit."
    )
