# 0004: Collision-safe IDs and a confidence label on every link

- Status: accepted
- Date: 2026-11-08

## Context

The hackathon tool used the clang USR as the function ID. On Redis this silently merged five different
`main()` functions, and two different `static` functions in two files both named `dict.c` (clang builds
the USR of a static C function from the file *name*, not its path). It also showed only proven links, so
functions called through pointers looked like they had no callers.

## Decision

- A function ID is its USR. When one USR is defined in more than one file, every one of those definitions
  gets the ID `<usr>@<repo-relative path>`, and the clash is recorded.
- A call to a clashing USR goes to the definition in the same translation unit when there is one.
  Otherwise it goes to every definition, labelled `ambiguous`.
- Every edge carries a `confidence`: `exact`, `virtual` (may land on an override), `via_pointer`, or
  `ambiguous`. Every answer also lists its blind spots (call sites it could not resolve).
- Later: add the build target (which program a file is linked into) to IDs, once we read link information.

## Consequences

- No more silent merges. Agents can tell proven links from possible ones.
- Answers get a little longer. The MCP tools group by confidence to keep them short.
