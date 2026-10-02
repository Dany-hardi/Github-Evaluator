#!/bin/sh
# Rebuild the raster brand assets from their HTML sources (run build_logo.py first if the logo itself changed) (needs google-chrome or chromium).
# usage: sh docs/assets/brand/src/build.sh
set -e
cd "$(dirname "$0")"
CH=$(command -v google-chrome || command -v chromium || command -v chromium-browser)
shot() { "$CH" --headless=new --no-sandbox --disable-gpu --hide-scrollbars --virtual-time-budget=1500 --window-size="$2" --screenshot="../$3" "file://$PWD/$1" >/dev/null 2>&1; }
shot social-preview.html 1280,640 social-preview.png
shot banner-light.html   1200,300 banner-light.png
shot banner-dark.html    1200,300 banner-dark.png
shot_icon() { "$CH" --headless=new --no-sandbox --disable-gpu --hide-scrollbars --default-background-color=00000000 --virtual-time-budget=1500 --window-size="$1,$1" --screenshot="$2" "file://$PWD/icon.html" >/dev/null 2>&1; }
shot_icon 192 ../icon-192.png
shot_icon 512 ../icon-512.png
shot_icon 180 ../../../../markbook/web/static/apple-touch-icon.png
echo "built: social-preview, banners, app icons"
