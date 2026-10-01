#!/bin/sh
# Rebuild the raster brand assets from their HTML sources (needs google-chrome or chromium).
# usage: sh docs/assets/brand/src/build.sh
set -e
cd "$(dirname "$0")"
CH=$(command -v google-chrome || command -v chromium || command -v chromium-browser)
shot() { "$CH" --headless=new --no-sandbox --disable-gpu --hide-scrollbars --virtual-time-budget=1500 --window-size="$2" --screenshot="../$3" "file://$PWD/$1" >/dev/null 2>&1; }
shot social-preview.html 1280,640 social-preview.png
shot banner-light.html   1200,300 banner-light.png
shot banner-dark.html    1200,300 banner-dark.png
echo "built: social-preview.png banner-light.png banner-dark.png"
