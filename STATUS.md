# STATUS.md — kdp-spread-checker-cli

## What was built

`kdp-spread-checker-cli` is a stdlib-only Python CLI that checks a finished
KDP paperback **interior** PDF before upload and prints a friendly fix list
ranked by severity (BLOCKER / warning / note). It complements
`kdp-cover-bleed-checker-cli`, which handles the cover side.

### Checks implemented

- **Page count and parity**: KDP minimum 24, maximum 828; odd page counts warn
  because KDP silently appends a blank page.
- **Page size / bleed family**: every page must match the trim exactly (no
  bleed) or trim + 0.25 in both directions (with bleed); mixed page sizes are
  a blocker; unknown sizes point back at the export dialog. `--bleed`
  auto-detects the family from the file (or forces one).
- **Margins by trim and page count**: the KDP gutter table by page count
  (0.375/0.5/0.625/0.75/0.875 in), text-into-gutter blockers, image-into-gutter
  warnings, off-trim-text warnings, outside/top/bottom minimums 0.25 in
  (no bleed) or 0.375 in (with bleed), and an "ink touches the edge but the
  file has no bleed" nudge.
- **Blank-page placement**: inventory of blank pages, right-hand filler
  placement warnings, blank runs of 3+ (the classic deleted chapter), and a
  friendly note when the book ends on a classic blank verso.
- **Front/back matter**: title page on page 1, a copyright-style page within
  the first 12, duplicate title blocks, whole pages with identical text, a
  TOC whose entries fit within the file (TOC range errors), and back-matter
  detection (About the Author / acknowledgments / index / glossary / etc.)
  in the last quarter.
- **Image geometry**: effective DPI at the placed size (300 target; <150 is a
  blocker), aspect-stretch detection, and a spread-scan heuristic for
  two-page spreads exported as one page.

### Architecture (all stdlib)

- `kdp_spread_checker/pdfreader.py` — forgiving PDF reader: scans the file for
  indirect objects (works with broken/missing xref tables), zlib-decodes
  Flate streams, expands `/Type /ObjStm` object streams, walks the page tree
  in document order with MediaBox/Resources inheritance, and refuses
  encrypted files with a human-readable message.
- `kdp_spread_checker/content.py` — a simplified content-stream interpreter
  (operators `BT/ET/Tm/Td/TD/T*/Tf/Tj/TJ/'/"`, `q/Q/cm`, `re/m/l/c/v/y`,
  `Do` including `/Form` recursion and inline-image skipping) that produces
  per-page: approximate text span boxes, path ink boxes, placed images with
  effective DPI, and best-effort extracted text.
- `kdp_spread_checker/checks.py` — the rule engine that turns those page
  plans plus KDP's published thresholds into a `Report` of findings (JSON
  serializable).
- `kdp_spread_checker/cli.py` — argparse CLI: friendly ranked output,
  `--json`, `--quiet`, `--trim`, `--bleed auto|yes|no`, `--list-trims`,
  documentable exit codes (0 clean, 1 blockers, 2 unreadable).
- `kdp_spread_checker/minipdf.py` — a tiny stdlib PDF writer used by the test
  suite to craft real good/bad interiors (also useful for future exporters).

## How to run

```bash
python -m kdp_spread_checker interior.pdf                 # default trim 6x9, bleed auto
python -m kdp_spread_checker interior.pdf --trim 5.5x8.5 --bleed yes
python -m kdp_spread_checker interior.pdf --json          # machine-readable
python -m kdp_spread_checker interior.pdf --quiet         # blockers only
python -m kdp_spread_checker --list-trims
pip install -e .                                          # optional: `kdp-spread-checker` command
```

Python 3.10+. No third-party dependencies for the tool itself.

## Verification results

- Full test suite: `python -m pytest tests -q` — **13 passed**.
- The suite builds genuine fixture PDFs in-process with `minipdf.py`:
  - a 48-page clean 6x9 interior (0 errors, 0 warnings; exit 0);
  - a 23-page interior (COUNT + PARITY);
  - a gutter-crash interior (GUTTER blocker, correct page attribution);
  - a wrong-trim interior (message quoting the observed 5.5 x 8.5 page);
  - a 49-page with-bleed interior (bleed auto-detected, PARITY, MARGIN,
    GUTTER blockers, exit 1);
  - a duplicate/missing-matter interior (DUP_FRONT, DUP_PAGE, COPYRIGHT);
  - bad-image interiors (IMG_DPI < 150 blocker, IMG_SQUASH, SPREAD_SCAN);
  - a blank-on-recto placement warning.
- CLI behaviors verified: exit code 1 when blockers, 0 when clean, 2 for
  garbage input and missing files with a friendly message; `--json` parses;
  `--list-trims` prints all 14 standard KDP trims; `python -m
  kdp_spread_checker --version` works.
- Parsed and checked real generated PDFs of ~30 pages each in well under 0.1 s.

## Known gaps / future work

- Text extraction is best-effort: subset fonts and CMap-re-encoded glyphs
  (very common in Word/InDesign exports) decode to little text, so the
  front/back-matter checks may report nothing rather than false alarms —
  a resolver with a per-document heuristic decoder is the natural follow-up.
- Glyph advance widths use a half-em average; very wide fonts (e.g., all
  capitals) can make near-margin text look a couple points wider than it is.
- No per-font metrics (AFM/BBox parsing is possible with stdlib and would
  sharpen the margin checks).
- Image DPI for `/Indexed`+DCTDecode and JPXDecode images is tracked from
  the placement matrix, so effective DPI is right, but bit depth/color checks
  (CMYK conversion advice) are out of scope.
- No imposition/spread preview rendering; detection of "facing pages merged
  into one" is heuristic (very wide, full-page images).
- The `kdp-cover-bleed-checker-cli` companion is referenced but lives in a
  separate project.
- Pytest is required only for the test suite; packaging (`pip install -e .`)
  uses setuptools for the console-script entry point.

## License

MIT — `LICENSE`. Copyright (c) 2026 Asdale Dynamics, LLC.
