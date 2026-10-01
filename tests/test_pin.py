import csv
import json
import subprocess
from datetime import datetime, timezone

import pytest

from markbook import cli
from markbook.pin import pin_one, pin_roster
from markbook.roster import Entry, RosterError, load_roster
from conftest import git, make_repo

DEADLINE = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
SPEC = """
name: Pin test
version: 1
deadline: 2026-03-01T12:00:00Z
criteria:
  - id: has_a
    title: has a.txt
    points: 1
    check: {type: file_exists, paths: ["a.txt"]}
"""


def commit(repo, name, text, date):
    (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"add {name}", date=date)
    return git(repo, "rev-parse", "HEAD")


def write_roster(path, rows, header="id,name,email,repo,ref"):
    path.write_text(header + "\n" + "\n".join(rows) + "\n")
    return path


@pytest.fixture
def spec(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(SPEC)
    return p


def pin_now(repo, ref=None, **kw):
    return pin_one(Entry("s", "S", "", str(repo), ref), mode="now", deadline=None, token=None,
                   bundle_dir=kw.get("bundle_dir"))


def test_now_captures_current_head(tmp_path):
    repo = make_repo(tmp_path / "r", {"a.txt": "1"})
    p = pin_now(repo)
    assert p.sha == git(repo, "rev-parse", "HEAD")
    assert p.error is None and p.verified and p.via == "ls-remote"


def test_ref_column_branch_and_tag_honoured(tmp_path):
    repo = make_repo(tmp_path / "r", {"a.txt": "1"})
    first = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "v1")
    git(repo, "checkout", "-q", "-b", "feature")
    feat = commit(repo, "b.txt", "x", "2026-02-11T10:00:00+00:00")
    git(repo, "checkout", "-q", "main")
    commit(repo, "c.txt", "x", "2026-02-12T10:00:00+00:00")
    assert pin_now(repo, "feature").sha == feat
    assert pin_now(repo, "v1").sha == first, "tag resolves to the commit"
    p = pin_now(repo, "nope")
    assert p.error == "ref_not_found" and p.sha == ""


def test_annotated_tag_is_peeled(tmp_path):
    repo = make_repo(tmp_path / "r", {"a.txt": "1"})
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "tag", "-a", "v1", "-m", "release")
    assert pin_now(repo, "v1").sha == sha


def test_full_sha_passthrough_and_short_sha(tmp_path):
    repo = make_repo(tmp_path / "r", {"a.txt": "1"})
    first = git(repo, "rev-parse", "HEAD")
    commit(repo, "b.txt", "x", "2026-02-11T10:00:00+00:00")
    p = pin_now(repo, first)
    assert p.sha == first and p.via == "given" and p.error is None
    assert not p.verified, "an old commit is not an advertised tip, so it is not claimed as verified"
    assert pin_now(repo, first[:8]).sha == first


def test_deadline_picks_last_commit_before(tmp_path):
    repo = make_repo(tmp_path / "r", {"a.txt": "1"}, date="2026-02-10T10:00:00+00:00")
    before = commit(repo, "b.txt", "x", "2026-03-01T11:59:59+00:00")
    on = commit(repo, "c.txt", "x", "2026-03-01T12:00:00+00:00")
    commit(repo, "d.txt", "x", "2026-03-01T12:00:01+00:00")
    commit(repo, "e.txt", "x", "2026-03-05T10:00:00+00:00")
    p = pin_one(Entry("s", "S", "", str(repo)), mode="deadline", deadline=DEADLINE, token=None, bundle_dir=None)
    assert p.sha == on and p.sha != before, "a commit exactly at the deadline counts as on time"
    assert p.sha != before and p.via == "clone" and p.verified


def test_deadline_no_commit_before(tmp_path):
    repo = make_repo(tmp_path / "r", {"a.txt": "1"}, date="2026-03-02T10:00:00+00:00")
    p = pin_one(Entry("s", "S", "", str(repo)), mode="deadline", deadline=DEADLINE, token=None, bundle_dir=None)
    assert p.error == "no_commit_before_deadline" and p.sha == ""


def test_missing_and_empty_repo(tmp_path):
    p = pin_now(tmp_path / "does-not-exist")
    assert p.error == "invalid_url" and p.sha == ""
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    assert pin_now(empty).error == "empty_repository"
    q = pin_one(Entry("s", "S", "", str(empty)), mode="deadline", deadline=DEADLINE, token=None, bundle_dir=None)
    assert q.error == "empty_repository"
    assert pin_now(f"file://{tmp_path}/nope.git").error is not None


def test_dash_prefixed_values_rejected(tmp_path):
    repo = make_repo(tmp_path / "r", {"a.txt": "1"})
    assert pin_now(repo, "--upload-pack=x").error == "invalid_url"
    assert pin_now("--upload-pack=x").error == "invalid_url"


def test_bundle_contains_sha(tmp_path):
    repo = make_repo(tmp_path / "r", {"a.txt": "1"})
    sha = commit(repo, "b.txt", "x", "2026-02-11T10:00:00+00:00")
    bdir = tmp_path / "bundles"
    p = pin_now(repo, bundle_dir=bdir)
    assert p.error is None and p.bundle == f"s-{sha[:12]}.bundle"
    f = bdir / p.bundle
    heads = subprocess.run(["git", "bundle", "list-heads", str(f)], capture_output=True, text=True, check=True).stdout
    assert sha in heads
    # The evidence survives deletion of the repo: clone from the bundle.
    dest = tmp_path / "restored"
    dest.mkdir()
    git(dest, "init", "-q")
    git(dest, "fetch", "-q", str(f), "refs/markbook/pin")
    assert git(dest, "rev-parse", "FETCH_HEAD") == sha
    assert git(dest, "cat-file", "-t", sha) == "commit"
    assert not list(bdir.glob("*.part"))


