"""Brand and front-end guarantees: these pin what was verified by eye (contrast, one source of truth for the
mark, the boot script's real behaviour, CSP and accessibility) so a later tweak cannot silently break them."""
import json
import re
import shutil
import subprocess
import sys
import xml.dom.minidom
from html.parser import HTMLParser
from pathlib import Path

import pytest

from markbook import cli
from markbook.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "markbook"
STATIC = PKG / "web" / "static"
TEMPLATES = PKG / "web" / "templates"
BRAND = ROOT / "docs" / "assets" / "brand"
PALETTE = {"#10223A", "#19B37D", "#FFD43B", "#FFFFFF", "#FBFAF6"}      # ink, marker green, highlighter, white, paper


@pytest.fixture
def client(tmp_path, demo_run):
    runs = tmp_path / "runs"
    cli.main(["grade", str(demo_run["spec_path"]), "--roster", str(demo_run["roster_path"]),
              "--sandbox", "none", "--out", str(runs / "r1"), "--quiet"])
    app = create_app(runs, runtime="none")
    app.config["TESTING"] = True
    return app.test_client()


PAGES = ["/", "/runs/r1", "/runs/r1/s/bob", "/runs/r1/s/ghost", "/runs/new", "/nope"]


class Doc(HTMLParser):
    """Just enough structure to assert on: tags in order, with their attributes."""

    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def count(self, tag):
        return sum(1 for t, _ in self.tags if t == tag)


def parse(html):
    d = Doc()
    d.feed(html)
    return d


# ── the product says Markbook, everywhere ─────────────────────────────────────

def test_the_old_name_survives_nowhere_in_the_product():
    hits = []
    for p in PKG.rglob("*"):
        if p.is_file() and p.suffix in {".py", ".html", ".js", ".css", ".json", ".svg", ".md"}:
            text = p.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"github[ -]evaluator", text, re.IGNORECASE):
                hits.append(str(p.relative_to(ROOT)))
    assert hits == []


@pytest.mark.parametrize("url", PAGES)
def test_every_page_is_branded_and_structured(client, url):
    html = client.get(url).get_data(as_text=True)
    d = parse(html)
    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
    assert "Markbook" in title
    assert d.tags[0] == ("html", {"lang": "en"}) or any(t == "html" and a.get("lang") == "en" for t, a in d.tags)
    assert d.count("h1") == 1, "exactly one h1 per page"
    assert any(t == "main" and a.get("id") == "main" for t, a in d.tags)
    assert any(t == "a" and a.get("class") == "skip" for t, a in d.tags), "skip link"
    assert any(t == "meta" and a.get("name") == "description" for t, a in d.tags)
    assert any(t == "link" and a.get("rel") == "icon" for t, a in d.tags)


@pytest.mark.parametrize("url", PAGES)
def test_boot_script_blocks_before_the_stylesheet_and_the_app_script_is_deferred(client, url):
    d = parse(client.get(url).get_data(as_text=True))
    order = [(t, a) for t, a in d.tags if (t == "script") or (t == "link" and a.get("rel") == "stylesheet")]
    boot = next(i for i, (t, a) in enumerate(order) if t == "script" and "boot.js" in a.get("src", ""))
    css = next(i for i, (t, a) in enumerate(order) if t == "link")
    assert boot < css, "theme/splash are decided before first paint"
    assert "defer" not in order[boot][1] and "async" not in order[boot][1]
    app = next(a for t, a in order if t == "script" and "markbook.js" in a.get("src", ""))
    assert "defer" in app


@pytest.mark.parametrize("url", PAGES)
def test_icons_and_svgs_are_accessible(client, url):
    d = parse(client.get(url).get_data(as_text=True))
    for tag, a in d.tags:
        if tag == "svg":
            assert a.get("aria-hidden") == "true" or (a.get("role") == "img" and a.get("aria-label")), a
    html = client.get(url).get_data(as_text=True)
    for m in re.finditer(r"<button\b([^>]*)>(.*?)</button>", html, re.S):
        attrs, inner = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        assert inner or "aria-label=" in attrs, f"unlabelled button: {attrs}"
    assert 'aria-labelledby="help-title"' in html


