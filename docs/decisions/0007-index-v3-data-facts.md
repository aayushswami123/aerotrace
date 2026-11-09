# 0007: Index format 3: call sites, address uses, escapes, initializers, missing members

- Status: accepted (approved by the owner before the work started)
- Date: 2026-11-08

## Context

Build item 5 (data dependencies) asks questions the index could not answer. It stored one fact per caller,
callee and line ("f calls g at line 12"), and reads/writes per function. It did not know:

- how many arguments each call passes, or what the caller does with the return value;
- where a function is named as a value (a handler table, a callback registration), which a signature
  change also breaks;
- where the address of a global or field goes somewhere we can't follow (`foo(&x)`, `p = &x`);
- which fields a global's initializer sets (`{.proc = f}` at file scope belongs to no function);
- where code still uses a field that no longer exists (the only exact record after a field is removed in
  the working tree is the compiler error).

## Decision

`SCHEMA_VERSION` goes from 2 to 3. Five tables are added (no existing table changes):

| Table | One row per |
|---|---|
| `call_sites` | call (and possible target): caller, callee (NULL for a pointer call, then `via_type`), line, column, number of arguments, how the result is used (`ignored`, `discarded`, `stored`, `compared`, `checked`, `passed`, `returned`, `called`, `used`) and a short detail |
| `address_sites` | place a function is named as a value: in which function or global initializer, and how (stored, passed to f() as argument N, compared) |
| `escapes` | place the address of a global or field goes where we can't follow it |
| `initializers` | field set by a global's designated initializer |
| `missing_members` | compiler error "no member named X in S" |

The plan named four tables; `initializers` was added because a file-scope initializer has no function to
attach a write to.

Later additions, same format (3), parser version 12:

- `escapes.function_id` may hold the id of a global instead of a function: `&x` stored in that global's
  file-scope initializer (a table such as Redis `static_configs`). No function does it, and the format
  has no file column; the line is in that global's file (#27).
- `initializers` also has fields set by position (`{1, 2}`), when each value can be matched to a field
  for sure (#26).
- `missing_members` also has "field designator X does not refer to any field in S", and "use of
  undeclared identifier X" with `record` = '' (a removed global, or a C++ member used without `this->`) (#25).

How a result is used is read from the call's parent expressions (skipping parentheses and implicit
conversions). Operators come from libclang's operator kind, never from tokens: reading tokens of large
macro expansions made SQLite's index 30 times slower in the first draft.

A call that does not compile (wrong number of arguments) is kept as a call site, marked "does not
compile". After a parameter is added, those are exactly the calls that matter.

## Consequences

- Old indexes are rebuilt (they have another schema version); `PARSER_VERSION` is 7.
- Index time: Redis 4.6 s (unchanged), SQLite 4.9 s (was 3.8 s).
- The canonical dump (`tests/golden/*.txt`) lists the new facts, so they are covered by the golden tests.
- Found while building this: calls that do not compile get no call link and make their function count as
  "address taken" (a bug in the call graph, reported separately; this format change does not alter it).