def test_bundle_deadline_mode(tmp_path):
    repo = make_repo(tmp_path / "r", {"a.txt": "1"}, date="2026-02-10T10:00:00+00:00")
    old = git(repo, "rev-parse", "HEAD")
    commit(repo, "late.txt", "x", "2026-04-01T10:00:00+00:00")
    p = pin_one(Entry("s", "S", "", str(repo)), mode="deadline", deadline=DEADLINE, token=None,
                bundle_dir=tmp_path / "b")
    assert p.sha == old and p.bundle


def test_manifest_csv_and_exit_code(tmp_path, spec, capsys):
    good = make_repo(tmp_path / "good", {"a.txt": "1"})
    sha = git(good, "rev-parse", "HEAD")
    roster = write_roster(tmp_path / "roster.csv", [
        f"alice,Alice,a@x.y,{good},",
        f"ghost,Ghost,g@x.y,{tmp_path}/ghost,",
    ])
    rc = cli.main(["pin", str(spec), "--roster", str(roster)])
    assert rc == cli.EXIT_PIN == 5, "unresolved rows give a non-zero exit but everything is still written"
    capsys.readouterr()
    out = tmp_path / "roster.pinned.csv"
    rows = list(csv.DictReader(out.open()))
    assert list(rows[0]) == ["id", "name", "email", "repo", "ref", "pinned_at"]
    assert rows[0]["ref"] == sha and rows[0]["pinned_at"].endswith("Z")
    assert rows[1]["ref"] == "" and rows[1]["pinned_at"] == ""
    man = json.loads((tmp_path / "pins.json").read_text())
    a, g = man["pins"]["alice"], man["pins"]["ghost"]
    assert man["mode"] == "now" and a["sha"] == sha and a["mode"] == "now" and a["error"] is None
    assert a["verified"] is True and a["captured_at"] == rows[0]["pinned_at"] and a["repo"] == str(good)
    assert g["sha"] is None and g["error"] == "invalid_url"
    assert roster.read_text().count("pinned_at") == 0, "original roster is untouched"


def test_all_ok_exits_zero_and_deadline_needs_spec_deadline(tmp_path, spec, capsys):
    good = make_repo(tmp_path / "good", {"a.txt": "1"})
    roster = write_roster(tmp_path / "r.csv", [f"alice,A,,{good},"])
    assert cli.main(["pin", str(spec), "--roster", str(roster), "--out", str(tmp_path / "o" / "p.csv"),
                     "--bundle", str(tmp_path / "bun")]) == 0
    assert (tmp_path / "o" / "pins.json").is_file() and len(list((tmp_path / "bun").glob("*.bundle"))) == 1
    nodl = tmp_path / "nodl.yaml"
    nodl.write_text(SPEC.replace("deadline: 2026-03-01T12:00:00Z\n", ""))
    assert cli.main(["pin", str(nodl), "--roster", str(roster), "--at", "deadline"]) == 1
    assert "deadline" in capsys.readouterr().err
    with pytest.raises(RosterError):
        pin_roster(roster, roster)


def test_pinned_roster_roundtrips_load_roster(tmp_path):
    good = make_repo(tmp_path / "good", {"a.txt": "1"})
    sha = git(good, "rev-parse", "HEAD")
    roster = write_roster(tmp_path / "r.csv", [f"alice,A,a@x.y,{good},main", f"bob,B,,{good},"],
                          header="id,name,email,repo,ref")
    res = pin_roster(roster, tmp_path / "p.csv")
    assert not res.failed
    entries = load_roster(tmp_path / "p.csv")
    assert [(e.id, e.name, e.email, e.repo, e.ref) for e in entries] == \
        [("alice", "A", "a@x.y", str(good), sha), ("bob", "B", "", str(good), sha)]
    # Re-pinning an already pinned roster (pinned_at present) works too.
    pin_roster(tmp_path / "p.csv", tmp_path / "p2.csv")
    assert load_roster(tmp_path / "p2.csv") == entries


def test_roster_without_ref_column_and_unknown_columns(tmp_path):
    good = make_repo(tmp_path / "good", {"a.txt": "1"})
    roster = tmp_path / "r.csv"
    roster.write_text(f"repo,notes\n{good},hello\n")
    pin_roster(roster, tmp_path / "p.csv")
    row = next(csv.DictReader((tmp_path / "p.csv").open()))
    assert list(row) == ["repo", "notes", "ref", "pinned_at"] and row["notes"] == "hello"


def test_grade_uses_pinned_sha_after_later_push(tmp_path, spec, capsys):
    repo = make_repo(tmp_path / "stu", {"a.txt": "1"})
    pinned = git(repo, "rev-parse", "HEAD")
    roster = write_roster(tmp_path / "roster.csv", [f"alice,Alice,,{repo},"])
    assert cli.main(["pin", str(spec), "--roster", str(roster)]) == 0
    later = commit(repo, "b.txt", "pushed after the deadline", "2026-03-10T10:00:00+00:00")
    assert later != pinned

    out = tmp_path / "run"
    assert cli.main(["grade", str(spec), "--roster", str(tmp_path / "roster.pinned.csv"),
                     "--sandbox", "none", "--out", str(out), "--quiet"]) == 0
    capsys.readouterr()
    row = next(csv.DictReader((out / "grades.csv").open()))
    assert row["commit"] == pinned

    # Control: the unpinned roster follows the student's later push.
    out2 = tmp_path / "run2"
    assert cli.main(["grade", str(spec), "--roster", str(roster), "--sandbox", "none",
                     "--out", str(out2), "--quiet"]) == 0
    assert next(csv.DictReader((out2 / "grades.csv").open()))["commit"] == later
