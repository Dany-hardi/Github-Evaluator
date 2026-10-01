"""`markbook demo`: a hermetic cohort for trying the tool in under a minute.

Builds seven local git repositories that exercise every code path: a clean
submission, a copied pair (identifiers renamed), a logic bug, a late
submission, a build failure with a single-commit history dump, and an
unreachable repository. No network is needed.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SPEC_YAML = """\
# Rubric-as-code for the demo assignment. Validate with: markbook validate spec.yaml
name: "FizzBuzz CLI (demo)"
version: 1
scale: 20                 # grades are reported out of 20
pass_mark: 10
deadline: 2026-03-01T23:59:00Z
late_penalty: {percent_per_day: 10, max_percent: 50}

sandbox:
  image: python:3.12-slim # used by --sandbox docker
  timeout: 5
  memory: 128m

similarity: {threshold: 0.6}

criteria:
  - id: structure
    title: Program file present
    points: 2
    check: {type: file_exists, paths: [fizzbuzz.py]}

  - id: runs
    title: Program runs without crashing
    points: 2
    requires: [structure]
    check: {type: command, run: "python3 fizzbuzz.py 3", expect_exit: 0}

  - id: behaviour
    title: Produces correct FizzBuzz output
    points: 8
    requires: [structure]
    check:
      type: cases
      run: python3 fizzbuzz.py
      cases:
        - {name: "first five", args: "5", stdout: "1\\n2\\nFizz\\n4\\nBuzz"}
        - {name: "reaches FizzBuzz", args: "15", stdout: "1\\n2\\nFizz\\n4\\nBuzz\\nFizz\\n7\\n8\\nFizz\\nBuzz\\n11\\nFizz\\n13\\n14\\nFizzBuzz"}
        - {name: "zero is empty", args: "0", stdout: ""}
        - {name: "boundary", args: "30", hidden: true, stdout_regex: "FizzBuzz\\\\n16\\\\n17\\\\nFizz\\\\n19\\\\nBuzz\\\\nFizz\\\\n22\\\\n23\\\\nFizz\\\\nBuzz\\\\n26\\\\nFizz\\\\n28\\\\n29\\\\nFizzBuzz$"}

  - id: readme
    title: README explains installation and usage
    points: 3
    check: {type: readme, sections: [usage, install], min_words: 30}

  - id: history
    title: Incremental development in Git
    points: 2
    check: {type: git, min_commits: 3, min_active_days: 2, max_single_commit_share: 0.8}

  - id: quality
    title: Code design and readability
    points: 3
    check: {type: manual, guidance: "Naming, decomposition, error handling. 0-3."}
"""

ALICE = '''"""FizzBuzz command line tool."""
import sys


class FizzBuzz:
    """Maps integers to their FizzBuzz representation."""

    def __init__(self, fizz=3, buzz=5):
        self.fizz = fizz
        self.buzz = buzz

    def render(self, n):
        out = ""
        if n % self.fizz == 0:
            out += "Fizz"
        if n % self.buzz == 0:
            out += "Buzz"
        return out or str(n)

    def sequence(self, limit):
        return [self.render(i) for i in range(1, limit + 1)]


def main(argv):
    if len(argv) != 2 or not argv[1].isdigit():
        print("usage: fizzbuzz.py N", file=sys.stderr)
        return 2
    for line in FizzBuzz().sequence(int(argv[1])):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''

BOB = '''import sys


def is_multiple(value, divisor):
    return value % divisor == 0


def convert(value):
    if is_multiple(value, 15):
        return "FizzBuzz"
    elif is_multiple(value, 3):
        return "Fizz"
    elif is_multiple(value, 5):
        return "Buzz"
    else:
        return str(value)


def run(count):
    current = 1
    while current <= count:
        print(convert(current))
        current += 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("missing argument")
        sys.exit(1)
    try:
        run(int(sys.argv[1]))
    except ValueError:
        print("not a number")
        sys.exit(1)
'''

# Same program, identifiers renamed and comments added: what a lazy copy looks like.
CAROL = '''import sys

# check whether a number divides evenly
def divisible(num, d):
    return num % d == 0

# translate a number to its word
def translate(num):
    if divisible(num, 15):
        return "FizzBuzz"
    elif divisible(num, 3):
        return "Fizz"
    elif divisible(num, 5):
        return "Buzz"
    else:
        return str(num)

# loop over the range
def loop(limit):
    idx = 1
    while idx <= limit:
        print(translate(idx))
        idx += 1

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("missing argument")
        sys.exit(1)
    try:
        loop(int(sys.argv[1]))
    except ValueError:
        print("not a number")
        sys.exit(1)
'''

DAVE = '''import sys

RULES = {3: "Fizz", 5: "Buzz"}


def label(n):
    parts = [word for div, word in sorted(RULES.items()) if n % div == 0]
    return "".join(parts) if parts else str(n)


def main():
    n = int(sys.argv[1])
    lines = []
    for i in range(1, n):  # off by one: n itself is never printed
        lines.append(label(i))
    print("\\n".join(lines))


main()
'''

ERIN = '''import sys


def fb(n):
    return "FizzBuzz" if n % 15 == 0 else "Fizz" if n % 3 == 0 else "Buzz" if n % 5 == 0 else str(n)


if __name__ == "__main__":
    print(*map(fb, range(1, int(sys.argv[1]) + 1)), sep="\\n")
'''

FRANK = '''import sys
def fizzbuzz(n)
    for i in range(1, n + 1):
        print(i)
'''

GOOD_README = """# FizzBuzz

