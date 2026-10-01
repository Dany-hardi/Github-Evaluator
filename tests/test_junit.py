import os
import sys
import textwrap

import pytest

from markbook import checks
from markbook.gitinfo import History
from markbook.sandbox import LocalSandbox
from markbook.spec import SandboxSpec, SpecError, load_spec

PY = sys.executable


def report_xml(passed=3, failed=1, skipped=1, errored=0):
    cases = [f'<testcase classname="t.Suite" name="ok_{i}"/>' for i in range(passed)]
    cases += [f'<testcase classname="t.Suite" name="bad_{i}"><failure message="expected 1, got 2">trace</failure></testcase>'
              for i in range(failed)]
    cases += [f'<testcase classname="t.Suite" name="err_{i}"><error message="boom"/></testcase>' for i in range(errored)]
    cases += ['<testcase classname="t.Suite" name="skip_0"><skipped/></testcase>' for _ in range(skipped)]
    return f'<?xml version="1.0"?><testsuites><testsuite name="s">{"".join(cases)}</testsuite></testsuites>'


@pytest.fixture
def ctx(tmp_path):
    counter = iter(range(1000))

    def make(files: dict[str, str]) -> checks.Context:
        work = tmp_path / f"w{next(counter)}"
        work.mkdir()
        for rel, content in files.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_text(content)
        sb = LocalSandbox(SandboxSpec(timeout=10))
        sb.load(work)
        return checks.Context(work, sb, History([]), default_timeout=10)
    return make


def writer(xml: str) -> str:
    """A command that writes `xml` to report.xml, like a test runner would."""
    return f"{PY} -c \"import pathlib,sys; pathlib.Path('report.xml').write_text(sys.stdin.read())\" < payload.xml"


def run(ctx, xml, **opts):
    c = ctx({"payload.xml": xml})
    return checks.junit({"run": writer(xml), **opts}, c)


# ── scoring ───────────────────────────────────────────────────────────────────

def test_partial_credit_by_passing_tests_skipped_excluded(ctx):
    out = run(ctx, report_xml(passed=3, failed=1, skipped=2))
    assert out.status == "partial" and out.fraction == pytest.approx(0.75)
    text = " | ".join(f"{e.label}: {e.text}" for e in out.evidence)
    assert "3/4 tests passed (2 skipped)" in text
    assert "FAIL t.Suite::bad_0: expected 1, got 2" in text


def test_errors_count_as_failures(ctx):
    out = run(ctx, report_xml(passed=1, failed=0, skipped=0, errored=1))
    assert out.fraction == pytest.approx(0.5)


def test_all_pass(ctx):
    out = run(ctx, report_xml(passed=4, failed=0, skipped=0))
    assert out.status == "passed" and out.fraction == 1.0


def test_hidden_tests_reveal_nothing(ctx):
    out = run(ctx, report_xml(passed=1, failed=2, skipped=0), hidden=True)
    text = " ".join(e.label + e.text for e in out.evidence)
    assert "bad_0" not in text and "expected 1, got 2" not in text and "FAIL (hidden) test 1" in text


def test_failure_list_is_capped(ctx):
    out = run(ctx, report_xml(passed=0, failed=25, skipped=0))
    labels = [e.label for e in out.evidence if e.label.startswith("FAIL")]
    assert len(labels) == checks.MAX_FAILURES_SHOWN
    assert any("15 more" in e.text for e in out.evidence)


# ── "nothing ran" must never look like a pass ─────────────────────────────────

def test_zero_tests_found_fails_by_default(ctx):
    out = run(ctx, report_xml(passed=0, failed=0, skipped=0))
    assert out.fraction == 0.0 and out.status == "failed" and "no tests ran" in out.evidence[0].text


def test_only_skipped_tests_is_not_a_pass(ctx):
    assert run(ctx, report_xml(passed=0, failed=0, skipped=3)).fraction == 0.0


def test_min_tests_enforced_and_zero_is_an_explicit_opt_out(ctx):
    assert run(ctx, report_xml(passed=2, failed=0, skipped=0), min_tests=5).fraction == 0.0
    assert run(ctx, report_xml(passed=0, failed=0, skipped=0), min_tests=0).fraction == 1.0


def test_missing_report_fails_and_shows_the_runner_output(ctx):
    c = ctx({})
    out = checks.junit({"run": "echo 'collected 0 items'; echo oops >&2; exit 2"}, c)
    assert out.fraction == 0.0
    text = " ".join(e.label + ":" + e.text for e in out.evidence)
    assert "report.xml was not produced" in text and "collected 0 items" in text and "oops" in text


def test_forged_report_committed_in_the_repo_is_ignored(ctx):
    """A student ships an all-pass report.xml and hopes the test command crashes before overwriting it."""
    forged = report_xml(passed=50, failed=0, skipped=0)
    c = ctx({"report.xml": forged})
    out = checks.junit({"run": "exit 1"}, c)             # the runner crashes, writes nothing
    assert out.fraction == 0.0, "the pre-existing report must be deleted before the run"


