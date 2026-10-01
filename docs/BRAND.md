# Markbook brand

A markbook is the teacher's record of marks. The mark is a **bookmark that has been marked**: a ribbon bookmark (the *book*), a highlighter swipe and a tick (the *mark*).

<p align="center"><img alt="The animated Markbook mark" src="assets/brand/mark-animated.svg" width="96"></p>

## The mark

| File | Use |
|---|---|
| [`assets/brand/mark-bare.svg`](assets/brand/mark-bare.svg) | In-product and on any background (header, empty states). **Source of truth**: the web UI's mark is tested to match it. |
| [`assets/brand/mark.svg`](assets/brand/mark.svg) | Inside an ink tile: app icon, avatars. |
| [`assets/brand/favicon.svg`](assets/brand/favicon.svg) | Small sizes (16 to 32 px): bolder, no highlighter, so the tick survives. |
| [`assets/brand/mark-animated.svg`](assets/brand/mark-animated.svg) | README and docs. Loops with a long pause, and is static for reduced motion. |
| `assets/brand/icon-192.png`, `icon-512.png` | Raster app icons. |
| `assets/brand/social-preview.png` | GitHub social preview (1280 x 640). |
| `assets/brand/banner-light.png`, `banner-dark.png` | README banner. |

Clear space: at least the width of the tick around the mark. Don't recolour it, rotate it (apart from the entrance animation), or put the tick on a light background.

## Colour

| Name | Hex | Role |
|---|---|---|
| Ink | `#10223A` | Text, the tile, the tick |
| Marker green | `#19B37D` | The bookmark |
| Highlighter | `#FFD43B` | The swipe; selection and emphasis, used sparingly |
| Paper | `#FBFAF6` | Light background |

The UI derives its themes from these (`web/static/markbook.css`). Text meets WCAG AA (4.5:1) and chart colours meet 3:1 in both themes; `tests/test_brand.py` fails if a token change breaks that. The logo green is **brand identity**, not a chart colour: charts use `--viz-*` tokens that are deep enough to be read.

## Type

**Fraunces** (variable, SIL Open Font License 1.1), bundled at `web/static/fonts/` with its licence, for the wordmark and headings: heavy *Mark*, regular *book*. The interface itself uses the system sans, so it feels native and stays fast. No webfont CDN: the UI makes no external requests.

## Motion

Motion is decoration and always optional.

- One intro animation, on the home page, once per browser session: the bookmark drops, the highlighter swipes, the tick draws, the wordmark rises (about 2.6 s). Any click or key skips it.
- Disabled entirely for `prefers-reduced-motion`; `?splash=0` turns it off and `?splash=1` forces it (useful for demos and screenshots).
- Micro-interactions (the tick redraws when you hover the logo, cards lift) are CSS-only and off for reduced motion.

## Voice

Plain, specific, and honest about limits: "Grade repositories. Review only what matters." Say what a number *is* ("a modelled workload figure, not a measured time saving"), not what it sounds like.

## Rebuilding the assets

```sh
sh docs/assets/brand/src/build.sh     # social preview and banners from their HTML sources (needs Chrome/Chromium)
```

The product screenshots in `docs/assets/screenshots/` are real captures of the web UI (`?theme=light|dark&splash=0`), taken from `markbook demo` data.
