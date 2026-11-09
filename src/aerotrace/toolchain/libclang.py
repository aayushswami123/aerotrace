"""Find and load the libclang we parse with.

We prefer the libclang already on the machine (Xcode, an LLVM install), because it matches the system
headers. The libclang bundled in the `libclang` wheel is the last resort: it is stuck at clang 18, which
can't read new macOS SDK headers. See docs/decisions/0001-python-and-libclang.md.
"""

from __future__ import annotations

import glob
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "AEROTRACE_LIBCLANG"


@dataclass(frozen=True)
class LibclangChoice:
    path: str | None  # None means "the one bundled in the libclang wheel"
    source: str  # where we found it, in plain words


def _version_key(path: str) -> tuple[int, ...]:
    nums = re.findall(r"(?:llvm|clang)[-@]?(\d+)", path)
    return tuple(int(n) for n in nums) or (0,)


def _run(cmd: list[str]) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


def _candidates() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if platform.system() == "Darwin":
        clang = _run(["xcrun", "--find", "clang"])
        if clang:
            found.append((str(Path(clang).parent.parent / "lib" / "libclang.dylib"), "Xcode toolchain"))
        found += [
            ("/Library/Developer/CommandLineTools/usr/lib/libclang.dylib", "Xcode Command Line Tools"),
            (
                "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/libclang.dylib",
                "Xcode",
            ),
            ("/opt/homebrew/opt/llvm/lib/libclang.dylib", "Homebrew LLVM"),
            ("/usr/local/opt/llvm/lib/libclang.dylib", "Homebrew LLVM"),
        ]
    else:
        libdir = _run(["llvm-config", "--libdir"])
        if libdir:
            found += [(p, "llvm-config") for p in sorted(glob.glob(f"{libdir}/libclang.so*"))]
        system = glob.glob("/usr/lib/llvm-*/lib/libclang.so*") + glob.glob("/usr/lib/*/libclang-*.so*")
        found += [(p, "system LLVM") for p in sorted(system, key=_version_key, reverse=True)]
        found += [(p, "system LLVM") for p in sorted(glob.glob("/usr/lib*/libclang.so*"))]
    return found


def find_libclang() -> LibclangChoice:
    """Pick a libclang: $AEROTRACE_LIBCLANG, then the system one, then the bundled one."""
    env = os.environ.get(ENV_VAR)
    if env:
        if not Path(env).is_file():
            raise FileNotFoundError(f"{ENV_VAR} points to {env}, which does not exist")
        return LibclangChoice(env, f"${ENV_VAR}")
    for path, source in _candidates():
        if Path(path).is_file():
            return LibclangChoice(path, source)
    return LibclangChoice(None, "bundled with the libclang Python package")


_configured: LibclangChoice | None = None


def configure(choice: LibclangChoice | None = None) -> LibclangChoice:
    """Load libclang once per process. Call this before creating any clang Index."""
    global _configured
    if _configured is not None:
        return _configured
    from clang import cindex

    choice = choice or find_libclang()
    if choice.path is not None:
        cindex.Config.set_library_file(choice.path)
    _configured = choice
    return choice


def clang_version() -> str:
    """The version string of the loaded libclang, for example 'Apple clang version 17.0.0 (...)'."""
    from clang import cindex

    configure()
    lib = cindex.conf.lib
    lib.clang_getClangVersion.restype = cindex._CXString
    lib.clang_getClangVersion.errcheck = cindex._CXString.from_result
    return str(lib.clang_getClangVersion())
