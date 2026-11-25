"""The Claude Code edit-check hook: sample hook inputs in, the JSON Claude Code reads out."""

from __future__ import annotations

import io
import json
import subprocess
import time

import pytest

from aerotrace.analysis import rules as rules_mod
from aerotrace.hooks import claude
from aerotrace.index.build import build_index
from aerotrace.mcp import install

ADD = "int add(int a, int b) { return a + b; }"
ADD3 = "int add(int a, int b, int c) { return a + b + c; }"


@pytest.fixture
def repo(repo_factory):
    root = repo_factory("testimpact").resolve()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    build_index(root, jobs=1)
    return root


def event(root, tool, rel, **inp):
    return {
        "session_id": "s",
        "transcript_path": "/dev/null",
        "cwd": str(root),
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"file_path": str(root / rel), **inp},
        "tool_use_id": "t",
    }


def run(ev, timeout=30.0):
    out = claude.answer(ev, timeout=timeout)
    if out is not None:
        assert json.loads(json.dumps(out)) == out
        assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    return out


def text(out):
    h = out["hookSpecificOutput"]
    return h.get("additionalContext") or h.get("permissionDecisionReason")


def test_edit_breaking_signature_is_high_with_functions_and_tests(repo):
    before = (repo / "src/calc.c").read_text()
    out = run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3, replace_all=False))
    h = out["hookSpecificOutput"]
    assert "permissionDecision" not in h  # never "allow": that would skip the user's own prompt
    t = h["additionalContext"]
    assert t.startswith("AeroTrace: this edit breaks code:")
    assert "twice (src/calc.c:" in t and "test_add (tests/test_unity.c:4)" in t
    assert "Tests to run:" in t and "Calc.Adds" in t
    assert (repo / "src/calc.c").read_text() == before  # nothing written
    assert run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3)) == out  # deterministic


def test_multiedit_applies_edits_in_order(repo):
    ev = event(
        repo,
        "MultiEdit",
        "src/calc.c",
        edits=[
            {
                "old_string": "int a, int b) { return a + b; }",
                "new_string": "int a, int b, int c) { return a; }",
            },
            {"old_string": "int c) { return a; }", "new_string": "int c) { return a + b + c; }"},
        ],
    )
    assert "add (src/calc.c:5)" in text(run(ev))


def test_multiedit_with_a_missing_string_is_silent(repo):
    ev = event(repo, "MultiEdit", "src/calc.c", edits=[{"old_string": "nope(", "new_string": "x"}])
    assert run(ev) is None


def test_body_only_edit_is_silent(repo):
    ev = event(repo, "Edit", "src/calc.c", old_string="a / b : 0;", new_string="a / b : -1;")
    assert run(ev) is None


def test_body_edit_with_many_callers_is_medium(repo):
    ev = event(repo, "Edit", "src/calc.c", old_string="return a * b;", new_string="return b * a;")
    t = text(run(ev))
    assert t.startswith("AeroTrace: this edit affects code: 12 affected function(s) or data.")
    assert len(t.splitlines()) <= 5


def test_write_new_file_says_it_was_not_checked(repo):
    ev = event(repo, "Write", "src/new.c", content="int brand_new(void) { return 1; }\n")
    out = run(ev)  # A-09: a new file is not in the build yet, so it was not checked: one line, not silence
    assert text(out) == (
        "AeroTrace could not check this edit (src/new.c is new and not in the build yet). "
        "Build before you go on."
    )


def test_write_overwrite_is_checked(repo):
    new = (repo / "src/calc.c").read_text().replace(ADD, ADD3)
    assert "breaks code" in text(run(event(repo, "Write", "src/calc.c", content=new)))


def test_non_c_file_unknown_tool_and_outside_file_are_silent(repo, tmp_path):
    (repo / "notes.md").write_text("hi\n")
    assert run(event(repo, "Edit", "notes.md", old_string="hi", new_string="ho")) is None
    assert run(event(repo, "Read", "src/calc.c")) is None
    ev = event(repo, "Write", "x.c", content="int f(void);\n")
    ev["tool_input"]["file_path"] = str(tmp_path / "elsewhere.c")
    assert run(ev) is None


def test_bad_json_on_stdin_prints_nothing():
    out = io.StringIO()
    assert claude.main(io.StringIO("not json"), out) == 0
    assert out.getvalue() == ""


def test_main_prints_one_json_line(repo):
    ev = event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3)
    out = io.StringIO()
    assert claude.main(io.StringIO(json.dumps(ev)), out) == 0
    assert "breaks code" in json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]


def test_missing_index_allows_with_note(repo_factory):
    root = repo_factory("testimpact").resolve()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    out = run(event(root, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3))
    h = out["hookSpecificOutput"]
    assert "permissionDecision" not in h
    assert h["additionalContext"].startswith("AeroTrace couldn't check this edit (no index yet")
    assert not (root / ".aerotrace").exists()  # it never builds a whole index


def test_broken_index_allows_with_note(repo):
    (repo / ".aerotrace" / "index.db").write_bytes(b"this is not sqlite" * 100)
    for p in (repo / ".aerotrace").glob("index.db-*"):
        p.unlink()
    out = run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3))
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert text(out).startswith("AeroTrace couldn't check this edit (")


