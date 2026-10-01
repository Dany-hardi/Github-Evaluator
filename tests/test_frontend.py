"""Drives the real markbook.js / boot.js against the real server-rendered pages in jsdom: sorting, filtering,
copy buttons, theme toggle, the intro splash (once, skippable), reviewer stamping, delete confirmation and the
drop zone. Skipped when Node or jsdom is missing (CI installs jsdom; locally: `npm i jsdom` and set NODE_PATH)."""
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

from markbook import cli
from markbook.web.app import create_app

NODE = shutil.which("node")
SCRIPT = Path(__file__).parent / "frontend" / "drive.js"


def _has_jsdom() -> bool:
    if not NODE:
        return False
    r = subprocess.run([NODE, "-e", "require.resolve('jsdom')"], capture_output=True, env=os.environ)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _has_jsdom(), reason="needs node and jsdom (set NODE_PATH to its node_modules)")


def test_every_interaction_works_in_a_browser_like_environment(tmp_path, demo_run):
    runs = tmp_path / "runs"
    cli.main(["grade", str(demo_run["spec_path"]), "--roster", str(demo_run["roster_path"]), "--sandbox", "none",
              "--out", str(runs / "r1"), "--quiet"])
    app = create_app(runs, runtime="none")
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = {**os.environ, "BASE": f"http://127.0.0.1:{server.server_port}", "RUN": "r1"}
        r = subprocess.run([NODE, str(SCRIPT)], capture_output=True, text=True, env=env, timeout=120)
    finally:
        server.shutdown()
    assert r.returncode == 0, r.stdout[-2500:] + r.stderr[-1500:]
    assert "FAIL" not in r.stdout and r.stdout.count("PASS") >= 20, r.stdout
