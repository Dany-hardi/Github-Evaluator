"""Isolation tests against a real container runtime, run for every runtime that works here
(docker, and podman when it is installed and usable). A runtime is skipped when it or a small
busybox-style image is not available locally (no network pulls in this suite): set
MARKBOOK_TEST_IMAGE (docker) / MARKBOOK_TEST_PODMAN_IMAGE (podman, falls back to MARKBOOK_TEST_IMAGE;
use a fully qualified name such as docker.io/library/busybox:latest) to override the image."""
import os
import subprocess

import pytest

from markbook.sandbox import ContainerSandbox, container_status
from markbook.spec import SandboxSpec

IMAGES = {
    "docker": os.environ.get("MARKBOOK_TEST_IMAGE", "nginx:alpine"),
    "podman": os.environ.get("MARKBOOK_TEST_PODMAN_IMAGE",
                             os.environ.get("MARKBOOK_TEST_IMAGE", "docker.io/library/nginx:alpine")),
}
PY_IMAGES = {
    "docker": os.environ.get("MARKBOOK_TEST_PY_IMAGE", "python:3.12-slim"),
    "podman": os.environ.get("MARKBOOK_TEST_PODMAN_PY_IMAGE",
                             os.environ.get("MARKBOOK_TEST_PY_IMAGE", "docker.io/library/python:3.12-slim")),
}


def _has_image(rt: str, image: str) -> bool:
    cmd = [rt, "image", "exists", image] if rt == "podman" else [rt, "image", "inspect", image]
    return subprocess.run(cmd, capture_output=True).returncode == 0


def _usable(rt: str) -> bool:
    return container_status(rt)[0] and _has_image(rt, IMAGES[rt])


def _params(usable):
    return [pytest.param(rt, marks=pytest.mark.skipif(not usable(rt), reason=f"needs {rt} and its local test image"))
            for rt in ("docker", "podman")]


pytestmark = pytest.mark.docker


@pytest.fixture(params=_params(_usable))
def runtime(request):
    return request.param


@pytest.fixture
def box(runtime, tmp_path):
    (tmp_path / "hello.txt").write_text("hi\n")
    sb = ContainerSandbox(SandboxSpec(image=IMAGES[runtime], timeout=3, memory="64m", disk="8m", pids=64), runtime)
    sb.load(tmp_path)
    yield sb
    sb.close()


def test_runs_as_non_root_with_sources_present(box):
    r = box.exec("cat hello.txt; id -u")
    assert r.exit_code == 0 and r.stdout.split() == ["hi", box.user.split(":")[0]]
    assert box.user.split(":")[0] != "0"


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


def test_no_container_left_behind(runtime, tmp_path):
    sb = ContainerSandbox(SandboxSpec(image=IMAGES[runtime]), runtime)
    sb.load(tmp_path)
    name = sb.name
    sb.close()
    out = subprocess.run([runtime, "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
                         capture_output=True, text=True).stdout
    assert out.strip() == ""


# ── parity with a real language image (Debian userland, GNU tar) ──────────────
# Regression: GNU tar failed to extract into the root-owned /work mount, which busybox tolerated, so
# every command criterion errored under a real image while the busybox tests stayed green.

@pytest.fixture(params=_params(lambda rt: container_status(rt)[0] and _has_image(rt, PY_IMAGES[rt])))
def py_runtime(request):
    return request.param


def test_container_and_local_grade_the_demo_identically(py_runtime, demo_run, tmp_path):
    import dataclasses
    from markbook.grader import grade_cohort
    from markbook.roster import load_roster
    spec = dataclasses.replace(demo_run["spec"], sandbox=dataclasses.replace(demo_run["spec"].sandbox,
                                                                              image=PY_IMAGES[py_runtime]))
    run = grade_cohort(spec, load_roster(demo_run["roster_path"]), py_runtime, allow_local=True, jobs=4)
    view = lambda r: {s["id"]: (s["status"], [(c["id"], c["status"], c["points"]) for c in s["criteria"]])
                      for s in r["submissions"]}
    assert view(run) == view(demo_run["run"])
