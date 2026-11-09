"""Benchmark Part B harness (pilot): `uv run bench/run.py prepare|run|judge|report`.

Not part of the aerotrace package. It builds third-party code and sends code to a model provider through
Claude Code, so it lives here, outside src/. Protocol: docs/benchmark/protocol.md.

The pilot is small: Sonnet, tasks R03, R04, B01 and C02, one run per side, a hard money limit.
Work folders go under $BENCH_ROOT (default /tmp/at-pilot-sonnet); logs go under bench/results/<batch>/.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bench"))
from tasks import REDIS_SUFFIX, TASKS, Task  # noqa: E402

_spec = importlib.util.spec_from_file_location("repos", REPO / "tests" / "realrepo" / "repos.py")
repos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repos)  # type: ignore[union-attr]

BATCH = os.environ.get("BENCH_BATCH", "pilot-sonnet")
BENCH_ROOT = Path(os.environ.get("BENCH_ROOT", "/tmp/at-pilot-sonnet"))
RESULTS = REPO / "bench" / "results" / BATCH
JUDGE_SRC = REPO / "bench" / "judge"
CLAUDE = Path.home() / ".local" / "bin" / "claude"
AEROTRACE = REPO / ".venv" / "bin" / "aerotrace"

PILOT_TASKS = ("R03", "R04", "B01", "C02")
MODEL_ALIAS = "sonnet"
BUDGET_USD = 25.0
OVERSHOOT_RESERVE_USD = 0.5  # --max-budget-usd stops after the turn that passes it; keep room for that
RUN_CAP_MAX_USD = 6.0
TIMEOUT_S = 45 * 60
JOBS = 2
SETUP_JOBS = 4
MODEL_ENV = (
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_FABLE_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
)
NAME_ONLY_ENV = ("CLAUDE_CODE_USE_BEDROCK", "AWS_REGION", "AWS_PROFILE", "ANTHROPIC_MODEL")


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(cmd: list[str] | str, cwd: Path | None = None, log: Path | None = None, **kw) -> int:
    out = log.open("w") if log else subprocess.DEVNULL
    try:
        return subprocess.run(
            cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, shell=isinstance(cmd, str), **kw
        ).returncode
    finally:
        if log:
            out.close()


def out(cmd: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True).stdout.strip()


def jload(p: Path, default=None):
    return json.loads(p.read_text()) if p.is_file() else default


def jsave(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def pair_order() -> list[str]:
    return sorted(PILOT_TASKS, key=lambda t: hashlib.sha256(f"{t}/1".encode()).hexdigest())


# ---------------------------------------------------------------- environment


def clean_env(extra: dict[str, str]) -> dict[str, str]:
    """Only what Claude Code needs to reach the model, plus the slot's variables. Same on both sides."""
    keep = ("HOME", "USER", "LOGNAME", "LANG", "AWS_PROFILE", "AWS_REGION", "CLAUDE_CODE_USE_BEDROCK")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update({k: os.environ[k] for k in MODEL_ENV if k in os.environ})
    env["PATH"] = f"{CLAUDE.parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    env["SHELL"] = "/bin/zsh"
    env["TERM"] = "dumb"
    env["DISABLE_AUTOUPDATER"] = "1"
    env.update(extra)
    return env


def env_record() -> dict:
    return {
        "model_env": {k: os.environ.get(k) for k in MODEL_ENV},
        "set_env_names": sorted(k for k in NAME_ONLY_ENV if k in os.environ),
    }


def credentials_ok() -> tuple[bool, str]:
    """SSO role credentials are short (1 h) but refresh by themselves while the SSO session lasts."""
    prof = os.environ.get("AWS_PROFILE", "")
    r = subprocess.run(
        ["aws", "configure", "export-credentials", "--profile", prof], capture_output=True, text=True
    )
    if r.returncode != 0:
        return False, r.stderr.strip()[:300]
    exp = json.loads(r.stdout).get("Expiration", "")
    return True, f"role credentials valid until {exp}"


def ports_free(base: int, count: int) -> bool:
    for p in range(base, base + count):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                return False
    return True


def kill_redis_under(folder: Path) -> list[int]:
    """Kill every redis-server whose working folder is inside `folder` (agents may leave servers running)."""
    r = subprocess.run(["lsof", "-a", "-d", "cwd", "-c", "redis-", "-Fpn"], capture_output=True, text=True)
    killed, pid = [], None
    for line in r.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid and line[1:].startswith(str(folder.resolve())):
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                pass
    return killed


