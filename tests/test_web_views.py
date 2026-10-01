"""View events: session boundaries for `markbook stats` (same fixture pattern as test_web.py)."""
import pytest

from markbook import cli
from markbook.views import load_views
from markbook.web.app import create_app

OK = {"Origin": "http://localhost"}


@pytest.fixture
def web(tmp_path, demo_run):
    runs = tmp_path / "runs"
    cli.main(["grade", str(demo_run["spec_path"]), "--roster", str(demo_run["roster_path"]),
              "--sandbox", "none", "--out", str(runs / "r1"), "--quiet"])
    app = create_app(runs, runtime="none")
    app.config["TESTING"] = True
    return app.test_client(), runs


def test_view_event_recorded(web):
    c, runs = web
    r = c.post("/runs/r1/s/alice/view", data={"reviewer": "ann"}, headers=OK)
    assert r.status_code == 204
    v = load_views(runs / "r1")
    assert len(v) == 1 and v[0]["submission"] == "alice" and v[0]["reviewer"] == "ann" and v[0]["run"] == "r1"
    assert v[0]["at"].endswith("+00:00")
    # no reviewer name -> "web", the same default as decisions
    assert c.post("/runs/r1/s/bob/view", headers=OK).status_code == 204
    assert load_views(runs / "r1")[-1]["reviewer"] == "web"


def test_view_event_rejections_write_nothing(web):
    c, runs = web
    assert c.post("/runs/r1/s/alice/view", data={"reviewer": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/runs/r1/s/alice/view", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/runs/nope/s/alice/view", headers=OK).status_code == 404
    assert c.post("/runs/.hidden/s/alice/view", headers=OK).status_code == 404
    assert c.post("/runs/r1/s/nobody/view", headers=OK).status_code == 404
    assert c.post("/runs/r1/s/alice/view", data={"reviewer": "x" * 61}, headers=OK).status_code == 400
    assert load_views(runs / "r1") == []
    assert c.post("/runs/r1/s/alice/view", data={"reviewer": "x" * 60}, headers=OK).status_code == 204
    assert [v["reviewer"] for v in load_views(runs / "r1")] == ["x" * 60]
    assert c.get("/runs/r1/s/alice/view").status_code == 405


def test_submission_page_has_beacon_and_no_inline_script(web):
    c, _ = web
    html = c.get("/runs/r1/s/alice").get_data(as_text=True)
    assert 'id="view-beacon"' in html and "/runs/r1/s/alice/view" in html
    assert "<script>" not in html


def test_stats_uses_recorded_views(web, capsys):
    c, runs = web
    c.post("/runs/r1/s/alice/view", data={"reviewer": "ann"}, headers=OK)
    capsys.readouterr()
    assert cli.main(["stats", str(runs / "r1"), "--json"]) == 0
    import json
    assert json.loads(capsys.readouterr().out)["has_views"] is True
