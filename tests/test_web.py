import io
import json
import time
import zipfile

import pytest

from markbook import cli
from markbook.web.app import create_app


@pytest.fixture
def web(tmp_path, demo_run):
    runs = tmp_path / "runs"
    cli.main(["grade", str(demo_run["spec_path"]), "--roster", str(demo_run["roster_path"]),
              "--sandbox", "none", "--out", str(runs / "r1"), "--quiet"])
    app = create_app(runs, runtime="none")
    app.config["TESTING"] = True
    return app.test_client(), runs


def post(c, url, **data):
    return c.post(url, data=data, headers={"Origin": "http://localhost"}, follow_redirects=False)


def test_pages_render(web):
    c, _ = web
    for url, needle in [("/", "FizzBuzz CLI"), ("/runs/r1", "less manual review"), ("/runs/r1?show=all", "ghost"),
                        ("/runs/r1?show=similarity", "carol"), ("/runs/r1/s/alice", "Code design"),
                        ("/runs/r1/s/ghost", "invalid_url"), ("/runs/new", "Start grading")]:
        r = c.get(url)
        assert r.status_code == 200 and needle in r.get_data(as_text=True), url


def test_run_id_traversal_blocked(web):
    c, _ = web
    for bad in ("..", "..%2F..%2Fetc", "r1%2F..", ".hidden", "a" * 100):
        assert c.get(f"/runs/{bad}").status_code == 404


