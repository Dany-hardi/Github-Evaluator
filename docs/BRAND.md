# Markbook brand

A markbook is the teacher's record of marks, and marks are written by hand. The logo is a **signature that ends in a tick**: *Markbook* in a cursive hand, finished by the check mark a teacher makes when something is right. Nothing else: no icon beside it, no ribbon, no highlighter.

<p align="center"><img alt="The animated Markbook wordmark" src="assets/brand/wordmark-animated.svg" width="360"></p>

## The logo

| File | Use |
|---|---|
| [`assets/brand/wordmark.svg`](assets/brand/wordmark.svg), [`wordmark-on-dark.svg`](assets/brand/wordmark-on-dark.svg) | The primary logo: header, README, documents. Ink on light, paper on dark; the tick is always marker green. |
| [`assets/brand/wordmark-animated.svg`](assets/brand/wordmark-animated.svg), [`-on-dark`](assets/brand/wordmark-animated-on-dark.svg) | README and docs. The word is written on, then the tick is drawn; loops with a long pause, and is static for reduced motion. |
| [`assets/brand/mark-bare.svg`](assets/brand/mark-bare.svg) | The short form, an **M with the tick**, for empty states and places too small for the full word. |
| [`assets/brand/mark.svg`](assets/brand/mark.svg) | The short form in an ink tile: app icon, avatars. |
| [`assets/brand/favicon.svg`](assets/brand/favicon.svg) | Small sizes (16 to 32 px): the same tile with a heavier M and tick so both survive. |
| `assets/brand/icon-192.png`, `icon-512.png`, `web/static/apple-touch-icon.png` | Raster app icons. |
| `assets/brand/social-preview.png` | GitHub social preview (1280 x 640). |
| `assets/brand/banner-light.png`, `banner-dark.png` | Static banners (1200 x 300). |

The web UI inlines the same paths; `tests/test_brand.py` fails if the template and these files drift apart.

Clear space: at least the height of the tick around the logo. Don't recolour it, outline it, stretch it, or set it in another typeface; don't put the green tick on a green background.

**How it is built.** The letters are the outlines of Dancing Script Bold (SIL Open Font License, see `assets/brand/src/DancingScript-OFL.txt`), converted to paths so no font is needed to display the logo anywhere, GitHub included. `python docs/assets/brand/src/build_logo.py` regenerates every SVG and the UI macros from the font; `sh docs/assets/brand/src/build.sh` renders the PNGs. To change the logo, change that script, not the files.

## Colour

| Name | Hex | Role |
|---|---|---|
| Ink | `#10223A` | Text, the tile, the tick |
| Marker green | `#19B37D` | The tick |
| Highlighter | `#FFD43B` | Selection and emphasis in the UI, used sparingly (not part of the logo) |
| Paper | `#FBFAF6` | Light background |

The UI derives its themes from these (`web/static/markbook.css`). Text meets WCAG AA (4.5:1) and chart colours meet 3:1 in both themes; `tests/test_brand.py` fails if a token change breaks that. The logo green is **brand identity**, not a chart colour: charts use `--viz-*` tokens that are deep enough to be read.

## Type

**Dancing Script** for the logo only (as outlines). **Fraunces** (variable, SIL Open Font License 1.1), bundled at `web/static/fonts/` with its licence, for page headings. The interface itself uses the system sans, so it feels native and stays fast. No webfont CDN: the UI makes no external requests.

## Motion

Motion is decoration and always optional.

- One intro animation, on the home page, once per browser session: the word *Markbook* is written from left to right on a slanted edge (about 1.4 s), the tick is drawn, and the tagline rises (about 2.9 s in all). Any click or key skips it.
- Disabled entirely for `prefers-reduced-motion`; `?splash=0` turns it off and `?splash=1` forces it (useful for demos and screenshots).
- Micro-interactions (the tick redraws when you hover the logo, cards lift) are CSS-only and off for reduced motion.

## Voice

Plain, specific, and honest about limits: "Grade repositories. Review only what matters." Say what a number *is* ("a modelled workload figure, not a measured time saving"), not what it sounds like.

## Rebuilding the assets

```sh
sh docs/assets/brand/src/build.sh     # social preview and banners from their HTML sources (needs Chrome/Chromium)
```

The product screenshots in `docs/assets/screenshots/` are real captures of the web UI (`?theme=light|dark&splash=0`), taken from `markbook demo` data.
