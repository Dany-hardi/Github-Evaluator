import json
import subprocess
from importlib import resources
from pathlib import Path

import pytest

from markbook.demo import build_demo
from markbook.grader import grade_cohort
from markbook.roster import load_roster
from markbook.spec import load_spec


def git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@e.x",
                        "-c", "init.defaultBranch=main", *args],
                       capture_output=True, text=True, check=True, env=env)
    return r.stdout.strip()


def make_repo(path: Path, files: dict[str, str], date: str | None = "2026-02-10T10:00:00+00:00") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    for rel, content in files.items():
        f = path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init", date=date)
    return path


@pytest.fixture(scope="session")
def demo_run(tmp_path_factory):
    """The demo cohort graded once for the whole session (it is deterministic)."""
    d = tmp_path_factory.mktemp("demo")
    spec_p, roster_p = build_demo(d)
    spec = load_spec(spec_p)
    run = grade_cohort(spec, load_roster(roster_p), "none", allow_local=True, jobs=4, run_id="testrun")
    return {"dir": d, "spec_path": spec_p, "roster_path": roster_p, "spec": spec, "run": run}


@pytest.fixture(scope="session")
def schema():
    return json.loads(resources.files("markbook").joinpath("schema/report.schema.json").read_text())


def sub_of(run: dict, sid: str) -> dict:
    return next(s for s in run["submissions"] if s["id"] == sid)


def crit_of(sub: dict, cid: str) -> dict:
    return next(c for c in sub["criteria"] if c["id"] == cid)