# ---------------------------------------------------------------- ledger (money)


def ledger() -> list[dict]:
    return jload(RESULTS / "ledger.json", [])


def spent() -> float:
    return round(sum(e["cost_usd"] for e in ledger()), 4)


def add_cost(what: str, cost: float, estimated: bool = False) -> None:
    led = ledger()
    led.append({"what": what, "cost_usd": round(cost, 4), "estimated": estimated, "at": now()})
    jsave(RESULTS / "ledger.json", led)


# ---------------------------------------------------------------- stream-json


def read_stream(p: Path) -> list[dict]:
    events = []
    if p.is_file():
        for line in p.read_text(errors="replace").splitlines():
            with contextlib.suppress(ValueError):
                events.append(json.loads(line))
    return events


def result_event(events: list[dict]) -> dict | None:
    return next((e for e in reversed(events) if e.get("type") == "result"), None)


def init_event(events: list[dict]) -> dict | None:
    return next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)


def claude_cmd(prompt: str, model: str, cap: float, mcp: Path | None) -> list[str]:
    cmd = [str(CLAUDE), "-p", prompt, "--model", model, "--effort", "high"]
    cmd += ["--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions"]
    cmd += ["--setting-sources", "project", "--strict-mcp-config"]
    if mcp:
        cmd += ["--mcp-config", str(mcp)]
    cmd += ["--no-session-persistence", "--max-budget-usd", f"{cap:.2f}"]
    return cmd


def seed_home() -> Path:
    return BENCH_ROOT / "seed-claude-home"


def listing(folder: Path) -> list[str]:
    rows = []
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            rows.append(f"{p.relative_to(folder)}\t{p.stat().st_size}\t{h}")
    return rows


# ---------------------------------------------------------------- prepare


def tasks_md_prompt(task_id: str) -> str:
    md = (REPO / "docs" / "benchmark" / "tasks.md").read_text()
    sec = md.split(f"\n## {task_id}", 1)[1]
    return sec.split("Prompt:\n\n```text\n", 1)[1].split("\n```", 1)[0]


def tasks_md_suffix() -> str:
    md = (REPO / "docs" / "benchmark" / "tasks.md").read_text()
    return md.split("Redis suffix:\n\n```text\n", 1)[1].split("\n```", 1)[0]