def test_timeout_fails(ctx):
    c = ctx({})
    out = checks.junit({"run": "sleep 20", "timeout": 1}, c)
    assert out.fraction == 0.0 and "timed out" in out.evidence[0].text


# ── hostile reports ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload,needle", [
    ("<not xml", "not valid XML"),
    ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><testsuite><testcase name="&a;"/></testsuite>', "DOCTYPE"),
    ("", "not valid XML"),
])
def test_malformed_or_entity_reports_are_rejected(ctx, payload, needle):
    out = run(ctx, payload)
    assert out.fraction == 0.0 and needle in out.evidence[0].text


def test_oversized_report_is_rejected(ctx):
    big = report_xml(passed=1, failed=0, skipped=0) + "<!--" + "x" * (2 << 20) + "-->"
    out = run(ctx, big)
    assert out.fraction == 0.0 and "larger than" in out.evidence[0].text


@pytest.mark.parametrize("bad", ["/etc/passwd", "../report.xml", "a/../../b.xml", ""])
def test_unsafe_report_paths_are_refused(ctx, bad):
    out = checks.junit({"run": "true", "report": bad}, ctx({}))
    assert out.status == "error" and out.review


def test_symlinked_report_cannot_read_host_files(ctx, tmp_path):
    secret = tmp_path / "secret.xml"
    secret.write_text(report_xml(passed=9, failed=0, skipped=0))
    c = ctx({})
    # The command plants a symlink to a host file instead of writing a real report.
    out = checks.junit({"run": f"ln -s {secret} report.xml"}, c)
    assert out.fraction == 0.0 and "symlink" in " ".join(e.text for e in out.evidence)


# ── parser ────────────────────────────────────────────────────────────────────

def test_parse_junit_shapes():
    cases, err = checks.parse_junit('<testsuite><testcase classname="a.B" name="c"/></testsuite>')
    assert err is None and cases == [{"name": "a.B::c", "status": "passed", "message": ""}]
    cases, _ = checks.parse_junit("<testsuites><testsuite><testcase name='x'><failure>text body</failure></testcase>"
                                  "</testsuite><testsuite><testcase name='y'/></testsuite></testsuites>")
    assert [c["status"] for c in cases] == ["failed", "passed"] and cases[0]["message"] == "text body"


# ── spec validation ───────────────────────────────────────────────────────────

def _spec(tmp_path, check):
    p = tmp_path / "spec.yaml"
    p.write_text(textwrap.dedent(f"""
        name: j
        sandbox: {{image: "python:3.12-slim"}}
        criteria:
          - {{id: t, points: 5, check: {check}}}
    """))
    return p


def test_spec_accepts_junit_and_requires_a_sandbox_image(tmp_path):
    s = load_spec(_spec(tmp_path, "{type: junit, run: 'pytest --junitxml=report.xml', min_tests: 3, hidden: true}"))
    assert s.criteria[0].type == "junit" and s.needs_sandbox
    p = tmp_path / "noimg.yaml"
    p.write_text("name: j\ncriteria:\n  - {id: t, points: 1, check: {type: junit, run: x}}\n")
    with pytest.raises(SpecError, match="sandbox.image"):
        load_spec(p)


@pytest.mark.parametrize("check,needle", [
    ("{type: junit}", "check.run is required"),
    ("{type: junit, run: x, report: ../r.xml}", "relative path"),
    ("{type: junit, run: x, min_tests: -1}", "min_tests"),
    ("{type: junit, run: x, bogus: 1}", "unknown check option"),
])
def test_spec_rejects_bad_junit_options(tmp_path, check, needle):
    with pytest.raises(SpecError, match=needle):
        load_spec(_spec(tmp_path, check))


# ── the real thing: pytest, end to end, through the grader ────────────────────

def test_real_pytest_run_gives_per_test_partial_credit(tmp_path):
    from markbook.grader import grade_submission
    from markbook.roster import Entry
    from conftest import make_repo
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(textwrap.dedent(f"""
        name: pytest-grading
        scale: 10
        sandbox: {{image: x, timeout: 60}}
        criteria:
          - id: tests
            points: 10
            check: {{type: junit, run: "{PY} -m pytest -q -p no:cacheprovider --junitxml=report.xml", min_tests: 3}}
    """))
    repo = make_repo(tmp_path / "stu", {
        "calc.py": "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a + b   # bug\n",
        "test_calc.py": ("from calc import add, sub\n\ndef test_add():\n    assert add(1, 2) == 3\n\n"
                         "def test_add_zero():\n    assert add(0, 0) == 0\n\ndef test_sub():\n    assert sub(3, 1) == 2\n"),
    })
    sub, _ = grade_submission(load_spec(spec_path), Entry("s", "S", "", str(repo)), "none", allow_local=True)
    c = sub["criteria"][0]
    assert c["status"] == "partial" and c["points"] == pytest.approx(10 * 2 / 3, abs=0.01)
    text = " ".join(e["label"] + " " + e["text"] for e in c["evidence"])
    assert "2/3 tests passed" in text and "test_sub" in text
