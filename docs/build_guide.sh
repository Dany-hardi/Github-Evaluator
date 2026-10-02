#!/bin/sh
# Build the complete guide as HTML and PDF.   usage: sh docs/build_guide.sh
# Needs pandoc; the PDF step needs Chrome or Chromium (it prints the HTML, so tables and emoji render properly).
set -e
cd "$(dirname "$0")"
# The Markdown has its own contents list and H1 (for GitHub); the built copy uses pandoc's title block and table of contents.
python3 - <<'PY'
import re
s = open("MARKBOOK_COMPLETE_GUIDE.md", encoding="utf-8").read()
s = re.sub(r"^# Markbook: The Complete Documentation and Design Guide\n", "", s, count=1, flags=re.M)
s = re.sub(r"^# Contents\n.*?(?=^# Part 1\.)", "", s, count=1, flags=re.M | re.S)
open("guide-build/_build.md", "w", encoding="utf-8").write(s)
PY
pandoc guide-build/_build.md -s --toc --toc-depth=2 --no-highlight --metadata pagetitle="Markbook: the complete guide" \
  -c guide-build/guide.css --embed-resources -o guide-build/MARKBOOK_COMPLETE_GUIDE.html
rm -f guide-build/_build.md
CH=$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)
if [ -n "$CH" ]; then
  "$CH" --headless=new --no-sandbox --disable-gpu --no-pdf-header-footer \
    --print-to-pdf=guide-build/MARKBOOK_COMPLETE_GUIDE.pdf "file://$PWD/guide-build/MARKBOOK_COMPLETE_GUIDE.html" >/dev/null 2>&1
  echo "built: guide-build/MARKBOOK_COMPLETE_GUIDE.html and .pdf"
else
  echo "built: guide-build/MARKBOOK_COMPLETE_GUIDE.html (no Chrome found: print it to PDF from a browser)"
fi