A tiny command line tool that prints the FizzBuzz sequence.

## Installation

No dependencies are needed beyond a Python 3 interpreter. Clone the repository and you are ready to go.

## Usage

Run `python3 fizzbuzz.py N` to print the sequence from 1 up to N inclusive.
"""

THIN_README = """# FizzBuzz

Prints FizzBuzz. Run it with python.
"""

D = "2026-02-{day:02d}T10:00:00+00:00"

# (id, name, files-per-commit [(date, message, {path: content})])
STUDENTS = [
    ("alice", "Alice Martin", [
        (D.format(day=20), "scaffold", {"fizzbuzz.py": "import sys\n\n\ndef main(argv):\n    return 0\n"}),
        (D.format(day=24), "implement FizzBuzz class", {"fizzbuzz.py": ALICE}),
        (D.format(day=27), "document usage", {"README.md": GOOD_README}),
    ]),
    ("bob", "Bob Okafor", [
        (D.format(day=21), "start", {"fizzbuzz.py": BOB.split("def run")[0]}),
        (D.format(day=25), "finish", {"fizzbuzz.py": BOB}),
        (D.format(day=26), "readme", {"README.md": GOOD_README}),
    ]),
    ("carol", "Carol Nguyen", [
        (D.format(day=26), "init", {"README.md": GOOD_README}),
        (D.format(day=27), "add code", {"fizzbuzz.py": CAROL}),
        (D.format(day=28), "tidy", {"fizzbuzz.py": CAROL + "\n"}),
    ]),
    ("dave", "Dave Smith", [
        (D.format(day=22), "wip", {"fizzbuzz.py": "import sys\n"}),
        (D.format(day=26), "solution", {"fizzbuzz.py": DAVE}),
        (D.format(day=26).replace("10:00", "18:00"), "readme", {"README.md": THIN_README}),
    ]),
    ("erin", "Erin Park", [
        (D.format(day=25), "solution", {"fizzbuzz.py": ERIN}),
        ("2026-03-02T09:00:00+00:00", "docs (late)", {"README.md": GOOD_README}),
        ("2026-03-03T09:00:00+00:00", "polish (late)", {"README.md": GOOD_README + "\nMIT licensed.\n"}),
    ]),
    ("frank", "Frank Dubois", [
        ("2026-02-28T23:00:00+00:00", "everything", {"fizzbuzz.py": FRANK}),
    ]),
]


def _git(repo: Path, *args: str, date: str | None = None) -> None:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Student", "-c", "user.email=student@example.edu",
                    "-c", "init.defaultBranch=main", *args],
                   check=True, capture_output=True, env=env)


def build_demo(dest: Path) -> tuple[Path, Path]:
    """Create the demo cohort under `dest`; return (spec_path, roster_path)."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "spec.yaml").write_text(SPEC_YAML, encoding="utf-8")
    rows = ["id,name,email,repo"]
    for sid, name, commits in STUDENTS:
        repo = dest / "repos" / sid
        repo.mkdir(parents=True, exist_ok=True)
        _git(repo, "init", "-q")
        for date, msg, files in commits:
            for rel, content in files.items():
                (repo / rel).write_text(content, encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", msg, date=date)
        rows.append(f"{sid},{name},{sid}@example.edu,{repo}")
    rows.append("ghost,Grace Host,ghost@example.edu,/nonexistent/ghost")
    (dest / "roster.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return dest / "spec.yaml", dest / "roster.csv"
