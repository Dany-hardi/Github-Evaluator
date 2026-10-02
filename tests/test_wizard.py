import csv
import subprocess
import sys

import pytest
import yaml

from markbook import cli
from markbook.spec import load_spec
from markbook.wizard import fix_repo, run_wizard


class Scripted:
    """Answers prompts by what they ask, not by position. An unmatched prompt gets '' (accept the default).
    A rule's answer may be a list: each time the prompt appears the next answer is used."""

    def __init__(self, rules=None, eof_after=None):
        self.rules = [(k, list(v) if isinstance(v, list) else [v]) for k, v in (rules or [])]
        self.prompts: list[str] = []
        self.eof_after = eof_after
        self.said: list[str] = []

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.eof_after is not None and len(self.prompts) > self.eof_after:
            raise EOFError
        for key, answers in self.rules:
            if key in prompt:
                return answers.pop(0) if len(answers) > 1 else answers[0]
        return ""

    def say(self, text: str = "") -> None:
        self.said.append(str(text))

    @property
    def out(self) -> str:
        return "\n".join(self.said)


def wizard(tmp_path, rules=None, **kw):
    s = Scripted(rules, kw.pop("eof_after", None))
    code = run_wizard(tmp_path, ask=s.ask, say=s.say, **kw)
    return code, s


# ── a complete conversation ───────────────────────────────────────────────────

def test_full_conversation_writes_a_valid_spec_and_roster(tmp_path):
    code, s = wizard(tmp_path, [
        ("assignment called", "Blog API"), ("Grade out of", "20"), ("Pass mark", "10"),
        ("Is there a deadline", "y"), ("Deadline", "2026-03-01 23:59"), ("Time zone offset", "+01:00"),
        ("Late penalty", "10"), ("Never take off more", "50"),
        ("Choose a number", "2"),                                   # Node.js
        ("Check: Required files exist", "y"), ("Files or patterns", "package.json, README.md, src/*.js"),
        ("Check: README quality", "y"), ("Required headings", "installation, démarrage"), ("Minimum words", "120"),
        ("Check: Git history", "y"),
        ("Check: Test suite", "y"), ("Minimum number of tests", "5"), ("Hide test names", "y"),
        ("Check: Manual review", "y"), ("What should the reviewer", "Layers and naming."),
        ("Check: another manual", "n"),
        ("Add the students now", "y"), ("Student 1: id", ["danyn", ""]), ("Full name", "Daniel"),
        ("Email", "d@x.edu"), ("Repository URL", "https:github.com/Dany-hardi/blog-api"),
    ])
    assert code == 0, s.out
    spec = load_spec(tmp_path / "spec.yaml")
    assert spec.name == "Blog API" and spec.scale == 20 and spec.pass_mark == 10
    assert [c.type for c in spec.criteria] == ["file_exists", "readme", "git", "junit", "manual"]
    assert spec.criteria[1].check["sections"] == ["installation", "démarrage"]
    assert spec.criteria[3].check["hidden"] is True and spec.criteria[3].check["min_tests"] == 5
    assert spec.deadline.isoformat() == "2026-03-01T22:59:00+00:00" and spec.late.percent_per_day == 10
    assert spec.sandbox.image == "node:20"
    rows = list(csv.DictReader((tmp_path / "roster.csv").open()))
    assert rows == [{"id": "danyn", "name": "Daniel", "email": "d@x.edu",
                     "repo": "https://github.com/Dany-hardi/blog-api"}], "the missing // is repaired"
    assert "markbook validate" in s.out and "20" in s.out


def test_pressing_enter_everywhere_gives_a_sensible_default_rubric(tmp_path):
    code, s = wizard(tmp_path, [("assignment called", "Quick lab")])
    assert code == 0, s.out
    spec = load_spec(tmp_path / "spec.yaml")
    assert [c.id for c in spec.criteria] == ["files", "readme", "manual"]       # the pre-ticked ones
    assert spec.max_points == 9 and spec.scale == 20 and spec.pass_mark == 10
    rows = list(csv.DictReader((tmp_path / "roster.csv").open()))
    assert rows[0]["id"] == "example", "no students typed: a clearly-marked example row, not an empty file"


def test_output_cases_python_tests_and_extra_packages(tmp_path):
    code, s = wizard(tmp_path, [
        ("assignment called", "Calc"), ("Choose a number", "1"),
        ("Check: Required files exist", "n"), ("Check: README quality", "n"), ("Check: Manual review", "n"),
        ("Check: Program output", "y"), ("Command that runs the program", "python calc.py"),
        ("Case 1: name", "add"), ("Arguments after", "2 3"), ("Expected output", ["5", "x\\ny"]),
        ("Add another test case", ["y", "n"]), ("Keep this case secret", ["n", "y"]), ("Input typed", ["", "a\\nb"]),
        ("Check: Test suite", "y"),
        ("Extra packages", "requests, flask==3.0.3"),
    ])
    assert code == 0, s.out
    d = yaml.safe_load((tmp_path / "spec.yaml").read_text())
    cases = d["criteria"][0]["check"]["cases"]
    assert cases[0] == {"name": "add", "args": "2 3", "stdout": "5"}
    assert cases[1]["stdin"] == "a\nb\n" and cases[1]["stdout"] == "x\ny" and cases[1]["hidden"] is True
    assert d["prepare"]["run"] == ["pip install --no-cache-dir pytest requests flask==3.0.3"]
    assert "install pytest once" in s.out


