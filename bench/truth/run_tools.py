"""Run all four tools on every case and save their raw answers.

Usage: python3 -I bench/truth/run_tools.py <work-dir> <out-dir> [repo ...]
Writes <out-dir>/raw/<case-id>/<tool>.json and <out-dir>/setup-<repo>.json (one-off setup times and
tool versions). Uses the unchanged, built tree <work-dir>/<repo>/base.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tests" / "realrepo"))
sys.path.insert(0, str(HERE.parent / "cases"))
import tools  # noqa: E402


def version(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    return (r.stdout or r.stderr).splitlines()[0].strip()


def write_compdb(base: Path, repo: str) -> None:
    if repo == "curl":
        import curl_build

        curl_build.write_compdb(base)
    else:
        import repos

        repos.write_compdb(repos.REDIS if repo == "redis" else repos.SQLITE, base)


def main() -> None:
    work, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    repos_wanted = sys.argv[3:] or ["redis", "sqlite", "curl"]
    cases = json.loads((HERE.parent / "cases" / "cases.json").read_text())["cases"]
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True
    ).stdout.strip()
    for repo in repos_wanted:
        base = work / repo / "base"
        write_compdb(base, repo)
        setup = {
            "repo": repo,
            "aerotrace_commit": git,
            "versions": {
                "rg": version(["rg", "--version"]),
                "cscope": version(["cscope", "-V"]),
                "clangd": version([tools.CLANGD, "--version"]),
                "aerotrace": tools.aerotrace("--version").stdout.strip(),
            },
        }
        print(f"{repo}: aerotrace index ...", file=sys.stderr)
        setup["aerotrace_index_seconds"] = round(tools.aerotrace_setup(base), 1)
        print(f"{repo}: cscope database ...", file=sys.stderr)
        setup["cscope_db_seconds"] = round(tools.cscope_setup(base, repo), 1)
        mine = [c for c in cases if c["repo"] == repo]
        print(f"{repo}: clangd background index ...", file=sys.stderr)
        server = tools.Clangd(base)
        first = tools.definition_position(base, mine[0])[0]
        setup["clangd_index_seconds"] = round(server.start(first), 1)
        (out / f"setup-{repo}.json").write_text(json.dumps(setup, indent=1, sort_keys=True) + "\n")
        try:
            for c in mine:
                d = out / "raw" / c["id"]
                d.mkdir(parents=True, exist_ok=True)
                for name, fn in (("aerotrace", lambda c=c, b=base: tools.ask_aerotrace(b, c)),
                                 ("ripgrep", lambda c=c, b=base: tools.ask_rg(b, c)),
                                 ("cscope", lambda c=c, b=base: tools.ask_cscope(b, c)),
                                 ("clangd", lambda c=c, b=base, s=server: tools.ask_clangd(s, b, c))):  # fmt: skip
                    try:
                        res = fn()
                    except Exception as e:  # recorded, never hidden
                        res = {"error": f"{type(e).__name__}: {e}"}
                    (d / f"{name}.json").write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
                    print(
                        f"  {c['id']:10} {name:9} {len(res.get('hits', []))} hits "
                        f"{res.get('seconds', '?')} s {res.get('error', '')[:80]}",
                        file=sys.stderr,
                    )
        finally:
            server.stop()


if __name__ == "__main__":
    main()
