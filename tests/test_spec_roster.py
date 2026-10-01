import textwrap

import pytest

from markbook.roster import RosterError, parse_roster
from markbook.spec import SpecError, load_spec

VALID = """
name: T
scale: 20
pass_mark: 10
deadline: 2026-03-01T23:59:00Z
late_penalty: {percent_per_day: 10}
sandbox: {image: "python:3.12-slim"}
criteria:
  - {id: a, points: 5, check: {type: file_exists, paths: [x.py]}}
  - {id: b, points: 5, requires: [a], check: {type: command, run: "python3 x.py"}}
"""


def write(tmp_path, text, name="spec.yaml"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text))
    return p


def test_valid_spec(tmp_path):
    s = load_spec(write(tmp_path, VALID))
    assert s.max_points == 10 and s.scale == 20 and s.deadline.tzinfo is not None
    assert s.borderline_margin == pytest.approx(0.4)  # 2% of scale by default
    assert len(s.sha256) == 64


def test_errors_are_aggregated_not_first_only(tmp_path):
    bad = """
    name: ""
    criteria:
      - {id: a, points: 0, check: {type: file_exists, paths: [x]}}
      - {id: a, points: 1, check: {type: nope}}
      - {id: b, points: 1, check: {type: command, run: x}}
      - {id: c, points: 1, requires: [later], check: {type: git, bogus: 1}}
    """
    with pytest.raises(SpecError) as e:
        load_spec(write(tmp_path, bad))
    msg = str(e.value)
    for needle in ("name: required", "points must be a positive", "check.type must be one of",
                   "sandbox.image: required", "unknown check option", "requires 'later'"):
        assert needle in msg, needle


def test_deadline_needs_timezone(tmp_path):
    with pytest.raises(SpecError, match="timezone"):
        load_spec(write(tmp_path, VALID.replace("2026-03-01T23:59:00Z", "2026-03-01T23:59:00")))


def test_late_penalty_requires_deadline(tmp_path):
    with pytest.raises(SpecError, match="requires a deadline"):
        load_spec(write(tmp_path, VALID.replace("deadline: 2026-03-01T23:59:00Z\n", "")))


def test_cases_file_must_stay_inside_spec_dir(tmp_path):
    spec = VALID.replace("check: {type: command, run: \"python3 x.py\"}",
                         "check: {type: cases, run: x, cases_file: ../../etc/passwd}")
    with pytest.raises(SpecError, match="inside the spec directory"):
        load_spec(write(tmp_path, spec))


def test_cases_file_is_merged(tmp_path):
    (tmp_path / "c.yaml").write_text("cases:\n  - {name: one, stdout: '1'}\n")
    spec = VALID.replace("check: {type: command, run: \"python3 x.py\"}",
                         "check: {type: cases, run: x, cases_file: c.yaml}")
    s = load_spec(write(tmp_path, spec))
    assert s.criteria[1].check["cases"][0]["name"] == "one"


def test_yaml_error_is_reported(tmp_path):
    with pytest.raises(SpecError, match="invalid YAML"):
        load_spec(write(tmp_path, "name: [unclosed"))


# ── roster ────────────────────────────────────────────────────────────────────

def test_roster_aliases_and_derived_ids():
    r = parse_roster("Student,GitHub URL\nAda,https://github.com/ada/proj\n")
    # 'student' is a name alias; 'github_url' is a repo alias; id derives from the repo path
    assert r[0].name == "Ada" and r[0].id == "ada_proj"


def test_roster_sanitises_ids_and_skips_blank_rows():
    r = parse_roster("id,repo\n  ../../etc/x ,https://github.com/a/b\n,\n")
    assert [e.id for e in r] == ["etc_x"]


def test_roster_duplicate_ids_rejected():
    with pytest.raises(RosterError, match="duplicate"):
        parse_roster("id,repo\na,r1\na,r2\n")


def test_roster_needs_repo_column():
    with pytest.raises(RosterError, match="repository column"):
        parse_roster("id,name\na,b\n")


def test_confined_spec_rejects_paths_outside_its_directory(tmp_path):
    (tmp_path / "ov").mkdir()
    base = tmp_path / "up"
    base.mkdir()
    spec = VALID.replace("criteria:", "overlay: ../ov\ncriteria:")
    p = write(base, spec)
    assert load_spec(p).overlay == (tmp_path / "ov").resolve()          # trusted CLI use: allowed
    with pytest.raises(SpecError, match="must stay inside"):             # untrusted upload: refused
        load_spec(p, confine=True)
