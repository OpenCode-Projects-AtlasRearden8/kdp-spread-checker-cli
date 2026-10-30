"""Command line entry point: python -m kdp_spread_checker interior.pdf"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .checks import Options, Report, check_doc, human_size, TRIMS
from .pdfreader import PdfError, load_pdf

PROG = "kdp-spread-checker"
VERSION = "0.1.0"

_LABEL = {"ERROR": "BLOCKER", "WARN": "warning", "INFO": "note"}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Check a finished KDP *interior* PDF before you upload it: page "
            "count and parity, blank-page placement, margins and bleed by "
            "trim size, duplicate/missing front and back matter, and image "
            "geometry. Pairs with kdp-cover-bleed-checker-cli, which checks "
            "the cover side."
        ),
        epilog=(
            "Exit codes: 0 = clean (or notes only), 1 = upload blockers "
            f"found, 2 = could not read the file.\n"
            f"Example: {PROG} interior.pdf --trim 6x9 --bleed auto"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("pdf", nargs="?", help="the interior PDF to check ('-' for stdin)")
    p.add_argument(
        "--trim", default="6x9",
        help="trim size, named (6x9, 5.5x8.5, ...) or WxH in inches "
             "(default: 6x9). See --list-trims."
    )
    p.add_argument(
        "--bleed", choices=["auto", "yes", "no"], default="auto",
        help="expect bleed margins (yes), exact-trim pages (no), or detect "
             "from the file (default)"
    )
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--quiet", action="store_true",
                   help="only print blockers (results still exit-coded)")
    p.add_argument("--list-trims", action="store_true",
                   help="list the standard KDP paperback trim sizes and exit")
    p.add_argument("--version", action="version", version=f"{PROG} {VERSION}")
    return p


def _resolve_trim(spec: str):
    spec = spec.strip().lower()
    if spec in TRIMS:
        return spec, TRIMS[spec]
    m = __import__("re").fullmatch(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", spec)
    if m:
        w, h = float(m.group(1)), float(m.group(2))
        if 3.0 < w < 9.5 and 3.0 < h < 12.0:
            return spec, (w, h)
    return None, None


def _friendly(path: Path, report: Report) -> str:
    out = []
    w, h = report.trim
    bleed_txt = {
        "yes": "with bleed; page size " + human_size(w + 0.25, h + 0.25),
        "no": "without bleed; page size " + human_size(w, h),
        "unknown": "page size does not match this trim",
    }[report.resolved_bleed]
    out.append(f"{PROG}: {path.name}")
    out.append(
        f"  interior: {report.pages} page(s) · trim {report.trim_key} "
        f"({bleed_txt})"
    )
    if report.n_error == 0 and report.n_warn == 0:
        out.append("  result: no blockers and no warnings - nice work.")
    else:
        out.append(
            f"  result: {report.n_error} blocker(s), {report.n_warn} "
            f"warning(s), {report.n_info} note(s)"
        )
    out.append("")
    cur = None
    for f in report.sorted():
        if f.severity != cur:
            cur = f.severity
            header = {
                "ERROR": "-- UPLOAD BLOCKERS (fix before you upload) --",
                "WARN": "-- WARNINGS (worth a look) --",
                "INFO": "-- NOTES (FYI) --",
            }[cur]
            out.append(header)
        page = f"p{f.page}" if f.page else "-"
        out.append(f"[{page}] {_LABEL[f.severity].upper() if cur == 'ERROR' else _LABEL[f.severity]}: {f.message}")
        if f.detail:
            out.append(f"       fix: {f.detail}")
    if report.n_error == 0:
        out.append("")
        out.append("No hard blockers found. KDP may still flag a few things on their side")
        out.append("(they do their own validation) - these are the checks that catch")
        out.append("most rejections before you spend an upload cycle on them.")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list_trims:
        print("Standard KDP paperback trim sizes (inches):")
        for key, (w, h) in TRIMS.items():
            print(f"  {key:<12} (page with bleed: {w + 0.25:.2f} x {h + 0.25:.2f})")
        return 0

    if not args.pdf:
        parser.error("provide an interior PDF to check (or use --list-trims)")

    if args.pdf == "-":
        data = sys.stdin.buffer.read()
        src = Path("<stdin>")
    else:
        src = Path(args.pdf)
        if not src.exists():
            print(f"error: file not found: {src}", file=sys.stderr)
            return 2
        data = src.read_bytes()

    trim_key, trim = _resolve_trim(args.trim)
    if trim is None:
        print(
            f"error: unknown trim {args.trim!r}. Use a listed name "
            f"(see --list-trims) or WxH in inches like 5.06x7.81.",
            file=sys.stderr,
        )
        return 2

    try:
        doc = load_pdf(data)
    except PdfError as e:
        msg = str(e)
        if args.json:
            print(json.dumps({"error": msg}))
        else:
            print(f"error checking {src.name}: {msg}", file=sys.stderr)
        return 2

    opts = Options(trim_key=trim_key, trim=trim, bleed=args.bleed)
    report = check_doc(doc, opts)

    if args.json:
        payload = report.to_dict()
        payload["file"] = src.name
        payload["exit_code_hint"] = 1 if report.n_error else 0
        print(json.dumps(payload, indent=2))
        return 1 if report.n_error else 0

    text = _friendly(src, report)
    if args.quiet:
        lines = []
        for f in report.sorted():
            if f.severity != "ERROR":
                continue
            page = f"p{f.page}" if f.page else "-"
            lines.append(f"[{page}] BLOCKER: {f.message}")
            if f.detail:
                lines.append(f"       fix: {f.detail}")
        print("\n".join(lines) if lines else "no blockers")
    else:
        print(text)
    return 1 if report.n_error else 0


if __name__ == "__main__":
    sys.exit(main())