def test_static_assets_are_served(client):
    for path, magic in [("favicon.svg", b"<svg"), ("apple-touch-icon.png", b"\x89PNG"), ("boot.js", b"markbook.theme"),
                        ("markbook.js", b"use strict"), ("markbook.css", b"Fraunces"),
                        ("fonts/Fraunces-Variable-latin.woff2", b"wOF2"), ("fonts/OFL.txt", b"SIL OPEN FONT LICENSE")]:
        r = client.get(f"/static/{path}")
        assert r.status_code == 200 and magic in r.data[:4096].upper() if path.endswith("OFL.txt") else magic in r.data[:4096], path


def test_csp_lets_the_bundled_font_load_from_self_and_nothing_else_loosens(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "font-src 'self'" in csp and "default-src 'none'" in csp and "'unsafe-inline'" not in csp
    assert "script-src 'self'" in csp and "style-src 'self'" in csp


@pytest.mark.parametrize("url", ["/", "/runs/r1", "/runs/r1/s/bob", "/runs/new"])
def test_no_inline_style_or_script_anywhere(client, url):
    html = client.get(url).get_data(as_text=True)
    assert " style=" not in html and "<script>" not in html and "onclick=" not in html


# ── single source of truth for the mark ───────────────────────────────────────

def _paths(svg_text):
    return re.findall(r'<path\b[^>]*?\bd="([^"]+)"', svg_text)


def test_the_header_mark_is_the_published_logo():
    macro = (TEMPLATES / "_ui.html").read_text()
    in_template = _paths(macro.split("{% macro mark")[1].split("{%- endmacro")[0])
    in_brand = _paths((BRAND / "mark-bare.svg").read_text())
    assert in_template == in_brand and len(in_brand) == 2, "template mark drifted from docs/assets/brand/mark-bare.svg"


def test_the_header_wordmark_is_the_published_wordmark():
    macro = (TEMPLATES / "_ui.html").read_text()
    in_template = _paths(macro.split("{% macro wordmark")[1].split("{%- endmacro")[0])
    for name in ("wordmark.svg", "wordmark-on-dark.svg", "wordmark-animated.svg", "wordmark-animated-on-dark.svg"):
        assert in_template == _paths((BRAND / name).read_text()) and len(in_template) == 2, name


def test_the_signature_is_written_then_ticked_in_the_splash_and_only_there():
    word = re.search(r"\.splash \.wm \.word \{([^}]*)\}", CSS).group(1)
    tick = re.search(r"\.splash \.wm \.tick \{([^}]*)\}", CSS).group(1)
    assert "animation: write" in word and "clip-path" in word, "the word starts hidden and is written on"
    d_word = float(re.search(r"animation: write ([\d.]+)s[^;]*? ([\d.]+)s forwards", word).group(2)) + float(re.search(r"write ([\d.]+)s", word).group(1))
    d_tick = float(re.search(r"draw [\d.]+s ease ([\d.]+)s", tick).group(1))
    assert d_tick >= d_word - .1, "the tick is drawn after the word is finished"
    assert "stroke-dashoffset: 1" in tick
    assert re.search(r"@keyframes write \{[^}]*clip-path[^}]*\}[^}]*clip-path", CSS)
    assert ".wm .word { fill: currentColor; }" in CSS, "outside the splash the word is plainly visible"


@pytest.mark.parametrize("path", sorted(BRAND.glob("*.svg")) + [STATIC / "favicon.svg"], ids=lambda p: p.name)
def test_brand_svgs_are_valid_xml_and_on_palette(path):
    doc = xml.dom.minidom.parse(str(path))
    assert doc.documentElement.tagName == "svg" and doc.documentElement.getAttribute("viewBox")
    colours = set()
    for el in doc.getElementsByTagName("*"):
        for attr in ("fill", "stroke"):
            v = el.getAttribute(attr)
            if v and v not in ("none", "currentColor"):
                colours.add(v.upper())
    assert colours <= PALETTE, f"off-palette colours in {path.name}: {colours - PALETTE}"


def test_png_icons_have_the_advertised_sizes():
    import struct
    for name, size in [("icon-192.png", 192), ("icon-512.png", 512)]:
        data = (BRAND / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", data[16:24]) == (size, size)
    data = (STATIC / "apple-touch-icon.png").read_bytes()
    assert struct.unpack(">II", data[16:24]) == (180, 180)


def test_font_ships_with_its_licence_and_in_the_package():
    assert (STATIC / "fonts" / "Fraunces-Variable-latin.woff2").stat().st_size > 10_000
    assert "SIL OPEN FONT LICENSE" in (STATIC / "fonts" / "OFL.txt").read_text().upper()
    assert "web/static/fonts/*" in (ROOT / "pyproject.toml").read_text()


# ── colour: contrast is a tested property, not a hope ─────────────────────────

CSS = (STATIC / "markbook.css").read_text()


def _tokens(selector):
    m = re.search(r"^" + selector + r"\s*\{(.*?)^\}", CSS, re.S | re.M)
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", m.group(1)))


def _lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in c]
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]


