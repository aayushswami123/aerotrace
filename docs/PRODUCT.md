# AeroTrace: what it is

AeroTrace is the change-safety gate for AI-written C and C++ code.

- **Before an edit**, it says exactly what will break.
- **After the edit**, it proves what happened.

## Who decides what

- **The compiler and the linker decide build and link breaks.** AeroTrace runs them on the edited code
  (see [ADR 0009](decisions/0009-the-compiler-decides.md)) and passes on their own error text. It does not
  guess what they would say.
- **AeroTrace adds what they can't see:** calls through function pointers, data flow through globals and
  struct fields, behaviour changes that still compile, the tests that cover the change, and the history
  of the code.

## Promises

- **Fully local.** No network, no telemetry. Code never leaves the machine.
- **Never runs build scripts.** No `make`, `cmake`, `ninja`, configure scripts or anything else from the
  repo. Only a trusted compiler and linker, one file at a time (ADR 0009).
- **Edits are applied in memory only.** AeroTrace never writes into the user's source tree, except its own
  `.aerotrace/` folder.
- **Deterministic.** The same code, flags and version give the same answer.
- **Never a silent guess.** When it can't be sure, it says UNKNOWN, why, and how to fix it.

## How good it must be

See [QUALITY_BAR.md](QUALITY_BAR.md). Every change is judged against it. The way there is in
[ROADMAP.md](ROADMAP.md).

## Out of scope for now

Dashboards and web UI (paused until Phase 1 passes), languages other than C and C++ (Ada comes later),
cloud and team features.
