import os
from pathlib import Path

import pytest

from markbook import checks
from markbook.gitinfo import Commit, History
from markbook.sandbox import LocalSandbox
from markbook.spec import SandboxSpec
from datetime import datetime, timezone


@pytest.fixture
def ctx(tmp_path):
    counter = iter(range(1000))

    def make(files: dict[str, str], history: History | None = None) -> checks.Context:
        work = tmp_path / f"work{next(counter)}"  # fresh tree per call
        work.mkdir()
        for rel, content in files.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text(content)
        sb = LocalSandbox(SandboxSpec(timeout=3))
        sb.load(work)
        return checks.Context(work, sb, history or History([]), default_timeout=3)
    return make


def test_command_pass_fail_and_stdout_contains(ctx):
    c = ctx({})
    assert checks.command({"run": "true"}, c).status == "passed"
    out = checks.command({"run": "exit 3"}, c)
    assert out.status == "failed" and "exit code 3, expected 3" not in out.evidence[0].text
    assert checks.command({"run": "echo hello", "stdout_contains": "hell"}, c).status == "passed"
    assert checks.command({"run": "echo hello", "stdout_contains": "bye"}, c).status == "failed"
    assert checks.command({"run": "exit 2", "expect_exit": 2}, c).status == "passed"


def test_command_timeout_is_a_result_not_a_crash(ctx):
    out = checks.command({"run": "sleep 10", "timeout": 1}, ctx({}))
    assert out.status == "failed" and "timed out" in out.evidence[0].text


def test_output_flood_is_capped(ctx):
    r = ctx({}).sandbox.exec("yes | head -c 5000000", timeout=5)
    assert len(r.stdout) <= 64 * 1024 and r.truncated


def test_local_sandbox_scrubs_environment(ctx, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "leak-me")
    r = ctx({}).sandbox.exec("env", timeout=5)
    assert "leak-me" not in r.stdout and "GITHUB_TOKEN" not in r.stdout


CASE_RUN = "python3 prog.py"


def test_cases_partial_credit_and_diff(ctx):
    c = ctx({"prog.py": "import sys\nprint(int(sys.argv[1]) * 2)\n"})
    out = checks.cases({"run": CASE_RUN, "cases": [
        {"name": "ok", "args": "2", "stdout": "4"},
        {"name": "bad", "args": "3", "stdout": "7"}]}, c)
    assert out.fraction == 0.5 and out.status == "partial"
    fail = next(e for e in out.evidence if e.label.startswith("FAIL"))
    assert "expected '7', got '6'" in fail.text


def test_crash_never_passes_even_if_output_matches(ctx):
    # Prints the expected (empty) output but crashes: must not earn the case.
    c = ctx({"prog.py": "import sys\nsys.exit(1)\n"})
    out = checks.cases({"run": CASE_RUN, "cases": [{"name": "empty", "stdout": ""}]}, c)
    assert out.fraction == 0.0
    ok = checks.cases({"run": CASE_RUN, "cases": [{"name": "empty", "stdout": "", "exit": 1}]}, c)
    assert ok.fraction == 1.0


def test_hidden_case_does_not_leak_expected_output(ctx):
    c = ctx({"prog.py": "print('wrong')\n"})
    out = checks.cases({"run": CASE_RUN, "cases": [{"name": "h", "hidden": True, "stdout": "SECRET-ANSWER"}]}, c)
    assert "SECRET-ANSWER" not in " ".join(e.text + e.label for e in out.evidence)


def test_compare_modes(ctx):
    c = ctx({"prog.py": "print('a   b')\nprint('c  ')\n"})
    run = lambda mode, want: checks.cases({"run": CASE_RUN, "compare": mode, "cases": [{"stdout": want}]}, c).fraction
    assert run("trim", "a   b\nc") == 1.0        # trailing whitespace ignored
    assert run("trim", "a b\nc") == 0.0          # inner whitespace matters
    assert run("tokens", "a b c") == 1.0         # whitespace-insensitive
    assert run("exact", "a   b\nc") == 0.0       # trailing newline/space matter


def test_stdout_regex_and_stdin(ctx):
    c = ctx({"prog.py": "print(input().upper())\n"})
    out = checks.cases({"run": CASE_RUN, "cases": [{"stdin": "abc\n", "stdout_regex": "^ABC$"}]}, c)
    assert out.fraction == 1.0


def test_readme_parts(ctx):
    good = "# Title\n## Installation\nblah\n## Usage\n" + "word " * 50
    out = checks.readme({"sections": ["install", "usage"], "min_words": 30}, ctx({"README.md": good}))
    assert out.fraction == 1.0
    out = checks.readme({"sections": ["install", "usage", "license"], "min_words": 500}, ctx({"README.md": good}))
    assert out.status == "partial"
    assert checks.readme({}, ctx({"x.py": "1"})).status == "failed"
    out = checks.readme({"forbid": ["lorem ipsum"]}, ctx({"readme.txt": "Lorem Ipsum dolor"}))
    assert out.fraction < 1.0


def test_readme_ignores_symlink_escaping_the_tree(ctx, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("# Usage\n" + "leak " * 100)
    c = ctx({})
    os.symlink(secret, c.workdir / "README.md")
    out = checks.readme({"min_words": 10}, c)
    assert out.status == "failed", "a symlinked README must not be read from outside the submission"


def test_file_exists_globs_and_symlink_probe(ctx, tmp_path):
    c = ctx({"src/main.c": "", "Makefile": ""})
    out = checks.file_exists({"paths": ["*.c", "Makefile", "docs/*.md"]}, c)
    assert out.status == "partial" and out.fraction == pytest.approx(2 / 3)
    os.symlink("/etc/passwd", c.workdir / "passwd.txt")
    assert checks.file_exists({"paths": ["passwd.txt"]}, c).status == "failed"


def _hist(commits):
    return History([Commit("x", a, e, datetime(2026, 2, d, tzinfo=timezone.utc), n) for a, e, d, n in commits])


def test_git_check(ctx):
    h = _hist([("a", "a@x", 1, 10), ("a", "a@x", 2, 10), ("b", "b@x", 2, 80)])
    c = ctx({}, h)
    out = checks.git({"min_commits": 3, "min_active_days": 2, "min_authors": 2, "max_single_commit_share": 0.5}, c)
    assert out.status == "partial" and out.fraction == pytest.approx(0.75)  # share 80% > 50%
    assert checks.git({"min_commits": 10}, c).status == "failed"
    assert checks.git({}, c).status == "passed"


def test_manual_is_never_auto_resolved(ctx):
    out = checks.manual({"guidance": "look"}, ctx({}))
    assert out.status == "manual" and out.review
