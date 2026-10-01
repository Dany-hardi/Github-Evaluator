"""Commit-history facts, read from the clone *before* any student code runs
(a build step could otherwise rewrite .git to forge its own history)."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SEP, _FIELD = "\x1e", "\x1f"


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    when: datetime
    lines: int  # insertions + deletions


@dataclass
class History:
    commits: list[Commit]
    shallow: bool = False

    @property
    def count(self) -> int:
        return len(self.commits)

    @property
    def authors(self) -> set[str]:
        return {c.email.lower() or c.author.lower() for c in self.commits}

    @property
    def active_days(self) -> int:
        return len({c.when.date() for c in self.commits})

    @property
    def last_commit(self) -> datetime | None:
        return max((c.when for c in self.commits), default=None)

    @property
    def max_single_commit_share(self) -> float:
        total = sum(c.lines for c in self.commits)
        return max((c.lines for c in self.commits), default=0) / total if total else 0.0


def read_history(repo: Path, limit: int = 2000, shallow: bool = False) -> History:
    out = subprocess.run(
        ["git", "-C", str(repo), "log", f"--max-count={limit}", "--numstat",
         f"--format={_SEP}%H{_FIELD}%an{_FIELD}%ae{_FIELD}%ct"],
        capture_output=True, text=True, timeout=60, errors="replace",
    )
    commits: list[Commit] = []
    if out.returncode != 0:
        return History(commits, shallow)
    for block in out.stdout.split(_SEP)[1:]:
        head, _, stats = block.partition("\n")
        sha, author, email, ts = head.split(_FIELD)
        lines = 0
        for row in stats.splitlines():
            parts = row.split("\t")
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():  # '-' = binary
                lines += int(parts[0]) + int(parts[1])
        commits.append(Commit(sha, author, email, datetime.fromtimestamp(int(ts), timezone.utc), lines))
    return History(commits, shallow)
