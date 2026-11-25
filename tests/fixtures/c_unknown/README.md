# c_unknown

Changes AeroTrace can't check (audit A-04). The headline must say UNKNOWN, never "Nothing found":

- `include/defs.h`: a function-like macro (`SCALE`), a typedef (`count_t`) and an enum (`color`), used in
  `src/user.c`. Changing any of them is not followed.
- `src/bad.c` does not compile before any change (`missing_name` is not declared), so an edit there is
  not fully checked.
- `src/lib.c`: `get` (called from `src/user.c`) and `spare`, which nothing calls: a body change in `spare`
  is fully checked and harmless (the green line).