# ── input validation re-asks instead of failing ───────────────────────────────

def test_bad_answers_are_re_asked_with_a_message(tmp_path):
    code, s = wizard(tmp_path, [
        ("assignment called", ["", "   ", "Lab"]),                  # required: asked until something is typed
        ("Grade out of", ["abc", "-4", "0", "100"]),                # not a number, negative, zero, then fine
        ("Pass mark", ["500", "60"]),                               # above the scale, then fine
        ("Is there a deadline", "y"), ("Deadline", ["tomorrow", "2026-03-01 23:59"]),
        ("Choose a number", ["9", "x", "1"]),
        ("Check: README quality", "y"), ("Minimum words", ["1.5", "50"]),
    ])
    assert code == 0, s.out
    spec = load_spec(tmp_path / "spec.yaml")
    assert spec.name == "Lab" and spec.scale == 100 and spec.pass_mark == 60
    assert s.out.count("Please type") >= 3 and "must be at least" in s.out and "at most" in s.out
    assert "Type a number from 1 to 5" in s.out and "Deadline must look like" in s.out


# ── cancelling and not clobbering ─────────────────────────────────────────────

@pytest.mark.parametrize("after", [0, 1, 6, 20])
def test_end_of_input_cancels_cleanly_and_writes_nothing(tmp_path, after):
    code, s = wizard(tmp_path, [("assignment called", "Lab")], eof_after=after)
    assert code == 1 and "Nothing was written" in s.out
    assert not (tmp_path / "spec.yaml").exists() and not (tmp_path / "roster.csv").exists()


def test_ctrl_c_cancels_cleanly(tmp_path):
    def boom(prompt):
        raise KeyboardInterrupt
    assert run_wizard(tmp_path, ask=boom, say=lambda *_: None) == 1
    assert list(tmp_path.iterdir()) == []


def test_declining_the_final_confirmation_writes_nothing(tmp_path):
    code, s = wizard(tmp_path, [("assignment called", "Lab"), ("Write ", "n")])
    assert code == 1 and not (tmp_path / "spec.yaml").exists()


def test_existing_files_are_protected_unless_forced(tmp_path):
    (tmp_path / "spec.yaml").write_text("mine")
    (tmp_path / "roster.csv").write_text("id,repo\nkeep,x\n")
    code, _ = wizard(tmp_path, [("assignment called", "Lab"), ("Overwrite", "n")])
    assert code == 1 and (tmp_path / "spec.yaml").read_text() == "mine"
    code, s = wizard(tmp_path, [("assignment called", "Lab")], force=True)
    assert code == 0 and (tmp_path / "spec.yaml").read_text() != "mine"
    code, s = wizard(tmp_path, [("assignment called", "Lab"), ("Overwrite", "y")])
    assert code == 0
    # the roster is only replaced with --force
    (tmp_path / "roster.csv").write_text("id,repo\nkeep,x\n")
    wizard(tmp_path, [("assignment called", "Lab"), ("Overwrite", "y")])
    assert (tmp_path / "roster.csv").read_text() == "id,repo\nkeep,x\n"


def test_fix_repo_repairs_only_the_known_typo():
    assert fix_repo("https:github.com/a/b") == "https://github.com/a/b"
    assert fix_repo("http:example.com/a/b") == "http://example.com/a/b"
    assert fix_repo("https://github.com/a/b") == "https://github.com/a/b"
    assert fix_repo("owner/repo") == "owner/repo" and fix_repo("  /local/path  ") == "/local/path"


# ── the CLI entry point ───────────────────────────────────────────────────────

def test_init_without_a_terminal_still_writes_the_template(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert cli.main(["init", str(tmp_path)]) == 0
    assert "My assignment" in (tmp_path / "spec.yaml").read_text()
    assert cli.main(["validate", str(tmp_path / "spec.yaml")]) == 0


def test_init_wizard_end_to_end_through_the_real_command(tmp_path):
    answers = "My Lab\n" + "\n" * 90                 # name, then accept every default
    r = subprocess.run([sys.executable, "-m", "markbook", "init", str(tmp_path / "c"), "--wizard"],
                       input=answers, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-400:]
    assert load_spec(tmp_path / "c" / "spec.yaml").name == "My Lab"
    assert (tmp_path / "c" / "roster.csv").exists() and "Wrote" in r.stdout


def test_init_wizard_with_closed_stdin_cancels_without_a_traceback(tmp_path):
    r = subprocess.run([sys.executable, "-m", "markbook", "init", str(tmp_path / "c"), "--wizard"],
                       input="", capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "Traceback" not in r.stderr and "Nothing was written" in r.stdout
    assert not (tmp_path / "c" / "spec.yaml").exists()
