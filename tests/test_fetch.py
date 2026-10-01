import pytest

from markbook.fetch import FetchError, _classify, _git_env, checkout, normalize_repo
from conftest import git, make_repo


def test_normalize_forms():
    assert normalize_repo("owner/repo") == ("https://github.com/owner/repo.git", None, "")
    assert normalize_repo("github.com/o/r") == ("https://github.com/o/r", None, "")
    assert normalize_repo("git@github.com:o/r.git") == ("https://github.com/o/r.git", None, "")
    assert normalize_repo("https://github.com/o/r/tree/dev/lab1/src") == \
        ("https://github.com/o/r.git", "dev", "lab1/src")


def test_local_paths_refused_unless_allowed(tmp_path):
    repo = make_repo(tmp_path / "r", {"a": "1"})
    with pytest.raises(FetchError) as e:
        checkout(str(repo), tmp_path / "d")
    assert e.value.code == "invalid_url"
    assert checkout(str(repo), tmp_path / "d2", allow_local=True).commit


@pytest.mark.parametrize("bad", ["-oProxyCommand=evil", "ssh://host/x", "ftp://x/y"])
def test_hostile_locations_rejected(tmp_path, bad):
    with pytest.raises(FetchError) as e:
        checkout(bad, tmp_path / "d", allow_local=True)
    assert e.value.code == "invalid_url"


def test_ref_checkout_pins_commit(tmp_path):
    repo = make_repo(tmp_path / "r", {"a.txt": "v1"})
    first = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("v2")
    git(repo, "commit", "-qam", "second")
    co = checkout(str(repo), tmp_path / "d", ref=first, allow_local=True)
    assert co.commit == first and (co.path / "a.txt").read_text() == "v1"


def test_bad_ref_and_flag_like_ref(tmp_path):
    repo = make_repo(tmp_path / "r", {"a": "1"})
    with pytest.raises(FetchError) as e:
        checkout(str(repo), tmp_path / "d", ref="nope", allow_local=True)
    assert e.value.code == "ref_not_found"
    with pytest.raises(FetchError) as e:
        checkout(str(repo), tmp_path / "d2", ref="--upload-pack=x", allow_local=True)
    assert e.value.code == "invalid_url"


def test_subdir_must_exist_and_stay_inside(tmp_path):
    repo = make_repo(tmp_path / "r", {"lab1/main.py": "1"})
    url = f"{repo}"
    co = checkout(url, tmp_path / "d", allow_local=True)
    assert co.workdir == co.path
    with pytest.raises(FetchError) as e:
        checkout("https://github.com/o/r/tree/main/../../x", tmp_path / "d2", allow_local=True)
    assert e.value.code in ("subdir_not_found", "network", "not_found_or_private", "clone_failed")


def test_empty_repo(tmp_path):
    empty = tmp_path / "e"
    empty.mkdir()
    git(empty, "init", "-q")
    with pytest.raises(FetchError) as e:
        checkout(str(empty), tmp_path / "d", allow_local=True)
    assert e.value.code in ("empty_repository", "clone_failed")


def test_token_never_in_argv_and_only_for_github():
    env = _git_env("s3cret", "https://github.com/o/r.git")
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader" and "s3cret" not in " ".join(env.values())  # base64'd
    other = _git_env("s3cret", "https://gitlab.example.com/o/r.git")
    assert "GIT_CONFIG_KEY_0" not in other, "token must not leak to non-GitHub hosts"


def test_error_classification():
    assert _classify("remote: Repository not found.")[0] == "not_found_or_private"
    assert _classify("fatal: could not read Username for 'https://github.com'")[0] == "not_found_or_private"
    assert _classify("fatal: Could not resolve host: github.com")[0] == "network"


# ── shallow fetches ───────────────────────────────────────────────────────────

def _three_commits(tmp_path):
    repo = make_repo(tmp_path / "r", {"a.txt": "1"})
    for i in (2, 3):
        (repo / "a.txt").write_text(str(i))
        git(repo, "commit", "-qam", f"c{i}")
    return repo


def test_shallow_fetches_only_tip_and_full_fetches_history(tmp_path):
    from markbook.gitinfo import read_history
    repo = _three_commits(tmp_path)
    url = f"file://{repo}"
    shallow = checkout(url, tmp_path / "s", allow_local=True, shallow=True)
    assert shallow.shallow and read_history(shallow.path, shallow=True).count == 1
    full = checkout(url, tmp_path / "f", allow_local=True, shallow=False)
    assert not full.shallow and read_history(full.path).count == 3


def test_shallow_with_ref_fetches_that_commit(tmp_path):
    repo = _three_commits(tmp_path)
    first = git(repo, "rev-list", "--max-parents=0", "HEAD")
    co = checkout(f"file://{repo}", tmp_path / "d", ref=first, allow_local=True, shallow=True)
    assert co.commit == first and (co.path / "a.txt").read_text() == "1"


def test_unknown_ref_gives_a_clear_message_in_both_modes(tmp_path):
    repo = _three_commits(tmp_path)
    for shallow in (True, False):
        with pytest.raises(FetchError) as e:
            checkout(f"file://{repo}", tmp_path / f"d{shallow}", ref="no-such-branch", allow_local=True, shallow=shallow)
        assert e.value.code == "ref_not_found"
        assert "--detach" not in e.value.message and "path argument" not in e.value.message


def test_file_urls_and_paths_refused_in_web_mode(tmp_path):
    """The web passes allow_local=False: neither plain paths nor file:// may reach git."""
    repo = make_repo(tmp_path / "r", {"a": "1"})
    for loc in (str(repo), f"file://{repo}"):
        with pytest.raises(FetchError) as e:
            checkout(loc, tmp_path / "d", allow_local=False)
        assert e.value.code == "invalid_url"