def cmd_prepare(_: argparse.Namespace) -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    meta: dict = {"at": now(), "pair_order": pair_order(), **env_record()}
    # 1. prompts byte-identical to tasks.md
    bad = [t for t in PILOT_TASKS if TASKS[t].prompt != tasks_md_prompt(t)]
    if bad or tasks_md_suffix() != REDIS_SUFFIX:
        print(f"prompt mismatch with tasks.md: {bad or 'suffix'}")
        return 1
    meta["prompts_match_tasks_md"] = True
    # 2. pinned archive
    meta["redis_archive"] = {"path": str(repos.fetch(repos.REDIS)), "sha256": repos.REDIS.sha256}
    # 3. versions
    meta["claude_code_version"] = out([str(CLAUDE), "--version"])
    meta["aerotrace_version"] = out([str(AEROTRACE), "--version"])
    meta["aerotrace_git"] = out(["git", "rev-parse", "HEAD"], cwd=REPO)
    meta["machine"] = {"cpus": os.cpu_count(), "uname": " ".join(os.uname())}
    # 4. seed config dir: empty
    shutil.rmtree(seed_home(), ignore_errors=True)
    seed_home().mkdir(parents=True)
    meta["seed_listing"] = listing(seed_home())
    # 5. credentials and model id
    ok, why = credentials_ok()
    meta["credentials"] = why
    if not ok:
        print(f"credentials: {why}")
        return 1
    smoke = BENCH_ROOT / "smoke"
    shutil.rmtree(smoke, ignore_errors=True)
    (smoke / "work").mkdir(parents=True)
    shutil.copytree(seed_home(), smoke / "home")
    cmd = claude_cmd("Reply with exactly: OK", MODEL_ALIAS, 0.5, None)
    log = RESULTS / "prepare-smoke.jsonl"
    sh(cmd, cwd=smoke / "work", log=log, env=clean_env({"CLAUDE_CONFIG_DIR": str(smoke / "home")}))
    ev = read_stream(log)
    res, ini = result_event(ev), init_event(ev)
    if not res or res.get("is_error"):
        print("smoke run failed; see", log)
        return 1
    add_cost("prepare smoke run (model alias resolution, credential check)", res.get("total_cost_usd", 0))
    meta["model_alias"] = MODEL_ALIAS
    meta["model_id"] = ini.get("model") if ini else None
    meta["smoke_model_usage"] = sorted(res.get("modelUsage", {}))
    # 6. pristine Redis build (for probe-b01) and empty-diff check validation
    pr = BENCH_ROOT / "pristine"
    if not (pr / "redis-8.10.3" / "src" / "redis-server").is_file():
        shutil.rmtree(pr, ignore_errors=True)
        root = repos.unpack(repos.REDIS, pr)
        rc = sh(["make", "-C", "src", f"-j{os.cpu_count()}", "MALLOC=libc"], cwd=root, log=pr / "build.log")
        if rc:
            print("pristine build failed")
            return 1
    meta["empty_diff_checks"] = {}
    for t in PILOT_TASKS:
        jd = BENCH_ROOT / "empty-check" / t
        shutil.rmtree(jd, ignore_errors=True)
        jd.mkdir(parents=True)
        res_checks = run_checks(TASKS[t], pr / "redis-8.10.3", jd, 29000)
        meta["empty_diff_checks"][t] = {c["name"]: c["pass"] for c in res_checks}
        if all(c["pass"] for c in res_checks):
            print(f"{t}: every check passes on the unchanged sources: broken check")
            return 1
    jsave(RESULTS / "prepare.json", meta)
    print(json.dumps({k: meta[k] for k in ("model_id", "claude_code_version", "pair_order")}, indent=1))
    print(f"spent so far: ${spent():.4f}")
    return 0


# ---------------------------------------------------------------- run


def setup_checkout(run_tmp: Path, arm: str, log_dir: Path) -> tuple[Path, dict]:
    """protocol.md, "Setting up one run": fresh unpack, build once, git baseline, AeroTrace on "with"."""
    info: dict = {}
    root = repos.unpack(repos.REDIS, run_tmp / "checkout")
    t0 = time.monotonic()
    rc = sh(
        ["make", "-C", "src", f"-j{SETUP_JOBS}", "MALLOC=libc"], cwd=root, log=log_dir / "setup-build.log"
    )
    info["setup_build_rc"], info["setup_build_s"] = rc, round(time.monotonic() - t0, 1)
    if rc:
        raise RuntimeError(f"setup build failed in {root}")
    git = ["git", "-c", "user.name=bench", "-c", "user.email=bench@localhost"]
    sh(["git", "init", "-q"], cwd=root)
    (root / ".git" / "info" / "exclude").write_text(".aerotrace/\n.mcp.json\n.claude/\n")
    sh(["git", "add", "-A"], cwd=root)
    sh([*git, "commit", "-qm", "baseline"], cwd=root)
    info["baseline_dirty"] = out(["git", "status", "--porcelain"], cwd=root)
    if arm == "with":
        t0 = time.monotonic()
        rc = sh([str(AEROTRACE), "index"], cwd=root, log=log_dir / "aerotrace-index.log")
        info["index_rc"], info["index_s"] = rc, round(time.monotonic() - t0, 1)
        rc2 = sh([str(AEROTRACE), "install", "--client", "claude"], cwd=root, log=log_dir / "install.log")
        info["install_rc"] = rc2
        status = subprocess.run([str(AEROTRACE), "status"], cwd=root, capture_output=True, text=True)
        (log_dir / "aerotrace-status.log").write_text(status.stdout + status.stderr)
        info["status_up_to_date"] = "up to date" in status.stdout.lower()
        for f in (".mcp.json", ".claude/settings.json"):
            if (root / f).is_file():
                shutil.copy(root / f, log_dir / f.replace("/", "_"))
        if rc or rc2 or not info["status_up_to_date"]:
            raise RuntimeError(f"AeroTrace setup failed in {root}: {info}")
    return root, info


