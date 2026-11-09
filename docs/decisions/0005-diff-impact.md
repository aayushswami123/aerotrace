# 0005: Diff impact maps lines with the index, and reads the old version from git

- Status: proposed
- Date: 2026-11-08

## Context

`aerotrace diff` has to turn changed lines into changed functions. The index describes one version of the
code: the working tree. A git diff has two sides, though. Added lines line up with the index, but removed
lines belong to the old version, which the index never saw.

This matters most for the most dangerous change: deleting a function. Git often lines a deletion up with
its neighbour (it shows the neighbour's `}` as removed together with the deleted function). Mapped only
against the index, the deletion looked like a small edit to the neighbour, and the deleted function and
its remaining callers were not reported at all. That is a wrong "safe" answer (product rule 5).

The index also keeps one line per variable (its definition) and no prototypes, so a changed `extern` or
prototype in a header could not be placed.

## Decision

- Lines on the side that matches the index (the new side for git, the old side for a patch that is not
  applied yet) are mapped with the index's function and variable line ranges.
- For git diffs, the old version of each changed file is read with `git show` and parsed with libclang,
  using the same compile flags, to get an *outline*: where each function, prototype, global and field sat.
  Removed lines are mapped onto it. A function in the old outline that is not in the index any more is
  reported as removed, with the functions that still call it (`calls_removed`).
- Lines the index can't place are mapped onto an outline of the file as it is now, so prototype and
  `extern` changes reach their definitions.
- Outlines are made one file at a time and never stored. The schema does not change.
- A patch is checked against the files on disk (every old line it shows must match). If it doesn't match,
  the file is reported and skipped, never mapped to the wrong lines.
- Whatever is still unplaced (macros, types, includes) is listed under "Changes AeroTrace could not follow",
  except blank lines.

## Consequences

- Deleted functions, and the code that still calls them, are reported even when git aligns the deletion
  badly. On Redis, deleting `checkStringLength` reports `setrangeCommand` and `appendCommand`.
- One extra libclang parse per changed file with removed lines, plus one for files with unplaced lines.
  On Redis that is well under a second.
- For a patch that is not applied yet, a function the patch deletes may show as "changed" rather than
  "removed" when git splits the deletion across two functions. Its callers are listed either way. Parsing
  the patched version in memory would fix the label; we will do that if the guardrail hook needs it.
- Macro and type uses are still not followed. That needs new facts in the index (a schema change, so a
  separate decision).
