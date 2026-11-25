# Benchmark truth and tool runs: exact setup

Everything here runs in temp folders, on copies of the pinned releases. Nothing is built in anyone's own
source tree. The temp folders are deleted at the end. This is the record needed to repeat the run on
another machine (Linux: issue #66).

## Machine and tools (this run)

- Apple M1 Pro, 8 cores, 32 GB, macOS 26.7.2, arm64.
- Compiler and linker: Apple clang 21.0.0 (clang-2100.1.1.101), Apple ld. GNU Make 3.81.
- ripgrep 15.2.0, cscope 15.9 (Homebrew), clangd: Apple clangd 21.0.0 (`/usr/bin/clangd`).
- Python 3.13 for the scripts. AeroTrace at the commit recorded in `setup-<repo>.json`.

The `setup` block of `truth.json` and the `setup-<repo>.json` files record these from the machine.

## Pinned sources

| repo | archive | sha256 |
|---|---|---|
| Redis 8.10.3 | `https://download.redis.io/releases/redis-8.10.3.tar.gz` | `edde778f6bdee7581e42fc139ca9e07e2822212092f7501f99989656e3629d02` |
| SQLite 3.54.1 | `https://www.sqlite.org/2026/sqlite-amalgamation-3540100.zip` | `0a9c8e34352dad46006aa82836d5383bff9dc6bc9ce5a60553c05aa30e6ad1f9` |
| curl 8.23.0 | `https://curl.se/download/curl-8.23.0.tar.gz` | `e3de77cfa6367083c776a26647fc9f633b538c06c3baa73d931a252ac8f72b49` |

## Baselines: one built copy per repo (`<work>/<repo>/base`)

```sh
# Redis: the redis-server program, libc malloc (as in tests/realrepo/repos.py)
cd <work>/redis/base/src && make -j8 MALLOC=libc CFLAGS=-ferror-limit=0 redis-server

# SQLite: the sqlite3 shell, default options
cd <work>/sqlite/base
cc -ferror-limit=0 -c sqlite3.c -o sqlite3.o && cc -ferror-limit=0 -c shell.c -o shell.o && cc shell.o sqlite3.o -o sqlite3

# curl: fixed minimal configure (bench/cases/curl_build.py CONFIGURE_ARGS), then everything
python3 -I bench/cases/curl_build.py configure <work>/curl/base
cd <work>/curl/base && make -j8 CFLAGS="-Qunused-arguments -Werror-implicit-function-declaration -O2 -Werror=partial-availability -ferror-limit=0"
```

The curl `CFLAGS` are configure's own, from `lib/Makefile`, plus `-ferror-limit=0`. They're saved in
`<work>/curl/cflags.txt`.

## Truth (`truth.py`)

For each case:
1. Clone the baseline with `cp -cpR`. That's copy-on-write on APFS, and `-p` keeps file times, so `make`
   rebuilds only what the patch touches.
2. Apply `bench/cases/patches/<id>.diff` with `patch -p1`.
3. Rebuild with `make -k -j1`: Redis `redis-server`; curl `-C lib libcurl.la`, then `-C src curl` (not
   the default target, which also builds the test-only `libcurlu`/`libcurltool` with `-DUNITTESTS`).
   For SQLite, compile both files and link only if both compile.
4. Parse every `error:` line and every `Undefined symbols` entry, then delete the clone.

Builds are serial (`-j1`) on purpose: parallel compilers mix their error lines, which once gave
garbled file names.

## Tools (`run_tools.py`, `tools.py`)

On the unchanged, built baseline, after writing its `compile_commands.json`. Redis and SQLite use
`tests/realrepo/repos.py`; curl uses `bench/cases/curl_build.py compdb`, with the flags from curl's
generated Makefiles.

- AeroTrace: `aerotrace index --root <base>`, then per case `aerotrace check --patch <case.diff> --json --root <base>`.
- ripgrep: `rg -n -w --no-heading --with-filename <name> <files of the build>`.
- cscope: `cscope -b -q -k -i cscope.files` (files of the build), then `cscope -d -L0 <name>`.
- clangd: `clangd --background-index --compile-commands-dir=<base> -j=8`. Wait for the background index
  to finish, then send `textDocument/references` (no declarations) at the symbol's definition.

Each query runs 3 times; the median time is kept.

## Scoring

`score.py`, rules in `docs/benchmark/accuracy-cases-protocol.md`.

## Clean up

```sh
rm -rf <work>   # the baselines, clones, logs and clangd/cscope/AeroTrace indexes all live here
```