def run_agent(task: Task, arm: str, slot: int, cap: float, model: str) -> dict:
    name = f"{task.id}-{arm}"
    run_tmp = BENCH_ROOT / "runs" / name
    log_dir = RESULTS / name
    shutil.rmtree(run_tmp, ignore_errors=True)
    shutil.rmtree(log_dir, ignore_errors=True)
    run_tmp.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    base = 22000 + 1000 * slot
    meta: dict = {"task": task.id, "arm": arm, "slot": slot, "cap_usd": cap, "model": model}
    if not ports_free(base, 601):
        meta["harness_failure"] = f"ports {base}..{base + 600} not free"
        jsave(log_dir / "meta.json", meta)
        return meta
    root, info = setup_checkout(run_tmp, arm, log_dir)
    meta["setup"] = info
    home = run_tmp / "claude-home"
    shutil.copytree(seed_home(), home)
    (run_tmp / "tmp").mkdir()
    env = clean_env(
        {
            "CLAUDE_CONFIG_DIR": str(home),
            "TMPDIR": str(run_tmp / "tmp"),
            "BENCH_JOBS": str(JOBS),
            "BENCH_BASEPORT": str(base),
            "BENCH_PORT": str(base + 600),
        }
    )
    meta["env_names"] = sorted(env)
    mcp = root / ".mcp.json" if arm == "with" else None
    cmd = claude_cmd(task.full_prompt, model, cap, mcp)
    meta["cmd"] = [c if c != task.full_prompt else "<PROMPT>" for c in cmd]
    (log_dir / "prompt.txt").write_text(task.full_prompt + "\n")
    loads: list[float] = []
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            loads.append(os.getloadavg()[0])
            stop.wait(30)

    threading.Thread(target=sample, daemon=True).start()
    meta["started"] = now()
    t0 = time.monotonic()
    with (log_dir / "stream.jsonl").open("w") as so, (log_dir / "stderr.log").open("w") as se:
        p = subprocess.Popen(cmd, cwd=root, env=env, stdout=so, stderr=se, start_new_session=True)
        try:
            meta["exit_code"] = p.wait(timeout=TIMEOUT_S)
            meta["timed_out"] = False
        except subprocess.TimeoutExpired:
            meta["timed_out"] = True
            meta["exit_code"] = None
    meta["wall_s"] = round(time.monotonic() - t0, 1)
    stop.set()
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(p.pid, signal.SIGKILL)
    meta["killed_redis"] = kill_redis_under(run_tmp)
    loads.append(os.getloadavg()[0])
    meta["load"] = {
        "start": loads[0],
        "end": loads[-1],
        "median": statistics.median(loads),
        "max": max(loads),
        "samples": len(loads),
    }
    # the agent's whole change, new files included (excluded paths stay out)
    sh(["git", "add", "-A"], cwd=root)
    diff = subprocess.run(
        ["git", "diff", "--binary", "--cached", "baseline"], cwd=root, capture_output=True
    ).stdout
    (log_dir / "diff.patch").write_bytes(diff)
    (log_dir / "claude-home-after.txt").write_text("\n".join(listing(home)) + "\n")
    ev = read_stream(log_dir / "stream.jsonl")
    res, ini = result_event(ev), init_event(ev)
    meta["init_model"] = ini.get("model") if ini else None
    meta["init_mcp_servers"] = ini.get("mcp_servers") if ini else None
    if res:
        meta["result"] = {
            k: res.get(k)
            for k in (
                "subtype",
                "is_error",
                "duration_ms",
                "duration_api_ms",
                "num_turns",
                "total_cost_usd",
                "usage",
                "modelUsage",
            )
        }
        meta["cost_usd"], meta["cost_estimated"] = res.get("total_cost_usd") or 0.0, False
    else:
        meta["cost_usd"], meta["cost_estimated"] = cap, True  # no result event: count the whole cap
    meta["ended"] = now()
    jsave(log_dir / "meta.json", meta)
    return meta


def _go(results: dict, task: Task, arm: str, slot: int, cap: float, model: str) -> None:
    try:
        results[arm] = run_agent(task, arm, slot, cap, model)
    except Exception as e:  # harness failure: recorded, the pair is not counted
        results[arm] = {"harness_failure": repr(e), "cost_usd": 0.0}


