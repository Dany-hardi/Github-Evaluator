"""Roster: who to grade and where their repository is."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

_ALIASES = {
    "repo": ("repo", "repository", "url", "github", "github_url", "repo_url"),
    "id": ("id", "student_id", "login", "username", "sis_login_id", "identifier", "matricule"),
    "name": ("name", "full_name", "student", "student_name"),
    "email": ("email", "email_address", "mail"),
    "ref": ("ref", "commit", "branch", "sha"),
}


class RosterError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    email: str
    repo: str
    ref: str | None = None


def safe_id(raw: str) -> str:
    """Identifier that is safe as a filename and in URLs."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw.strip()).strip("._") or "student"


def _derive_id(repo: str) -> str:
    tail = repo.rstrip("/").removesuffix(".git").split("/")
    return safe_id("_".join(tail[-2:]) if len(tail) >= 2 else tail[-1])


def load_roster(path: str | Path) -> list[Entry]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise RosterError(f"cannot read roster: {exc}") from exc
    return parse_roster(text)


def parse_roster(text: str) -> list[Entry]:
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise RosterError("roster is empty")
    cols = {h.strip().lower().replace(" ", "_"): h for h in reader.fieldnames if h}

    def pick(row: dict, key: str) -> str:
        for alias in _ALIASES[key]:
            if alias in cols and (row.get(cols[alias]) or "").strip():
                return row[cols[alias]].strip()
        return ""

    if not any(a in cols for a in _ALIASES["repo"]):
        raise RosterError(f"roster needs a repository column (one of: {', '.join(_ALIASES['repo'])}); "
                          f"found: {', '.join(reader.fieldnames)}")
    entries, seen = [], set()
    for n, row in enumerate(reader, start=2):
        repo = pick(row, "repo")
        if not repo:
            continue  # tolerate blank trailing rows
        sid = safe_id(pick(row, "id")) if pick(row, "id") else _derive_id(repo)
        if sid in seen:
            raise RosterError(f"line {n}: duplicate student id {sid!r}")
        seen.add(sid)
        entries.append(Entry(sid, pick(row, "name") or sid, pick(row, "email"), repo, pick(row, "ref") or None))
    if not entries:
        raise RosterError("roster has no rows with a repository")
    return entries
