"""Read-only questions to git: what changed in the working tree compared with a commit.

We only ever read. Every command runs with the user's repo as the working folder, with settings that make
the output stable (no colours, no external diff tools, no pagers, fixed a/ b/ prefixes, paths unquoted).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(Exception):
    """Git is missing, this is not a repo, or a ref does not exist. The message is plain English."""


_SAFE_REF = re.compile(r"^[A-Za-z0-9._/@{}~^:+-]+$")
_SHA = re.compile(r"^[0-9a-f]{40,64}$")

_CONFIG = [
    "-c", "core.quotePath=false",
    "-c", "diff.noprefix=false",
    "-c", "diff.mnemonicPrefix=false",
    "-c", "diff.external=",
    "-c", "color.ui=false",
]  # fmt: skip


def _git(root: Path, *args: str, timeout: int = 120) -> str:
    env = {**os.environ, "GIT_PAGER": "cat", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    env.pop("GIT_EXTERNAL_DIFF", None)
    try:
        r = subprocess.run(
            ["git", *_CONFIG, *args], cwd=root, capture_output=True, env=env, timeout=timeout, check=False
        )
    except FileNotFoundError as e:
        raise GitError("git is not installed or not on PATH.") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} took longer than {timeout}s.") from e
    if r.returncode != 0:
        msg = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise GitError(msg[0] if msg else f"git {args[0]} failed")
    return r.stdout.decode("utf-8", "surrogateescape")


def is_repo(root: Path) -> bool:
    try:
        return _git(root, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except GitError:
        return False


@dataclass(frozen=True)
class Base:
    ref: str  # what the user asked for ("HEAD", "main")
    sha: str  # the commit we compare with: where HEAD left `ref`
    ref_sha: str  # the commit `ref` points at (differs from sha when the branches have split)


def resolve_base(root: Path, ref: str) -> Base:
    """The commit to compare the working tree with: the merge base of `ref` and HEAD."""
    if not is_repo(root):
        raise GitError(f"{root} is not inside a git repository.")
    if not ref or ref.startswith("-") or not _SAFE_REF.match(ref):
        raise GitError(f"'{ref}' is not a valid branch, tag or commit name.")
    try:
        ref_sha = _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except GitError:
        ref_sha = ""
    if not _SHA.match(ref_sha):
        raise GitError(f"git does not know '{ref}'. Use a branch, tag or commit that exists here.")
    try:
        head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}").strip()
    except GitError as e:
        raise GitError("this repository has no commits yet.") from e
    try:
        sha = _git(root, "merge-base", ref_sha, head).strip()
    except GitError as e:
        raise GitError(f"'{ref}' and HEAD have no common history.") from e
    return Base(ref, sha, ref_sha)


def diff_against(root: Path, sha: str) -> str:
    """Unified diff (no context lines) from commit `sha` to the working tree, paths relative to `root`."""
    if not _SHA.match(sha):
        raise GitError(f"not a commit id: {sha}")
    return _git(
        root,
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames",
        "--ignore-submodules",
        "--relative",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "--unified=0",
        sha,
        "--",
    )


def untracked(root: Path) -> list[str]:
    """New files git does not track yet (ignored files left out), relative to `root`, sorted."""
    out = _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", ".")
    return sorted(p for p in out.split("\0") if p)


def prefix(root: Path) -> str:
    """Where `root` sits inside the git repo ("" at the top, "sub/dir/" below it)."""
    return _git(root, "rev-parse", "--show-prefix").strip()


def show(root: Path, sha: str, path: str) -> str | None:
    """The content of `path` (relative to `root`) at commit `sha`, or None when it did not exist there."""
    if not _SHA.match(sha) or path.startswith("-"):
        return None
    try:
        return _git(root, "show", f"{sha}:./{path}")
    except GitError:
        return None