def cmd_run(_: argparse.Namespace) -> int:
    prep = jload(RESULTS / "prepare.json")
    if not prep or not prep.get("model_id"):
        print("run `prepare` first")
        return 1
    model = prep["model_id"]
    order = pair_order()
    done = jload(RESULTS / "pairs.json", {})
    for i, t in enumerate(order):
        if done.get(t, {}).get("status") == "done":
            continue
        pairs_left = len(order) - i
        remaining = BUDGET_USD - OVERSHOOT_RESERVE_USD - spent()
        cap = min(RUN_CAP_MAX_USD, remaining / (2 * pairs_left))
        cap = int(cap * 100) / 100
        if spent() + 2 * cap > BUDGET_USD or cap <= 0.1:
            for rest in order[i:]:
                done[rest] = {"status": "not run", "why": f"budget: spent ${spent():.2f}, cap ${cap:.2f}"}
            jsave(RESULTS / "pairs.json", done)
            break
        ok, why = credentials_ok()
        if not ok:
            print(f"credentials: {why}; stopping cleanly, rerun `run` after logging in")
            return 1
        print(f"[{now()}] pair {t}: cap ${cap:.2f} per run, spent so far ${spent():.2f}", flush=True)
        results: dict[str, dict] = {}
        th = [
            threading.Thread(target=_go, args=(results, TASKS[t], a, s, cap, model))
            for s, a in enumerate(("with", "without"))
        ]
        for x in th:
            x.start()
        for x in th:
            x.join()
        for arm in ("with", "without"):
            m = results[arm]
            add_cost(f"{t}-{arm}", m.get("cost_usd", 0.0), m.get("cost_estimated", False))
        failed = [a for a in results if results[a].get("harness_failure")]
        done[t] = {"status": "harness failure" if failed else "done", "cap_usd": cap, "spent_after": spent()}
        jsave(RESULTS / "pairs.json", done)
        print(
            f"[{now()}] pair {t}: "
            + ", ".join(f"{a} ${results[a].get('cost_usd', 0):.2f}" for a in ("with", "without"))
            + f"; spent ${spent():.2f}",
            flush=True,
        )
        if failed:
            print(f"harness failure in {t}: {[results[a]['harness_failure'] for a in failed]}")
            return 1
        if spent() >= BUDGET_USD:
            for rest in order[i + 1 :]:
                done[rest] = {"status": "not run", "why": f"budget reached: spent ${spent():.2f}"}
            jsave(RESULTS / "pairs.json", done)
            break
    print(f"total spent: ${spent():.4f}")
    return 0


# ---------------------------------------------------------------- judge

PROTECTED = re.compile(r"^(tests/|runtest|deps/|utils/|src/cli_commands\.[ch]$)|(^|/)Makefile$")
BAD_ADDED = re.compile(r"(^|\s)-w(\s|\"|$)|-Wno-|#pragma.*diagnostic|#pragma\s+GCC\s+system_header")


def diff_gate(diff: str) -> tuple[bool, list[str]]:
    why: list[str] = []
    files = sorted(set(re.findall(r"^diff --git a/(\S+) b/(\S+)", diff, re.M)))
    for a, b in files:
        for f in {a, b}:
            if PROTECTED.search(f):
                why.append(f"touches protected path {f}")
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++") and BAD_ADDED.search(line[1:]):
            why.append(f"adds a forbidden line: {line[:120]}")
    return not why, why


def diff_size(diff: str) -> dict:
    lines = diff.splitlines()
    return {
        "added": sum(1 for x in lines if x.startswith("+") and not x.startswith("+++")),
        "removed": sum(1 for x in lines if x.startswith("-") and not x.startswith("---")),
        "files": len(re.findall(r"^diff --git ", diff, re.M)),
    }


def run_checks(task: Task, root: Path, jd: Path, port: int) -> list[dict]:
    env = {
        **os.environ,
        "JUDGE": str(jd / "judge-scripts"),
        "RUN_TMP": str(jd / "probe"),
        "PORT": str(port + 700),
        "PRISTINE": str(BENCH_ROOT / "pristine" / "redis-8.10.3"),
    }
    shutil.rmtree(jd / "judge-scripts", ignore_errors=True)
    shutil.copytree(JUDGE_SRC, jd / "judge-scripts")  # outside every checkout
    (jd / "probe").mkdir(exist_ok=True)
    rows = []
    for c in task.checks:
        r = subprocess.run(c.cmd, shell=True, cwd=root, env=env, capture_output=True, text=True, timeout=300)
        got = r.stdout.strip()
        if c.want is not None:
            ok = got == c.want
        elif c.want_empty:
            ok = got == ""
        else:
            ok = r.returncode == 0
        rows.append(
            {"name": c.name, "pass": ok, "exit": r.returncode, "stdout": r.stdout[-2000:], "want": c.want}
        )
    return rows