def test_stale_index_is_refreshed_for_a_few_files(repo):
    p = repo / "src/calc.c"
    p.write_text(p.read_text().replace("a / b : 0;", "a / b : 1;"))
    ev = event(repo, "Edit", "src/calc.c", old_string="a / b : 1;", new_string="a / b : 2;")
    assert run(ev) is None  # re-indexed calc.c, then a quiet body-only edit
    ev = event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3)
    assert "breaks code" in text(run(ev))


def test_stale_index_beyond_the_limit_allows_with_note(repo, monkeypatch):
    monkeypatch.setattr(claude, "SMALL_REFRESH", 0)
    p = repo / "src/util.c"
    p.write_text(p.read_text() + "\n")
    out = run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3))
    assert text(out) == (
        "AeroTrace couldn't check this edit (index is stale (1 files changed); run `aerotrace index`)."
    )


def test_internal_error_allows_with_note(repo, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bad day")

    monkeypatch.setattr(claude, "check_edit", boom)
    out = run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3))
    assert text(out) == "AeroTrace couldn't check this edit (internal error: RuntimeError: bad day)."


def test_timeout_allows_with_note(repo, monkeypatch):
    monkeypatch.setattr(claude, "check_edit", lambda *a, **k: time.sleep(1))
    out = run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3), timeout=0.1)
    assert text(out) == "AeroTrace couldn't check this edit (it took longer than 0.1 s)."
    assert "permissionDecision" not in out["hookSpecificOutput"]


def write_rules(repo, body):
    (repo / ".aerotrace" / "rules.toml").write_text(body)


def test_block_rule_denies(repo):
    write_rules(repo, '[[rule]]\nname = "calc"\npaths = ["src/*.c"]\nlevel = "medium"\naction = "block"\n')
    out = run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3))
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"
    assert "breaks code" in h["permissionDecisionReason"]
    assert "Blocked by rule 'calc' in .aerotrace/rules.toml" in h["permissionDecisionReason"]


def test_block_rule_on_other_paths_only_warns(repo):
    write_rules(repo, '[[rule]]\npaths = ["include/*"]\nlevel = "low"\naction = "block"\n')
    out = run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3))
    assert "permissionDecision" not in out["hookSpecificOutput"]


def test_protected_path_blocks_even_a_quiet_edit(repo):
    write_rules(
        repo, '[[rule]]\nname = "frozen"\npaths = ["src/calc.c", "*.md"]\nlevel = "low"\naction = "block"\n'
    )
    out = run(event(repo, "Edit", "src/calc.c", old_string="return a * b;", new_string="return b * a;"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    (repo / "README.md").write_text("x\n")
    out = run(event(repo, "Edit", "README.md", old_string="x", new_string="y"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_failed_check_blocks_only_on_a_protected_path(repo, monkeypatch):
    # a level-"low" block rule protects the path whatever the index says (review of #50)
    write_rules(repo, '[[rule]]\nname = "p"\npaths = ["src/*"]\nlevel = "low"\naction = "block"\n')
    monkeypatch.setattr(claude, "check_edit", lambda *a, **k: (_ for _ in ()).throw(claude.Skip("x")))
    out = run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3))
    h = out["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny"
    assert "src/calc.c is protected. AeroTrace couldn't check this edit (x)." in h["permissionDecisionReason"]
    # a rule that needs a level never blocks when the level is not known
    write_rules(repo, '[[rule]]\npaths = ["src/*"]\nlevel = "medium"\naction = "block"\n')
    out = run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3))
    assert "permissionDecision" not in out["hookSpecificOutput"]


def test_protected_path_blocks_without_an_index_and_on_timeout(repo_factory, monkeypatch):
    root = repo_factory("testimpact").resolve()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / ".aerotrace").mkdir()
    write_rules(root, '[[rule]]\npaths = ["src/calc.c"]\nlevel = "low"\naction = "block"\n')
    ev = event(root, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3)
    h = run(ev)["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny" and "no index yet" in h["permissionDecisionReason"]
    monkeypatch.setattr(claude, "check_edit", lambda *a, **k: time.sleep(1))
    h = run(ev, timeout=0.1)["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny" and "longer than 0.1 s" in h["permissionDecisionReason"]


def to_crlf(repo, rel):
    p = repo / rel
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))


def test_crlf_file_patch_matches_the_disk(repo):
    to_crlf(repo, "src/calc.c")
    before = (repo / "src/calc.c").read_bytes()
    t = text(run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3)))
    assert t.startswith("AeroTrace: this edit breaks code") and "removed by the patch" not in t
    assert "[breaks, exact] twice (src/calc.c:22)" in t
    new = before.decode().replace(ADD, ADD3)
    t = text(run(event(repo, "Write", "src/calc.c", content=new)))
    assert t.startswith("AeroTrace: this edit breaks code") and "removed by the patch" not in t
    assert "[breaks, exact] twice (src/calc.c:22)" in t
    assert (repo / "src/calc.c").read_bytes() == before
    patch = claude.make_patch("src/calc.c", before.decode(), new)
    assert "-int add(int a, int b) { return a + b; }\r\n" in patch


