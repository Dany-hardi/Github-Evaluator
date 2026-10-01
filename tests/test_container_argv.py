"""No daemon needed: assert the exact argv each container runtime is given, so an isolation flag
can never be dropped for one runtime (Podman support is verified end to end only by the CI job)."""
import json
import os
import subprocess

import pytest

from markbook import sandbox as sb
from markbook.sandbox import (ContainerSandbox, DockerSandbox, ExecResult, PodmanSandbox, SandboxUnavailable,
                              make_sandbox)
from markbook.spec import SandboxSpec

CFG = SandboxSpec(image="img:1", memory="96m", cpus=1.5, pids=77, disk="12m", timeout=5)
RUNTIMES = ["docker", "podman"]


@pytest.fixture(autouse=True)
def binaries_exist(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")


def _pairs(argv):
    return {argv[i]: argv[i + 1] for i in range(len(argv) - 1)}


@pytest.mark.parametrize("rt", RUNTIMES)
def test_every_isolation_flag_is_present(rt):
    argv = make_sandbox(rt, CFG).run_argv()
    flags = _pairs(argv)
    assert argv[:3] == [rt, "run", "-d"]
    assert flags["--network"] == "none"
    assert flags["--cap-drop"] == "ALL"
    assert flags["--security-opt"] == "no-new-privileges"
    assert flags["--memory"] == "96m" and flags["--memory-swap"] == "96m"  # swap == memory: no swap
    assert flags["--cpus"] == "1.5" and flags["--pids-limit"] == "77"
    assert flags["--label"] == "markbook=1"
    assert flags["--name"].startswith("markbook-")
    uid = flags["--user"].split(":")[0]
    assert uid.isdigit() and uid != "0"
    tmpfs = [argv[i + 1] for i, a in enumerate(argv) if a == "--tmpfs"]
    assert "/tmp:rw,exec,size=64m" in tmpfs and "/work:rw,exec,size=12m,mode=1777" in tmpfs
    # no host mounts of any kind, no privileged/host namespaces, no published ports
    for banned in ("-v", "--volume", "--mount", "--privileged", "--net", "--pid", "--ipc", "-p", "--publish",
                   "--userns", "--cap-add", "--device"):
        assert banned not in argv
    assert argv[-3:] == ["img:1", "sleep", "3600"]


def test_docker_run_argv_is_unchanged():
    d = DockerSandbox(CFG)
    assert d.run_argv() == [
        "docker", "run", "-d", "--name", d.name, "--label", "markbook=1",
        "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--memory", "96m", "--memory-swap", "96m", "--cpus", "1.5", "--pids-limit", "77",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,exec,size=64m", "--tmpfs", "/work:rw,exec,size=12m,mode=1777",
        "-w", "/work", "-e", "HOME=/tmp", "-e", "LANG=C.UTF-8", "img:1", "sleep", "3600"]


def test_podman_as_host_root_never_runs_student_as_root(monkeypatch):
    monkeypatch.setattr(os, "getuid", lambda: 0)
    assert PodmanSandbox(CFG).user == "65534:65534"
    assert DockerSandbox(CFG).user == f"0:{os.getgid()}"  # existing Docker behaviour, untouched


def test_aliases_and_generic_class():
    assert issubclass(DockerSandbox, ContainerSandbox) and issubclass(PodmanSandbox, ContainerSandbox)
    g = ContainerSandbox(CFG, "podman")
    assert g.binary == "podman" and g.runtime == "podman"


def test_missing_binary_names_the_requested_runtime(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    for rt in RUNTIMES:
        with pytest.raises(SandboxUnavailable, match=f"{rt} not found on PATH"):
            make_sandbox(rt, CFG)


def test_unknown_runtime_lists_choices():
    with pytest.raises(SandboxUnavailable, match="'docker', 'podman' or 'none'"):
        make_sandbox("lxd", CFG)


class Recorder:
    """Stands in for run_capped / subprocess.run / Popen and records every argv."""

    def __init__(self, probe_out="memory.max=100663296\nmemory.swap.max=0\npids.max=77\ncpu.max=150000 100000\n"):
        self.calls = []
        self.probe_out = probe_out

    def run_capped(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[1:3] == ["exec", argv[2]] and "memory.max" in " ".join(argv):
            return ExecResult(0, self.probe_out, "", 0.0)
        return ExecResult(0, "", "", 0.0)

    def run(self, argv, **kw):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(sb, "run_capped", r.run_capped)
    monkeypatch.setattr(subprocess, "run", r.run)
    return r


@pytest.mark.parametrize("rt", RUNTIMES)
def test_exec_and_cleanup_use_the_runtime_binary(rt, rec):
    box = make_sandbox(rt, CFG)
    box._start()
    box.exec("echo hi", timeout=4)
    box.close()
    assert all(c[0] == rt for c in rec.calls)
    run = rec.calls[0]
    assert run[1] == "run"
    ex = next(c for c in rec.calls if c[1] == "exec" and "timeout" in c)
    assert ex[1:6] == ["exec", "-i", "-w", "/work", box.name]
    assert ex[6:] == ["timeout", "-s", "KILL", "4", "sh", "-c", "echo hi"]
    assert rec.calls[-1] == [rt, "rm", "-f", box.name]  # always removed
    assert (rec.calls[-2] == [rt, "kill", box.name]) == (rt == "podman")


@pytest.mark.parametrize("rt", RUNTIMES)
def test_load_streams_tar_through_exec_without_a_mount(rt, monkeypatch, tmp_path, rec):
    seen = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            seen["argv"] = argv
            self.stdin = open(os.devnull, "wb")
            self.returncode = 0

        def communicate(self, timeout=None):
            assert self.stdin is None  # the closed pipe must be detached first (Python < 3.13)
            return b"", b""

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    (tmp_path / "a.txt").write_text("x")
    box = make_sandbox(rt, CFG)
    box.load(tmp_path)
    assert seen["argv"] == [rt, "exec", "-i", "-w", "/work", box.name, "tar", "-xf", "-"]


def test_podman_refuses_when_limits_are_not_enforced(monkeypatch):
    bad = Recorder(probe_out="memory.max=max\nmemory.swap.max=MISSING\npids.max=max\ncpu.max=max 100000\n")
    monkeypatch.setattr(sb, "run_capped", bad.run_capped)
    monkeypatch.setattr(subprocess, "run", bad.run)
    box = make_sandbox("podman", CFG)
    with pytest.raises(SandboxUnavailable) as e:
        box._start()
    msg = str(e.value)
    assert "NOT enforced" in msg and "memory.max=max" in msg and "pids.max=max" in msg and "cpu.max=max" in msg
    assert "delegated" in msg
    assert bad.calls[-1][:3] == ["podman", "rm", "-f"], "the container is removed before refusing"


def test_podman_refuses_when_limit_files_are_unreadable(monkeypatch):
    bad = Recorder(probe_out="memory.max=MISSING\nmemory.swap.max=MISSING\npids.max=MISSING\ncpu.max=MISSING\n")
    monkeypatch.setattr(sb, "run_capped", bad.run_capped)
    monkeypatch.setattr(subprocess, "run", bad.run)
    with pytest.raises(SandboxUnavailable, match="NOT enforced"):
        make_sandbox("podman", CFG)._start()


def test_podman_refuses_when_swap_is_allowed(monkeypatch):
    bad = Recorder(probe_out="memory.max=1\nmemory.swap.max=max\npids.max=7\ncpu.max=1 1\n")
    monkeypatch.setattr(sb, "run_capped", bad.run_capped)
    monkeypatch.setattr(subprocess, "run", bad.run)
    with pytest.raises(SandboxUnavailable, match="memory.swap.max=max"):
        make_sandbox("podman", CFG)._start()


def test_docker_start_does_not_probe_cgroups(rec):
    make_sandbox("docker", CFG)._start()
    assert len(rec.calls) == 1


def test_start_failure_message_has_podman_hints(monkeypatch):
    monkeypatch.setattr(sb, "run_capped", lambda argv, **kw: ExecResult(125, "", "Error: short name", 0.0))
    with pytest.raises(SandboxUnavailable, match="subuid.*fully qualified"):
        make_sandbox("podman", CFG)._start()
    with pytest.raises(SandboxUnavailable) as e:
        make_sandbox("docker", CFG)._start()
    assert "subuid" not in str(e.value)


# ── status / images / orphans ────────────────────────────────────────────────

def _info(rootless=True, cgver="v2", controllers=("cpu", "memory", "pids", "io")):
    return json.dumps({"version": {"Version": "4.9.3"},
                       "host": {"cgroupVersion": cgver, "cgroupControllers": list(controllers),
                                "security": {"rootless": rootless}}})


def _fake_info(monkeypatch, out, rc=0):
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, rc, out, "boom\n" if rc else "")
    monkeypatch.setattr(subprocess, "run", run)
    return seen


def test_podman_status_ok_rootless_cgroup_v2(monkeypatch):
    seen = _fake_info(monkeypatch, _info())
    ok, detail = sb.podman_status()
    assert ok and detail == "podman 4.9.3 rootless" and seen[0] == ["podman", "info", "--format", "json"]


@pytest.mark.parametrize("info,needle", [
    (_info(cgver="v1"), "cgroup v1"),
    (_info(controllers=("cpu", "io")), "memory"),
    (_info(controllers=("memory", "pids")), "cpu"),
    ("not json", "refusing to guess"),
    (json.dumps({"host": {}}), "refusing to guess"),
])
def test_podman_status_fails_loudly(monkeypatch, info, needle):
    _fake_info(monkeypatch, info)
    ok, detail = sb.podman_status()
    assert not ok and needle in detail


def test_podman_status_rootful_needs_no_delegation(monkeypatch):
    _fake_info(monkeypatch, _info(rootless=False, cgver="v1", controllers=()))
    assert sb.podman_status() == (True, "podman 4.9.3 rootful")


def test_podman_info_error_is_reported(monkeypatch):
    _fake_info(monkeypatch, "", rc=125)
    assert sb.podman_status() == (False, "boom")


def test_docker_status_unchanged(monkeypatch):
    seen = _fake_info(monkeypatch, "26.1.0\n")
    assert sb.docker_status() == (True, "docker 26.1.0")
    assert seen[0] == ["docker", "info", "--format", "{{.ServerVersion}}"]


def test_ensure_image_commands(monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if argv[1:3] in (["image", "exists"], ["image", "inspect"]) else 0, "", "")
    monkeypatch.setattr(subprocess, "run", run)
    sb.ensure_image("i:1", None, "podman")
    sb.ensure_image("i:1")
    assert calls == [["podman", "image", "exists", "i:1"], ["podman", "pull", "--quiet", "i:1"],
                     ["docker", "image", "inspect", "i:1"], ["docker", "pull", "--quiet", "i:1"]]


def test_ensure_image_pull_failure_names_hint(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 125, "", "short-name\n"))
    with pytest.raises(SandboxUnavailable, match="fully qualified"):
        sb.ensure_image("busybox", None, "podman")


@pytest.mark.parametrize("rt", RUNTIMES)
def test_remove_orphans_uses_label_and_binary(rt, monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "abc\ndef\n" if argv[1] == "ps" else "", "")
    monkeypatch.setattr(subprocess, "run", run)
    assert sb.remove_orphans(rt) == 2
    assert calls == [[rt, "ps", "-aq", "--filter", "label=markbook=1"], [rt, "rm", "-f", "abc", "def"]]


# ── preflight / CLI / schema wiring ──────────────────────────────────────────

def _spec(tmp_path):
    from markbook.spec import load_spec
    (tmp_path / "s.yaml").write_text("name: x\nsandbox: {image: python:3}\n"
                                     "criteria:\n  - {id: a, points: 1, check: {type: command, run: 'true'}}\n")
    return load_spec(tmp_path / "s.yaml")


@pytest.mark.parametrize("rt,name", [("docker", "Docker"), ("podman", "Podman")])
def test_preflight_names_the_requested_runtime(rt, name, tmp_path, monkeypatch):
    from markbook import grader
    monkeypatch.setattr(f"markbook.grader.{rt}_status", lambda: (False, "nope"))
    with pytest.raises(SandboxUnavailable, match=f"(?s){name} is required.*nope.*Start {name}"):
        grader.preflight(_spec(tmp_path), rt)


def test_preflight_ensures_image_with_the_runtime(tmp_path, monkeypatch):
    from markbook import grader
    got = []
    monkeypatch.setattr("markbook.grader.podman_status", lambda: (True, "podman x"))
    monkeypatch.setattr("markbook.grader.ensure_image", lambda image, log, rt: got.append((image, rt)))
    grader.preflight(_spec(tmp_path), "podman")
    assert got == [("python:3", "podman")]


def test_cli_accepts_podman_and_schema_allows_it():
    from markbook import cli
    from importlib import resources
    p = cli.build_parser()
    for cmd in (["grade", "s.yaml", "--repo", "r"], ["retry", "run"], ["serve"], ["demo"]):
        assert p.parse_args([*cmd, "--sandbox", "podman"]).sandbox == "podman"
    schema = json.loads(resources.files("markbook").joinpath("schema/report.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["assignment"]["properties"]["sandbox"]["properties"]["runtime"]["enum"] == \
        ["docker", "podman", "none"]


def test_doctor_reports_both_runtimes_and_cleans_each_present_one(monkeypatch, capsys):
    from markbook import cli
    monkeypatch.setattr(cli, "container_status",
                        lambda rt: (True, "podman 4 rootless") if rt == "podman" else (False, "docker not found on PATH"))
    cleaned = []
    monkeypatch.setattr(cli, "remove_orphans", lambda rt: cleaned.append(rt) or 3)
    rc = cli.main(["doctor", "--clean"])
    out = capsys.readouterr().out
    assert rc == 0 and "podman (sandbox)" in out and "docker (sandbox)" in out
    assert cleaned == ["podman"] and "podman: removed 3 orphaned" in out


def test_doctor_fails_when_no_runtime_is_usable(monkeypatch, capsys):
    from markbook import cli
    monkeypatch.setattr(cli, "container_status", lambda rt: (False, f"{rt} not found on PATH"))
    assert cli.main(["doctor"]) == 1
    assert "none usable" in capsys.readouterr().out
