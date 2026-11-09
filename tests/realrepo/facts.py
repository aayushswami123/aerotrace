"""The shape of a golden fact. Facts are plain data, written and checked by hand against the pinned source.

Every fact has `proof`: the exact source lines a person can open to see that the fact is true. The suite
checks those lines against the pinned source first, so a proof can't silently go stale. Only then does it
ask AeroTrace. A fact that just copied the tool's output would prove nothing, so expected answers come
from reading the code (or, for big lists, from parsing the source text directly, never from the tool).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Line:
    """`text` must appear on line `line` of `file` (spaces don't matter)."""

    file: str
    line: int
    text: str


@dataclass(frozen=True)
class Fn:
    """A function as `name@file`, optionally with the confidence label or distance we expect."""

    name: str
    file: str
    confidence: str | None = None
    distance: int | None = None

    def __str__(self) -> str:
        extra = ", ".join(
            x for x in (self.confidence, f"distance {self.distance}" if self.distance else None) if x
        )
        return f"{self.name}@{self.file}" + (f" ({extra})" if extra else "")


@dataclass(frozen=True)
class Call:
    """A call site: `caller@file` calls at `line`. `use` is how the result is used (stored, compared, ...);
    `detail` must appear in the detail AeroTrace gives (e.g. "in o, then compared with ==")."""

    caller: str
    file: str
    line: int
    use: str | None = None
    detail: str | None = None
    n_args: int | None = None
    certainty: str | None = None


@dataclass(frozen=True)
class Addr:
    """A place that uses a function's address: `how` must appear in AeroTrace's description."""

    file: str
    line: int
    how: str


@dataclass(frozen=True)
class Group:
    """Callers further up that come in through one pointer call: `entry` is the function with the pointer
    call (name@file), `line` one of its pointer-call lines, `members` callers that must be in that group."""

    entry: str
    line: int
    members: tuple[Fn, ...]


@dataclass(frozen=True)
class Edit:
    """Replace `old` (must appear exactly once) with `new` in `file`, as a patch that is not applied."""

    file: str
    old: str
    new: str


@dataclass(frozen=True)
class Fact:
    id: str
    why: str  # plain English: what is true, and how the proof lines show it
    kind: str  # callers | callees | impact | writes | reads | find | diff | git_diff | signature
    query: str = ""  # the function asked about, as name@file (kinds other than diff)
    proof: tuple[Line, ...] = ()
    must: tuple = ()  # Fn for function kinds, variable qualified names for writes/reads
    must_not: tuple = ()  # things the tool must never claim here
    exact: bool = False  # `must` is the complete answer at distance 1 (callers/callees) or for find
    depth: int = 1
    # computed expectations, for lists too long to type (e.g. every handler in Redis's command table)
    must_from_source: Callable[[Path], list[Fn]] | None = None
    # signature facts: call sites, places that use the address, pointer calls that may reach it
    calls: tuple[Call, ...] = ()
    addresses: tuple[Addr, ...] = ()
    pointer_calls: tuple[Call, ...] = ()
    returns_void: bool | None = None
    # git_diff facts: the edits are made in the working tree of a fresh git copy, then `aerotrace diff`
    only_files: tuple[str, ...] = ()  # compile only these files (keeps the test fast); () = all
    # ranked signature answers: these call sites (caller, file, line) appear in this order in the text
    ranked: tuple[tuple[str, str, int], ...] = ()
    groups: tuple[Group, ...] = ()  # pointer groups of callers further up (needs depth >= 1)
    max_lines: int | None = None  # the default text answer has at most this many lines
    text_must_not: tuple[str, ...] = ()  # words the default text must not use before "Limits of this answer"
    # diff facts
    edits: tuple[Edit, ...] = ()
    changed: tuple[str, ...] = ()  # name@file of functions the diff changes
    changed_state: dict[str, str] = field(default_factory=dict)  # name@file -> new | removed | changed
    changed_decls: tuple[str, ...] = ()
    no_outside: bool = False  # every changed line must be placed (no "could not follow")
    # a fact AeroTrace gets wrong today. The test still runs; it must keep failing (strict xfail), so a fix
    # shows up as "unexpectedly passed" and the mark gets removed.
    known_bug: str | None = None
