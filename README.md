# kdp-spread-checker-cli

A local, stdlib-only command line tool that checks a **finished KDP paperback
interior PDF** before you spend an upload cycle - page count and parity,
blank-page placement, margins and bleed by trim size, duplicate or missing
front/back matter, and image (DPI/stretch) geometry - and prints a friendly
fix list ranked by severity.

It complements **kdp-cover-bleed-checker-cli** (the cover side). One tool
looks at the inside of the book, the other at the outside.

Built by **Asdale Dynamics, LLC** and maintained as part of
[Dylan Asdale's](https://dylanasdale.github.io) series of tools that explain
and automate AI-era work "like a friend would": plain English, step by step,
never condescending. Everything runs locally. No accounts, no uploads, no
telemetry - your manuscript never leaves your machine.

## Why

KDP validates interiors in its own way and quietly "fixes" some of them (like
padding odd page counts with a blank page). Getting rejected - or printed with
text swallowed by the gutter - after an upload is annoying. This catches the
most common interior rejections in seconds, with a readable, ranked fix list.

## Quick start

Requires Python 3.10+. No third-party packages.

```bash
# on the book you are about to upload (no bleed, 6x9):
python -m kdp_spread_checker my-interior.pdf

# if your pages export at trim + 0.25 in (bleed):
python -m kdp_spread_checker my-interior.pdf --trim 6x9 --bleed yes

# see every standard KDP paperback trim size:
python -m kdp_spread_checker --list-trims

# machine-readable output for scripts and CI:
python -m kdp_spread_checker my-interior.pdf --json
```

Or install it as a command:

```bash
pip install -e .
kdp-spread-checker my-interior.pdf --trim 5.5x8.5
```

## What it checks

- **Page count and parity**: 24-page minimum, 828-page cap, and whether an odd
  page count will force KDP to append a blank page.
- **Blank-page placement**: notes blank pages, warns when a filler blank sits
  on a right-hand page right before content (sections usually open on the
  right), and flags runs of three-or-more blanks that usually mean a vanished
  chapter.
- **Page size and bleed**: every page must match the chosen trim exactly (no
  bleed) or trim + 0.25 in in both directions (with bleed); mixed sizes are a
  blocker. Detectors note ink that *looks* designed for bleeding.
- **Margins by trim size**: the KDP gutter (spine) margin is enforced by the
  page count (0.375 in up to 150 pages, then 0.5/0.625/0.75/0.875 in), text
  must stay out of the gutter, and outside/top/bottom margins are checked
  against 0.25 in (no bleed) or 0.375 in (with bleed).
- **Front matter**: title page on page 1, exactly one copyright-style page,
  duplicate title blocks, whole pages containing identical text (the classic
  double-export), and a table of contents whose page numbers still fit the
  file (chapters get deleted after the TOC is written; this catches it).
- **Back matter**: detects About the Author / acknowledgments / index in the
  last quarter, and politely mentions when none is found.
- **Image geometry**: effective DPI at the placed size (300 DPI target,
  under 150 is a blocker), aspect-stretch detection, and a spread-scan
  heuristic for the "a two-page spread was exported as one page" mistake.

Report sections are ranked: `UPLOAD BLOCKERS`, then `WARNINGS`, then `NOTES`,
each with a concrete fix suggestion.

## Exit codes

| Code | Meaning                                      |
|------|----------------------------------------------|
| 0    | clean, or notes/warnings only                 |
| 1    | at least one upload blocker found             |
| 2    | the file could not be read (not a PDF, encrypted, missing) |

## Honest limitations

The checker reads the PDF directly with the standard library. Content stream
geometry (text placement, images, paths) is measured directly; glyph widths
are approximated at about half an em per character, so margin decisions are
trustworthy, but it is a heuristic meter, not a typesetting engine. Text
extraction is best-effort: PDFs whose fonts re-encode glyphs with CMaps or
subset fonts may yield little extractable text, in which case the
front/back-matter checks stay quiet rather than nagging false alarms. Color
profiles (CMYK conversion) are out of scope.

## Development

```bash
# run the test suite (pytest; the tool itself has no dependencies)
python -m pytest tests -q
```

The tests craft genuinely good and genuinely bad PDF interiors with a tiny
in-repo writer (`kdp_spread_checker/minipdf.py`), so nothing in the toolchain
relies on third-party libraries and every rule is exercised by a real PDF.

Bugs, ideas, and kind words: this is an [Asdale Dynamics, LLC](https://dylanasdale.github.io)
open-source tool, MIT licensed (see `LICENSE`).