def test_crlf_multiline_old_string_written_with_plain_newlines(repo):
    to_crlf(repo, "src/calc.c")
    old = "int calc_mode = 0;\n\n" + ADD
    t = text(
        run(event(repo, "Edit", "src/calc.c", old_string=old, new_string="int calc_mode = 0;\n\n" + ADD3))
    )
    assert "add (src/calc.c:5)" in t


def test_invalid_rules_warn_and_never_block(repo):
    write_rules(repo, '[[rule]]\npaths = ["src/*"]\naction = "explode"\n')
    out = run(event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3))
    h = out["hookSpecificOutput"]
    assert "permissionDecision" not in h
    assert h["additionalContext"].startswith("AeroTrace ignored .aerotrace/rules.toml: rule 'rule 1': action")
    write_rules(repo, "this is = = not toml")
    out = run(event(repo, "Edit", "src/calc.c", old_string="return a * b;", new_string="return b * a;"))
    assert text(out).startswith("AeroTrace ignored .aerotrace/rules.toml: not valid TOML")


def test_rules_parse_and_levels():
    r = rules_mod.parse_rules(
        '[thresholds]\nmedium_affected = 2\ntimeout_seconds = 1.5\n[[rule]]\nname = "x"\n'
    )
    assert (r.medium_affected, r.timeout_seconds, r.rules[0].action, r.rules[0].level) == (
        2,
        1.5,
        "warn",
        "high",
    )
    for bad in (
        "[thresholds]\nmedium_affected = 0\n",
        "[thresholds]\nfoo = 1\n",
        "other = 1\n",
        "rule = 3\n",
    ):
        with pytest.raises(ValueError):
            rules_mod.parse_rules(bad)
    c = {"summary": {"breaks_exact": 0, "breaks_probable": 0, "affected_direct": 1, "affected_further": 1}}
    assert rules_mod.level(c, r) == "medium"
    assert rules_mod.level(c, rules_mod.Rules()) == "low"
    c["summary"]["breaks_probable"] = 1
    assert rules_mod.level(c, rules_mod.Rules()) == "medium"
    c["summary"]["breaks_exact"] = 1
    assert rules_mod.level(c, rules_mod.Rules()) == "high"


def test_threshold_from_rules(repo):
    write_rules(repo, "[thresholds]\nmedium_affected = 100\n")
    assert (
        run(event(repo, "Edit", "src/calc.c", old_string="return a * b;", new_string="return b * a;")) is None
    )


def test_hook_off_and_on(repo, capsys):
    from aerotrace import cli

    ev = event(repo, "Edit", "src/calc.c", old_string=ADD, new_string=ADD3)
    assert cli.main(["hook", "off", "--root", str(repo)]) == 0
    assert run(ev) is None
    assert cli.main(["hook", "on", "--root", str(repo)]) == 0
    assert run(ev) is not None
    cli.main(["hook", "status", "--root", str(repo)])
    assert "Edit check: on." in capsys.readouterr().out


def test_install_merges_settings_and_does_not_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "launch_command", lambda: ["/opt/aero tools/aerotrace", "mcp"])
    s = tmp_path / ".claude" / "settings.json"
    s.parent.mkdir()
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
    s.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}, "hooks": {"PreToolUse": [other]}}))
    for _ in range(2):
        install.apply(install.hook_plan(tmp_path))
    data = json.loads(s.read_text())
    assert data["permissions"] == {"allow": ["Bash(ls)"]}
    pre = data["hooks"]["PreToolUse"]
    assert pre[0] == other and len(pre) == 2
    assert pre[1] == {
        "matcher": "Edit|MultiEdit|Write",
        "hooks": [{"type": "command", "command": "'/opt/aero tools/aerotrace' hook run", "timeout": 30}],
    }
    # a user's own hook is never taken for ours, even if its command looks alike
    mine = {"type": "command", "command": "/usr/local/bin/myhook hook run"}
    s.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [mine]}]}}))
    install.apply(install.hook_plan(tmp_path))
    pre = json.loads(s.read_text())["hooks"]["PreToolUse"]
    assert pre[0] == {"matcher": "Edit", "hooks": [mine]} and len(pre) == 2
    s.write_text("{nope")
    with pytest.raises(install.InstallError):
        install.hook_plan(tmp_path)


def test_install_claude_writes_both_files(tmp_path, monkeypatch, capsys):
    from aerotrace import cli

    monkeypatch.setattr(install, "launch_command", lambda: ["/x/aerotrace", "mcp"])
    assert cli.main(["install", "--client", "claude", "--root", str(tmp_path)]) == 0
    assert "aerotrace" in json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert (tmp_path / ".claude" / "settings.json").is_file()
    assert "edit check" in capsys.readouterr().out
    assert cli.main(["install", "--client", "cursor", "--root", str(tmp_path / "c")]) == 0
    assert not (tmp_path / "c" / ".claude").exists()  # other clients are unchanged