def test_csrf_cross_origin_post_blocked(web):
    c, _ = web
    r = c.post("/runs/r1/delete", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = c.post("/runs/r1/delete", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert c.get("/runs/r1").status_code == 200, "run must still exist"


def test_security_headers(web):
    c, _ = web
    h = c.get("/").headers
    assert "script-src 'self'" in h["Content-Security-Policy"] and "'unsafe-inline'" not in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"


def test_templates_have_no_inline_script_or_style(web):
    c, _ = web
    for url in ("/", "/runs/r1", "/runs/r1/s/bob", "/runs/new"):
        html = c.get(url).get_data(as_text=True)
        assert " style=" not in html and "onclick=" not in html and "<script>" not in html, url


def test_evidence_is_html_escaped(web, tmp_path):
    c, runs = web
    raw = json.loads((runs / "r1" / "run.json").read_text())
    raw["submissions"][0]["criteria"][0]["evidence"] = [{"label": "x", "text": "<script>alert(1)</script>"}]
    raw["submissions"][0]["name"] = "<img src=x onerror=alert(1)>"
    (runs / "r1" / "run.json").write_text(json.dumps(raw))
    html = c.get("/runs/r1/s/alice").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_decision_flow_jumps_to_next_in_queue(web):
    c, runs = web
    r = post(c, "/runs/r1/s/alice/decide", reviewer="ann", action="points", criterion="quality", points="3",
             comment="nice")
    assert r.status_code == 302
    # alice is resolved, so we land on the first remaining queue item (bob)
    assert r.headers["Location"].endswith("/runs/r1/s/bob")
    page = c.get("/runs/r1/s/alice").get_data(as_text=True)
    assert "overridden" in page and "ann" in page and "nice" in page
    # raw result untouched, audit log written
    assert json.loads((runs / "r1" / "overrides.json").read_text())["decisions"][0]["reviewer"] == "ann"
    # on-disk report.json was refreshed for CLI users
    rep = json.loads((runs / "r1" / "report.json").read_text())
    assert next(s for s in rep["submissions"] if s["id"] == "alice")["score"]["scaled"] == 20.0


def test_invalid_points_rejected_with_message(web):
    c, _ = web
    r = post(c, "/runs/r1/s/alice/decide", action="points", criterion="quality", points="99")
    assert r.status_code == 302
    assert "Not saved" in c.get("/runs/r1/s/alice").get_data(as_text=True)
    r = post(c, "/runs/r1/s/alice/decide", action="points", criterion="quality", points="abc")
    assert "Not saved" in c.get("/runs/r1/s/alice").get_data(as_text=True)


def test_clear_flag_and_waive_late(web):
    c, _ = web
    post(c, "/runs/r1/s/bob/decide", action="clear_similarity", comment="lecture helper")
    assert "similarity" not in c.get("/runs/r1?show=review").get_data(as_text=True).split("bob")[1][:400]
    post(c, "/runs/r1/s/erin/decide", action="waive_late")
    assert "Waived" in c.get("/runs/r1/s/erin").get_data(as_text=True)


def test_downloads(web):
    c, _ = web
    rep = c.get("/runs/r1/download/report.json")
    assert rep.status_code == 200 and json.loads(rep.data)["schema_version"] == "1"
    assert "attachment" in rep.headers["Content-Disposition"]
    assert c.get("/runs/r1/download/grades.canvas.csv").data.startswith(b"Student,ID")
    assert c.get("/runs/r1/download/grades.moodle.csv").data.startswith(b"Identifier")
    assert c.get("/runs/r1/download/junit.xml").data.startswith(b"<?xml")
    z = zipfile.ZipFile(io.BytesIO(c.get("/runs/r1/download/feedback.zip").data))
    assert "alice.md" in z.namelist()
    assert c.get("/runs/r1/download/../../etc/passwd").status_code == 404
    assert c.get("/runs/r1/download/anything.else").status_code == 404


def test_new_run_validation_errors(web):
    c, _ = web
    r = post(c, "/runs/new", spec_text="", roster_text="")
    assert r.status_code == 400 and "Provide a spec" in r.get_data(as_text=True)
    r = post(c, "/runs/new", spec_text="name: x\ncriteria: []", roster_text="id,repo\na,b")
    assert r.status_code == 400 and "at least one criterion" in r.get_data(as_text=True)


def test_uploaded_spec_cannot_reference_outside_directories(web):
    c, _ = web
    spec = ("name: x\nsandbox: {image: python:3}\noverlay: ../../../../etc\n"
            "criteria:\n  - {id: a, points: 1, check: {type: file_exists, paths: [x]}}\n")
    r = post(c, "/runs/new", spec_text=spec, roster_text="id,repo\na,https://github.com/o/r")
    assert r.status_code == 400 and "overlay" in r.get_data(as_text=True)


def test_local_paths_rejected_for_web_rosters_and_run_completes(web, demo_run):
    """The web never reads local paths: they come back as per-student fetch errors, not file access."""
    c, runs = web
    spec = ("name: web\nscale: 10\ncriteria:\n"
            "  - {id: a, points: 5, check: {type: file_exists, paths: [x]}}\n"
            "  - {id: m, points: 5, check: {type: manual}}\n")
    roster = f"id,repo\nlocal,{demo_run['dir']}/repos/alice\n"
    r = post(c, "/runs/new", spec_text=spec, roster_text=roster)
    assert r.status_code == 302
    run_id = r.headers["Location"].rsplit("/", 1)[1]
    for _ in range(100):
        st = c.get(f"/runs/{run_id}/status.json").get_json()
        if st["state"] != "running":
            break
        time.sleep(0.1)
    assert st["state"] == "done"
    sub = json.loads((runs / run_id / "run.json").read_text())["submissions"][0]
    assert sub["status"] == "error" and sub["error"]["code"] == "invalid_url"


def test_docker_unavailable_returns_503_with_guidance(tmp_path, monkeypatch):
    monkeypatch.setattr("markbook.grader.docker_status", lambda: (False, "daemon down"))
    app = create_app(tmp_path / "r", runtime="docker")
    spec = ("name: x\nsandbox: {image: python:3}\n"
            "criteria:\n  - {id: a, points: 1, check: {type: command, run: 'true'}}\n")
    r = app.test_client().post("/runs/new", data={"spec_text": spec, "roster_text": "id,repo\na,https://github.com/o/r"})
    assert r.status_code == 503 and "daemon down" in r.get_data(as_text=True)
    assert not any((tmp_path / "r").iterdir()), "no half-created run directory is left behind"


def test_interrupted_run_is_marked_failed_on_restart(tmp_path):
    d = tmp_path / "runs" / "x1"
    d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"state": "running", "name": "n", "total": 3, "done": 1, "active": []}))
    app = create_app(tmp_path / "runs", runtime="none")
    assert json.loads((d / "status.json").read_text())["state"] == "failed"
    assert "Server restarted" in app.test_client().get("/runs/x1").get_data(as_text=True)