def judge_one(name: str, slot: int) -> dict:
    task = TASKS[name.split("-")[0]]
    log_dir = RESULTS / name
    jdir = log_dir / "judge"
    shutil.rmtree(jdir, ignore_errors=True)
    jdir.mkdir()
    jd = BENCH_ROOT / "judge" / name
    shutil.rmtree(jd, ignore_errors=True)
    jd.mkdir(parents=True)
    port = 30000 + 1000 * slot
    v: dict = {"run": name, "at": now()}
    diff_text = (log_dir / "diff.patch").read_text(errors="replace")
    v["diff_size"] = diff_size(diff_text)
    root = repos.unpack(repos.REDIS, jd / "fresh")
    if diff_text.strip():
        r = subprocess.run(
            ["git", "apply", "--binary", str(log_dir / "diff.patch")],
            cwd=root,
            capture_output=True,
            text=True,
        )
        v["diff_applies"] = r.returncode == 0
        if r.returncode:
            v["apply_error"] = r.stderr[-1000:]
    else:
        v["diff_applies"] = True
        v["empty_diff"] = True
    v["gate_ok"], v["gate_why"] = diff_gate(diff_text)
    t0 = time.monotonic()
    rc = sh(["make", "-C", "src", f"-j{JOBS}", "MALLOC=libc"], cwd=root, log=jdir / "build.log")
    v["build_s"] = round(time.monotonic() - t0, 1)
    blog = (jdir / "build.log").read_text(errors="replace")
    v["build_ok"] = rc == 0
    v["warnings"] = sum(1 for x in blog.splitlines() if "warning:" in x)
    v["warning_lines"] = [x for x in blog.splitlines() if "warning:" in x][:20]
    v["build_clean"] = v["build_ok"] and v["warnings"] == 0
    if v["build_ok"]:
        rt = ["./runtest", "--clients", "2", "--baseport", str(port), "--portcount", "500"]
        rt += ["--timeout", "1200"]
        for u in task.units:
            rt += ["--single", u]
        t0 = time.monotonic()
        rc = sh(rt, cwd=root, log=jdir / "test.log", timeout=3600)
        v["tests_s"] = round(time.monotonic() - t0, 1)
        tlog = (jdir / "test.log").read_text(errors="replace")
        v["tests_ok"] = rc == 0
        fails = [x for x in tlog.splitlines() if "[err]" in x or "[exception]" in x]
        v["failing_tests"], v["failing_lines"] = len(fails), fails[:20]
    else:
        v["tests_ok"], v["failing_tests"] = False, None
    v["checks"] = run_checks(task, root, jd, port) if v["diff_applies"] else []
    kill_redis_under(jd)
    v["solved"] = bool(
        v["diff_applies"]
        and v["gate_ok"]
        and v["build_clean"]
        and v["tests_ok"]
        and v["checks"]
        and all(c["pass"] for c in v["checks"])
    )
    jsave(jdir / "verdict.json", v)
    print(f"[{now()}] judged {name}: solved={v['solved']}", flush=True)
    return v


def cmd_judge(args: argparse.Namespace) -> int:
    names = args.runs or sorted(
        p.name for p in RESULTS.iterdir() if (p / "diff.patch").is_file() and (p / "meta.json").is_file()
    )
    queue = list(names)
    lock = threading.Lock()

    def worker(slot: int) -> None:
        while True:
            with lock:
                if not queue:
                    return
                n = queue.pop(0)
            judge_one(n, slot)

    th = [threading.Thread(target=worker, args=(s,)) for s in range(2)]
    for x in th:
        x.start()
    for x in th:
        x.join()
    return 0


# ---------------------------------------------------------------- report