def contrast(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + .05) / (lo + .05)


LIGHT = _tokens(r":root")
DARK = {**LIGHT, **_tokens(r':root\[data-theme="dark"\]')}
TEXT_PAIRS = [("ink", "paper"), ("ink", "surface"), ("ink", "surface-2"), ("muted", "paper"), ("muted", "surface"),
              ("muted", "surface-2"), ("brand", "paper"), ("brand", "surface"), ("on-brand", "brand"),
              ("brand", "brand-soft"), ("ok", "ok-soft"), ("warn", "warn-soft"), ("bad", "bad-soft"),
              ("info", "info-soft"), ("bad", "surface"), ("warn", "surface")]
GRAPHIC = ["viz-ok", "viz-warn", "viz-bad"]


@pytest.mark.parametrize("theme,tokens", [("light", LIGHT), ("dark", DARK)])
def test_text_contrast_meets_wcag_aa(theme, tokens):
    bad = [(a, b, round(contrast(tokens[a], tokens[b]), 2)) for a, b in TEXT_PAIRS if contrast(tokens[a], tokens[b]) < 4.5]
    assert bad == [], f"{theme}: below 4.5:1 {bad}"


@pytest.mark.parametrize("theme,tokens", [("light", LIGHT), ("dark", DARK)])
def test_chart_colours_meet_non_text_contrast(theme, tokens):
    bad = [(g, bg, round(contrast(tokens[g], tokens[bg]), 2)) for g in GRAPHIC for bg in ("paper", "surface")
           if contrast(tokens[g], tokens[bg]) < 3.0]
    assert bad == [], f"{theme}: chart colours below 3:1 {bad}"


def test_the_two_dark_theme_blocks_cannot_drift_apart():
    """Dark exists twice (explicit toggle, and the OS preference when no toggle was chosen)."""
    m = re.search(r'@media \(prefers-color-scheme: dark\) \{\s*:root:not\(\[data-theme="light"\]\) \{(.*?)^  \}', CSS, re.S | re.M)
    auto = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", m.group(1)))
    explicit = _tokens(r':root\[data-theme="dark"\]')
    assert auto == explicit


def test_reduced_motion_removes_the_splash_and_the_motion():
    block = CSS.split("@media (prefers-reduced-motion: reduce)")[1].split("@media print")[0]
    assert ".splash { display: none !important; }" in block and "animation-duration: .001ms !important" in block


# ── boot.js, run for real under Node with a fake browser ──────────────────────

NODE = shutil.which("node")
HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const cfg = JSON.parse(process.argv[3]);
const classes = new Set(), attrs = {};
const store = (seed, throws) => ({ d: {...seed}, getItem(k) { if (throws) throw new Error("blocked"); return k in this.d ? this.d[k] : null; },
                                   setItem(k, v) { if (throws) throw new Error("blocked"); this.d[k] = String(v); } });
