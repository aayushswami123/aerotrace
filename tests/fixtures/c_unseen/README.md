# c_unseen

Uses AeroTrace can't see (review of #84). `f` is only called under `#ifdef USE_F` (not set), and `h` only
from `src/out.c`, which the tests drop from compile_commands.json. Changing the signature of either, or
removing it, is never a clear answer. Body, comment and blank-line edits are.