def aerotrace_said(events: list[dict], limit: int = 700) -> dict:
    """AeroTrace's own words in a run: MCP tool calls with their results, and hook messages."""
    calls, names = [], {}
    for e in events:
        if e.get("type") == "assistant":
            for c in e.get("message", {}).get("content", []):
                if c.get("type") == "tool_use" and c.get("name", "").startswith("mcp__aerotrace__"):
                    names[c["id"]] = (c["name"], c.get("input"))
    hooks = []
    for e in events:
        if e.get("type") == "user":
            for c in e.get("message", {}).get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in names:
                    body = c.get("content")
                    if isinstance(body, list):
                        body = "\n".join(x.get("text", "") for x in body if isinstance(x, dict))
                    n, inp = names[c["tool_use_id"]]
                    calls.append({"tool": n, "input": inp, "result_excerpt": str(body)[:limit]})
        blob = json.dumps(e)
        if e.get("type") == "system" and "hook" in str(e.get("subtype", "")):
            hooks.append({"subtype": e.get("subtype"), "excerpt": blob[:limit]})
        elif (
            e.get("type") != "assistant"
            and "AeroTrace" in blob
            and "hook" in blob.lower()
            and not any(c.get("tool_use_id") in names for c in e.get("message", {}).get("content", []) or [])
        ):
            hooks.append({"subtype": e.get("type"), "excerpt": blob[:limit]})
    return {"mcp_calls": calls, "hook_messages": hooks}


def tool_counts(events: list[dict]) -> dict[str, int]:
    n: dict[str, int] = {}
    for e in events:
        if e.get("type") == "assistant":
            for c in e.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    n[c["name"]] = n.get(c["name"], 0) + 1
    return dict(sorted(n.items()))


def cmd_report(_: argparse.Namespace) -> int:
    pairs = jload(RESULTS / "pairs.json", {})
    rows = []
    for t in pair_order():
        for arm in ("with", "without"):
            name = f"{t}-{arm}"
            m = jload(RESULTS / name / "meta.json")
            if not m or pairs.get(t, {}).get("status") != "done":
                rows.append({"run": name, "status": pairs.get(t, {}).get("status", "not run")})
                continue
            v = jload(RESULTS / name / "judge" / "verdict.json", {})
            ev = read_stream(RESULTS / name / "stream.jsonl")
            r = m.get("result") or {}
            said = aerotrace_said(ev)
            jsave(RESULTS / name / "aerotrace-said.json", said)
            rows.append(
                {
                    "run": name,
                    "status": "done",
                    "solved": v.get("solved"),
                    "gate_ok": v.get("gate_ok"),
                    "build_ok": v.get("build_ok"),
                    "warnings": v.get("warnings"),
                    "tests_ok": v.get("tests_ok"),
                    "failing_tests": v.get("failing_tests"),
                    "checks": f"{sum(c['pass'] for c in v.get('checks', []))}/{len(v.get('checks', []))}",
                    "failed_checks": [c["name"] for c in v.get("checks", []) if not c["pass"]],
                    "wall_s": m.get("wall_s"),
                    "timed_out": m.get("timed_out"),
                    "result_subtype": r.get("subtype"),
                    "cost_usd": m.get("cost_usd"),
                    "cap_usd": m.get("cap_usd"),
                    "turns": r.get("num_turns"),
                    "usage": {
                        k: (r.get("usage") or {}).get(k)
                        for k in (
                            "input_tokens",
                            "output_tokens",
                            "cache_creation_input_tokens",
                            "cache_read_input_tokens",
                        )
                    },
                    "models": sorted((r.get("modelUsage") or {}).keys()),
                    "init_model": m.get("init_model"),
                    "aerotrace_calls": len(said["mcp_calls"]),
                    "hook_messages": len(said["hook_messages"]),
                    "tools": tool_counts(ev),
                    "diff_size": v.get("diff_size"),
                    "index_s": (m.get("setup") or {}).get("index_s"),
                    "load_median": (m.get("load") or {}).get("median"),
                }
            )
    summary = {"spent_usd": spent(), "budget_usd": BUDGET_USD, "pairs": pairs, "runs": rows}
    jsave(RESULTS / "summary.json", summary)
    print(json.dumps(summary, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="bench/run.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare").set_defaults(func=cmd_prepare)
    sub.add_parser("run").set_defaults(func=cmd_run)
    j = sub.add_parser("judge")
    j.add_argument("runs", nargs="*")
    j.set_defaults(func=cmd_judge)
    sub.add_parser("report").set_defaults(func=cmd_report)
    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
