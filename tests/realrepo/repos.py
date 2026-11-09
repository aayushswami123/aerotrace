"""The pinned real repos for golden tests: where to get them, how to check them, how to unpack them.

Sources are downloaded once into a cache folder (CI caches it), checked against a pinned SHA-256, and
unpacked fresh into a temporary folder for every test run, so a test can never see a stale index.
We never run a repo's own build. Compile flags are written here, copied from the repo's Makefile.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Pinned:
    name: str
    version: str
    url: str
    sha256: str  # of the downloaded archive; checked before it is unpacked
    folder: str  # the top folder inside the archive
    where_hash_comes_from: str


REDIS = Pinned(
    name="redis",
    version="8.10.3",
    url="https://download.redis.io/releases/redis-8.10.3.tar.gz",
    sha256="edde778f6bdee7581e42fc139ca9e07e2822212092f7501f99989656e3629d02",
    folder="redis-8.10.3",
    where_hash_comes_from="https://github.com/redis/redis-hashes (README, line for redis-8.10.3.tar.gz)",
)

SQLITE = Pinned(
    name="sqlite",
    version="3.54.1",
    url="https://www.sqlite.org/2026/sqlite-amalgamation-3540100.zip",
    sha256="0a9c8e34352dad46006aa82836d5383bff9dc6bc9ce5a60553c05aa30e6ad1f9",
    folder="sqlite-amalgamation-3540100",
    # sqlite.org publishes SHA3-256: 92281c86e2957f9b9305890f9ac04204caa7b677cc868dc326d942689df28e30
    where_hash_comes_from="https://www.sqlite.org/download.html (SHA3-256 matches; SHA-256 computed from it)",
)

ALL = (REDIS, SQLITE)

# The full SQLite source tree, with its Tcl tests. Used only by the opt-in coverage goldens
# (test_coverage_golden.py, marker realrepo_coverage), which build it: see the README.
SQLITE_SRC = Pinned(
    name="sqlite-src",
    version="3.54.1",
    url="https://www.sqlite.org/2026/sqlite-src-3540100.zip",
    sha256="387e7f6cad4797b17848c07d453dc6606917bb356a6cfe5a3abafd38c240c71b",
    folder="sqlite-src-3540100",
    # sqlite.org publishes SHA3-256: 877c6f4996ce7adb368686bce21638de9e3d91534832ad3d9d119037d1bc22ad
    where_hash_comes_from="https://www.sqlite.org/download.html (SHA3-256 matches; SHA-256 computed from it)",
)
COVERAGE = (REDIS, SQLITE, SQLITE_SRC)


def cache_dir() -> Path:
    return Path(os.environ.get("AEROTRACE_REALREPO_CACHE", Path.home() / ".cache" / "aerotrace-realrepo"))


def archive(p: Pinned) -> Path:
    return cache_dir() / p.url.rsplit("/", 1)[1]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(p: Pinned) -> Path:
    """The checked archive, downloading it first when it is not in the cache."""
    path = archive(p)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        # Some mirrors refuse Python's default User-Agent (download.redis.io answers 403).
        req = urllib.request.Request(p.url, headers={"User-Agent": "aerotrace-golden-tests/1"})
        with urllib.request.urlopen(req, timeout=120) as r, tmp.open("wb") as out:
            shutil.copyfileobj(r, out)
        tmp.replace(path)
    got = _sha256(path)
    if got != p.sha256:
        path.unlink()
        raise RuntimeError(f"{p.name} {p.version}: SHA-256 is {got}, expected {p.sha256}. Download removed.")
    return path


def unpack(p: Pinned, dest: Path) -> Path:
    """Unpack the checked archive into `dest`; returns the repo root (with a compile_commands.json for the
    repos we index; the SQLite source tree is only built, never indexed)."""
    path = fetch(p)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            z.extractall(dest)
            for info in z.infolist():  # zipfile drops the executable bit; scripts like ./configure need it
                mode = (info.external_attr >> 16) & 0o777
                if mode & 0o111 and not info.is_dir():
                    (dest / info.filename).chmod(mode)
    else:
        with tarfile.open(path) as t:
            t.extractall(dest, filter="data")
    root = dest / p.folder
    if p is not SQLITE_SRC:
        write_compdb(p, root)
    return root


# ---------------------------------------------------------------- compile flags (copied from each build)


def _redis_server_files(root: Path) -> list[str]:
    """The .c files of the redis-server binary: REDIS_SERVER_OBJ in src/Makefile."""
    for line in (root / "src" / "Makefile").read_text().splitlines():
        if line.startswith("REDIS_SERVER_OBJ="):
            return sorted(o[:-2] + ".c" for o in line.split("=", 1)[1].split())
    raise RuntimeError("REDIS_SERVER_OBJ not found in src/Makefile")


def write_compdb(p: Pinned, root: Path) -> None:
    if p is REDIS:
        # src/Makefile: STD (-pedantic -DREDIS_STATIC='' -std=gnu11), FINAL_CFLAGS includes (line 279),
        # MALLOC=libc (jemalloc's headers only exist after building deps, and we never run the build).
        flags = ["-pedantic", "-DREDIS_STATIC=", "-std=gnu11", "-Wno-c11-extensions"]
        incs = ["hiredis", "linenoise", "lua/src", "hdr_histogram", "fpconv", "xxhash"]
        flags += [f"-I../deps/{d}" for d in incs]
        directory = root / "src"
        files = _redis_server_files(root)
    else:
        # The amalgamation's own build instructions: cc shell.c sqlite3.c (no extra flags needed).
        flags, directory, files = [], root, ["shell.c", "sqlite3.c"]
    entries = [
        {"directory": str(directory), "file": f, "arguments": ["cc", *flags, "-c", f, "-o", f[:-2] + ".o"]}
        for f in files
    ]
    (root / "compile_commands.json").write_text(json.dumps(entries, indent=1))


if __name__ == "__main__":  # used by CI: download everything into the cache before the tests run
    import sys

    for pinned in COVERAGE if "--coverage" in sys.argv else ALL:
        print(f"{pinned.name} {pinned.version}: {fetch(pinned)}")
