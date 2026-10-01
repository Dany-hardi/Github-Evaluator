"""Isolation tests against a real Docker daemon. Skipped when Docker or a
small busybox-style image is not available locally (no network pulls in CI of
this repo): set MARKBOOK_TEST_IMAGE to override the image."""
import os
import subprocess

import pytest

from markbook.sandbox import DockerSandbox, docker_status
from markbook.spec import SandboxSpec

IMAGE = os.environ.get("MARKBOOK_TEST_IMAGE", "nginx:alpine")


def _usable() -> bool:
    ok, _ = docker_status()
    if not ok:
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0


pytestmark = [pytest.mark.docker, pytest.mark.skipif(not _usable(), reason=f"needs Docker and local image {IMAGE}")]


@pytest.fixture
def box(tmp_path):
    (tmp_path / "hello.txt").write_text("hi\n")
    sb = DockerSandbox(SandboxSpec(image=IMAGE, timeout=3, memory="64m", disk="8m", pids=64))
    sb.load(tmp_path)
    yield sb
    sb.close()


def test_runs_as_non_root_with_sources_present(box):
    r = box.exec("cat hello.txt; id -u")
    assert r.exit_code == 0 and r.stdout.split() == ["hi", str(os.getuid())]


def test_no_network(box):
    r = box.exec("wget -T2 -qO- http://1.1.1.1")
    assert r.exit_code != 0 and "unreachable" in r.stderr.lower()


def test_rootfs_not_writable(box):
    assert box.exec("echo x > /etc/pwned").exit_code != 0


def test_disk_is_capped(box):
    r = box.exec("dd if=/dev/zero of=big bs=1M count=100", timeout=10)
    assert r.exit_code != 0 and "no space" in r.stderr.lower()


def test_runaway_process_is_killed_at_timeout(box):
    r = box.exec("while true; do :; done", timeout=2)
    assert r.timed_out and r.duration < 6


def test_host_filesystem_not_visible(box, tmp_path):
    # Neither the host home directory nor the sources' original host path exist inside the container.
    assert box.exec(f"test ! -e {os.path.expanduser('~')} && test ! -e {tmp_path}").exit_code == 0


def test_no_container_left_behind(tmp_path):
    sb = DockerSandbox(SandboxSpec(image=IMAGE))
    sb.load(tmp_path)
    name = sb.name
    sb.close()
    out = subprocess.run(["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
                         capture_output=True, text=True).stdout
    assert out.strip() == ""


# ── parity with a real language image (Debian userland, GNU tar) ──────────────
# Regression: GNU tar failed to extract into the root-owned /work mount, which busybox tolerated, so
# every command criterion errored under a real image while the busybox tests stayed green.

PY_IMAGE = os.environ.get("MARKBOOK_TEST_PY_IMAGE", "python:3.12-slim")
_have_py = subprocess.run(["docker", "image", "inspect", PY_IMAGE], capture_output=True).returncode == 0 \
    if docker_status()[0] else False


@pytest.mark.skipif(not _have_py, reason=f"needs local image {PY_IMAGE}")
def test_docker_and_local_grade_the_demo_identically(demo_run, tmp_path):
    import dataclasses
    from markbook.grader import grade_cohort
    from markbook.roster import load_roster
    spec = dataclasses.replace(demo_run["spec"], sandbox=dataclasses.replace(demo_run["spec"].sandbox, image=PY_IMAGE))
    docker_run = grade_cohort(spec, load_roster(demo_run["roster_path"]), "docker", allow_local=True, jobs=4)
    view = lambda r: {s["id"]: (s["status"], [(c["id"], c["status"], c["points"]) for c in s["criteria"]])
                      for s in r["submissions"]}
    assert view(docker_run) == view(demo_run["run"])