const local = store(cfg.local || {}, cfg.blocked), session = store(cfg.session || {}, cfg.blocked);
const sandbox = {
  document: { documentElement: { classList: { add: c => classes.add(c) }, setAttribute: (k, v) => { attrs[k] = v; } } },
  location: { pathname: cfg.path, search: cfg.search || "" },
  localStorage: local, sessionStorage: session, URLSearchParams,
  window: {}, matchMedia: () => ({ matches: !!cfg.reduce }),
};
sandbox.window.matchMedia = sandbox.matchMedia;
new Function(...Object.keys(sandbox), src)(...Object.values(sandbox));
console.log(JSON.stringify({ splash: classes.has("splash-on"), theme: attrs["data-theme"] || null, stored: local.d["markbook.theme"] || null }));
"""


def boot(**cfg):
    out = subprocess.run([NODE, "-e", HARNESS, "-", str(STATIC / "boot.js"), json.dumps(cfg)], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(not NODE, reason="node is not installed")
class TestBootScript:
    def test_first_visit_to_home_plays_the_intro(self):
        assert boot(path="/")["splash"] is True

    def test_only_once_per_session(self):
        assert boot(path="/", session={"markbook.splash": "1"})["splash"] is False

    def test_only_on_the_home_page(self):
        assert boot(path="/runs/abc")["splash"] is False and boot(path="/runs/new")["splash"] is False

    def test_reduced_motion_never_plays_it(self):
        assert boot(path="/", reduce=True)["splash"] is False
        assert boot(path="/", reduce=True, search="?splash=1")["splash"] is False

    def test_query_overrides_for_demos_and_screenshots(self):
        assert boot(path="/runs/x", search="?splash=1")["splash"] is True
        assert boot(path="/", search="?splash=0")["splash"] is False

    def test_theme_is_restored_before_paint(self):
        r = boot(path="/runs/x", local={"markbook.theme": "dark"})
        assert r["theme"] == "dark"

    def test_theme_query_is_applied_and_remembered_but_junk_is_ignored(self):
        r = boot(path="/runs/x", search="?theme=light")
        assert r["theme"] == "light" and r["stored"] == "light"
        assert boot(path="/runs/x", search="?theme=<script>")["theme"] is None
        assert boot(path="/runs/x", local={"markbook.theme": "hotpink"})["theme"] is None

    def test_blocked_storage_never_breaks_the_page(self):
        assert boot(path="/", blocked=True) == {"splash": False, "theme": None, "stored": None}


# ── the terminal gets the brand too ───────────────────────────────────────────

def _run(args, env_extra=None):
    import os
    env = {**os.environ, **(env_extra or {})}
    r = subprocess.run([sys.executable, "-m", "markbook", *args], capture_output=True, text=True, env=env, timeout=30)
    strip = lambda t: re.sub(r"\x1b\[[0-9;]*m", "", t)      # argparse colours help when FORCE_COLOR is set
    r.stdout, r.stderr = strip(r.stdout), strip(r.stderr)
    return r


def test_bare_command_prints_the_mark_and_help_and_succeeds():
    r = _run([])
    assert r.returncode == 0 and "Markbook" in r.stdout and "markbook demo" in r.stdout and "usage: markbook" in r.stdout
    assert "Markbook ✔" in r.stdout or "Markbook [v]" in r.stdout


def test_help_and_banner_never_crash_on_an_ascii_terminal():
    for args in ([], ["--help"], ["demo", "--help"]):
        r = _run(args, {"PYTHONIOENCODING": "ascii", "NO_COLOR": "1"})
        assert r.returncode == 0, (args, r.stderr[-300:])
        assert "UnicodeEncodeError" not in r.stderr
    assert "[v]" in _run([], {"PYTHONIOENCODING": "ascii"}).stdout, "ascii-safe tick when the terminal can't draw it"
