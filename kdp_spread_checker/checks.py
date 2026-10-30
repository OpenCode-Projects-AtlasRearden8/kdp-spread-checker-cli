"""KDP interior rules and the friendly check engine.

Reference points (KDP paperback publishing help, re-checked 2026):

* Page counts: minimum 24 pages, maximum 828. An odd page count is padded
  by KDP with one extra blank page at the end of the book.
* The interior PDF page size must match the trim size exactly (no bleed),
  or trim + 0.25" in both dimensions (which is 0.125" of bleed on each side).
* Minimum outside/top/bottom margins: 0.25" without bleed, 0.375" with bleed.
* Minimum inside (gutter) margin depends on the final page count:
      24-150  -> 0.375 in
     151-300  -> 0.5 in
     301-500  -> 0.625 in
     501-700  -> 0.75 in
     701-828  -> 0.875 in
* Images: target 300 DPI at the printed size; below ~150 DPI print quality
  visibly falls apart.

Page 1 of the PDF is a right-hand (recto) page, exactly as KDP treats it:
odd PDF pages sit on the right of a printed spread, even pages on the left,
so the spine (gutter) margin is on the LEFT of odd pages and the RIGHT of
even pages.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .content import PagePlan, plan_page
from .pdfreader import PdfDoc

IN = 72.0  # points per inch

# --- published KDP trim sizes (inches) --------------------------------------
TRIMS: dict[str, tuple[float, float]] = {
    "5x8": (5.0, 8.0),
    "5.06x7.81": (5.06, 7.81),
    "5.25x8": (5.25, 8.0),
    "5.5x8.5": (5.5, 8.5),
    "6x9": (6.0, 9.0),
    "6.14x9.21": (6.14, 9.21),
    "6.69x9.61": (6.69, 9.61),
    "7x10": (7.0, 10.0),
    "7.44x9.69": (7.44, 9.69),
    "7.5x9.25": (7.5, 9.25),
    "8x10": (8.0, 10.0),
    "8.25x6": (8.25, 6.0),
    "8.25x8.25": (8.25, 8.25),
    "8.5x8.5": (8.5, 8.5),
}

MIN_PAGES = 24
MAX_PAGES = 828
SIZE_TOL = 0.04        # inches of wiggle when matching page size
BLEED = 0.125          # inches, each side
MARGIN_NOBLEED = 0.25  # min outside/top/bottom margin without bleed
MARGIN_BLEED = 0.375   # min outside/top/bottom margin with bleed
DPI_HARD = 150.0
DPI_TARGET = 300.0

GUTTER_TABLE = (
    (24, 150, 0.375),
    (151, 300, 0.5),
    (301, 500, 0.625),
    (501, 700, 0.75),
    (701, 828, 0.875),
)

SEV_ORDER = {"ERROR": 0, "WARN": 1, "INFO": 2}
TOC_LINE = re.compile(r"[.]{2,}[ .]*(\d{1,4})\b")


@dataclass
class Finding:
    code: str
    severity: str  # ERROR | WARN | INFO
    page: int | None
    message: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "page": self.page,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class Options:
    trim_key: str = "6x9"
    trim: tuple[float, float] = (6.0, 9.0)  # inches
    bleed: str = "auto"                      # auto | yes | no


@dataclass
class Report:
    pages: int
    trim_key: str
    trim: tuple[float, float]
    resolved_bleed: str = "unknown"        # yes | no | unknown
    findings: list[Finding] = field(default_factory=list)

    @property
    def n_error(self) -> int:
        return sum(1 for f in self.findings if f.severity == "ERROR")

    @property
    def n_warn(self) -> int:
        return sum(1 for f in self.findings if f.severity == "WARN")

    @property
    def n_info(self) -> int:
        return sum(1 for f in self.findings if f.severity == "INFO")

    def add(self, code, severity, page, message, detail="") -> Finding:
        f = Finding(code, severity, page, message, detail)
        self.findings.append(f)
        return f

    def sorted(self) -> list[Finding]:
        return sorted(
            self.findings,
            key=lambda f: (SEV_ORDER[f.severity],
                           f.page if f.page else 10 ** 6, f.code),
        )

    def to_dict(self) -> dict:
        return {
            "pages": self.pages,
            "trim": self.trim_key,
            "bleed": self.resolved_bleed,
            "counts": {
                "errors": self.n_error,
                "warnings": self.n_warn,
                "notes": self.n_info,
            },
            "findings": [f.to_dict() for f in self.sorted()],
        }


def gutter_margin(page_count: int) -> float:
    pc = min(max(page_count, MIN_PAGES), MAX_PAGES)
    for lo, hi, margin in GUTTER_TABLE:
        if lo <= pc <= hi:
            return margin
    return GUTTER_TABLE[-1][2]


def human_size(w: float, h: float) -> str:
    def fmt(v: float) -> str:
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return s
    return f"{fmt(w)} x {fmt(h)} in"


def _close(a: float, b: float, tol: float = SIZE_TOL) -> bool:
    return abs(a - b) <= tol


def _blank(plan: PagePlan) -> bool:
    return plan.is_blank()


def check_doc(doc: PdfDoc, opts: Options) -> Report:
    plans = [plan_page(p, doc.objects) for p in doc.pages]
    n = len(doc.pages)
    report = Report(pages=n, trim_key=opts.trim_key, trim=opts.trim)

    tw, th = opts.trim

    # --- how do the page sizes relate to the chosen trim? ---------------------
    n_trim = sum(
        1 for p in doc.pages
        if _close(p.mediabox[2] / IN, tw) and _close(p.mediabox[3] / IN, th)
    )
    n_bleed = sum(
        1 for p in doc.pages
        if _close(p.mediabox[2] / IN, tw + 2 * BLEED)
        and _close(p.mediabox[3] / IN, th + 2 * BLEED)
    )
    exp_nob = human_size(tw, th)
    exp_bleed = human_size(tw + 2 * BLEED, th + 2 * BLEED)

    if opts.bleed == "yes":
        report.resolved_bleed = "yes"
        if n_bleed < n:
            report.add(
                "SIZE", "ERROR", None,
                f"You asked for bleed, but not every page is {exp_bleed} "
                f"(trim {exp_nob} plus 0.125 in on each side).",
                "Set the page size of the whole document to trim + 0.25 in "
                "in both directions, then re-export."
            )
    elif opts.bleed == "no":
        report.resolved_bleed = "no"
        if n_trim < n:
            report.add(
                "SIZE", "ERROR", None,
                f"You said no bleed, but not every page is {exp_nob}.",
                "Either export at the exact trim size, or run again with "
                "--bleed yes if this book is meant to bleed."
            )
    else:  # auto
        if n_trim and n_bleed:
            report.resolved_bleed = "unknown"
            report.add(
                "SIZE_MIX", "ERROR", None,
                "The file mixes page sizes: "
                f"{n_trim} x {exp_nob} (no bleed) and {n_bleed} x {exp_bleed} (with bleed).",
                "One book, one page size: pick with-bleed or without for the "
                "whole interior and re-export."
            )
        elif n_bleed:
            report.resolved_bleed = "yes"
            report.add(
                "SIZE", "INFO", None,
                f"Detected bleed: pages are {exp_bleed} "
                f"(trim {exp_nob} + 0.125 in on each side).",
                "With bleed on, KDP also expects 0.375 in outside/top/bottom "
                "margins - checked below."
            )
        elif n_trim:
            report.resolved_bleed = "no"
        else:
            report.resolved_bleed = "unknown"

    _check_page_sizes(report, doc, opts, exp_nob, exp_bleed)
    _check_counts(report, n)
    _check_margins(report, doc, plans, opts)
    _check_blank_placement(report, plans)
    _check_front_back_matter(report, plans)
    _check_images(report, plans)

    seen: set[tuple] = set()
    deduped = []
    for f in report.findings:
        k = (f.code, f.severity, f.page, f.message)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(f)
    report.findings = deduped
    return report


# ---------------------------------------------------------------------------

def _check_page_sizes(report: Report, doc: PdfDoc, opts: Options, exp_n: str, exp_b: str):
    tw, th = opts.trim
    if opts.bleed == "yes":
        expected = exp_b
    elif opts.bleed == "no":
        expected = exp_n
    else:
        expected = f"{exp_n} (no bleed) or {exp_b} (with bleed)"

    odd = []
    for p in doc.pages:
        w = p.mediabox[2] / IN
        h = p.mediabox[3] / IN
        if not (_close(w, tw) and _close(h, th)) and not (
            _close(w, tw + 2 * BLEED) and _close(h, th + 2 * BLEED)
        ):
            odd.append((p.index, w, h))
    if not odd:
        return
    idx, w, h = odd[0]
    report.add(
        "SIZE", "ERROR", idx,
        f"Page {idx} is {human_size(w, h)}, but trim {opts.trim_key} "
        f"expects {expected}.",
        "Set the whole document to the correct page size before export "
        "(in Word: Layout > Size > More Paper Sizes; in layout tools it is "
        "the document/page setup)."
    )
    if len(odd) > 1:
        more = ", ".join(f"p{i}" for i, _, _ in odd[1:12])
        report.add(
            "SIZE", "ERROR", None,
            f"{len(odd) - 1} more page(s) have the wrong size as well "
            f"({more}).",
            "Same fix as above - the entire interior needs one consistent "
            "page size."
        )


def _check_counts(report: Report, n: int):
    if n < MIN_PAGES:
        report.add(
            "COUNT", "ERROR", None,
            f"This interior has {n} pages; KDP's paperback minimum is "
            f"{MIN_PAGES}.",
            "Add front matter (title page, copyright page, table of "
            "contents) and back matter to get past the minimum."
        )
    elif n > MAX_PAGES:
        report.add(
            "COUNT", "ERROR", None,
            f"This interior has {n} pages; KDP caps paperbacks at {MAX_PAGES}.",
            "If the book really is that long, plan to split it into volumes."
        )
    if n and n % 2 == 1:
        report.add(
            "PARITY", "WARN", None,
            f"The page count ({n}) is odd, so KDP will silently add one "
            "blank page at the end of the printed book.",
            "Add (or remove) a page yourself - usually a blank verso - so "
            "you control what that last page looks like."
        )


def _check_margins(report: Report, doc: PdfDoc, plans: list[PagePlan], opts: Options):
    n = len(plans)
    if n == 0:
        return
    gutter = gutter_margin(n)
    min_out = MARGIN_BLEED if report.resolved_bleed == "yes" else MARGIN_NOBLEED
    with_bleed = report.resolved_bleed == "yes"

    edge_ink_pages: list[int] = []
    unbleeded_pages: list[int] = []

    def _listing(vals: list[int]) -> str:
        s = ", ".join(f"p{v}" for v in vals[:10])
        return s + (", ..." if len(vals) > 10 else "")

    for page, plan in zip(doc.pages, plans):
        idx = page.index
        mb = page.mediabox
        x0, y0, x1, y1 = mb
        if with_bleed:
            tx0, ty0 = x0 + BLEED * IN, y0 + BLEED * IN
            tx1, ty1 = x1 - BLEED * IN, y1 - BLEED * IN
        else:
            tx0, ty0, tx1, ty1 = x0, y0, x1, y1
        recto = idx % 2 == 1
        if recto:
            g_lo, g_hi = tx0, tx0 + gutter * IN          # spine on the left
        else:
            g_lo, g_hi = tx1 - gutter * IN, tx1          # spine on the right
        tol = 0.02 * IN

        # --- gutter: text may never enter the spine strip ----------------------
        crossed = False
        for s in plan.text_spans:
            if min(s.x1, g_hi) - max(s.x0, g_lo) > tol:
                report.add(
                    "GUTTER", "ERROR", idx,
                    f"Text runs into the spine margin on page {idx} "
                    f"(minimum {gutter:g} in gutter for a {n}-page book).",
                    "Increase the inside margin in your layout tool so body "
                    "text stays out of the binding area; heading ink may "
                    "look close but body text must clear the gutter."
                )
                crossed = True
                break
        if not crossed:
            for img in plan.images:
                r = img.rect
                if min(r[2], g_hi) - max(r[0], g_lo) > tol:
                    spans_page = (r[2] - r[0]) > 0.96 * (tx1 - tx0)
                    if not spans_page:
                        report.add(
                            "IMG_GUTTER", "WARN", idx,
                            f"An image on page {idx} pokes into the spine "
                            f"margin ({gutter:g} in minimum for {n} pages).",
                            "Fine if it's deliberate full-bleed art that "
                            "wraps the whole spread; otherwise pull it off "
                            "the spine."
                        )
                    break

        # --- outer margins: text within top/bottom/outside minimums -----------
        for s in plan.text_spans:
            d_left = (s.x0 - tx0) / IN
            d_right = (tx1 - s.x1) / IN
            d_top = (ty1 - s.y1) / IN
            d_bot = (s.y0 - ty0) / IN
            spine_side = d_left if recto else d_right
            if spine_side <= 0.02:
                # gutter check already reported; keep loop cheap
                continue
            outside = min(d_right if recto else d_left, d_top, d_bot)
            if outside <= 0.02:
                report.add(
                    "OFF_TRIM", "WARN", idx,
                    f"Text on page {idx} runs past the trim edge (by about "
                    f"{-outside:.2g} in).",
                    "Body text should sit fully inside the trim; only "
                    "deliberate art should cross it."
                )
                break
            if outside < min_out - 0.004:
                report.add(
                    "MARGIN", "WARN", idx,
                    f"Text on page {idx} is closer than {min_out:g} in to "
                    "an outer/top/bottom edge.",
                    f"Nudge text to at least {min_out:g} in from the outer, "
                    "top, and bottom trim edges."
                )
                break

        # --- artificial-no-bleed hint ----------------------------------------
        if not with_bleed:
            ink = plan.union_box()
            if ink:
                at_edge = (
                    ink[0] <= x0 + tol or ink[1] <= y0 + tol
                    or ink[2] >= x1 - tol or ink[3] >= y1 - tol
                )
                if at_edge:
                    edge_ink_pages.append(idx)
        elif not plan.is_blank():
            ink = plan.union_box()
            if ink and not (
                ink[0] < tx0 - tol or ink[1] < ty0 - tol
                or ink[2] > tx1 + tol or ink[3] > ty1 + tol
            ):
                unbleeded_pages.append(idx)

    if edge_ink_pages:
        report.add(
            "EDGE_INK", "INFO", edge_ink_pages[0],
            f"Ink touches the page edge as if designed for bleed, but the "
            f"file has no bleed (pages: {_listing(edge_ink_pages)}).",
            "If that art is meant to print to the paper edge, export with "
            "bleed (page size trim + 0.25 in). Otherwise pull it slightly "
            "inside the trim."
        )
    if unbleeded_pages:
        report.add(
            "NO_BLEED_ART", "INFO", None,
            f"Pages with no bleeding content: {_listing(unbleeded_pages)}",
            "That's fine for text pages - only worth review where you "
            "expected full-bleed art."
        )


def _check_blank_placement(report: Report, plans: list[PagePlan]):
    n = len(plans)
    if n == 0:
        return
    blanks = [i + 1 for i, p in enumerate(plans) if _blank(p)]
    if not blanks:
        return
    listed = ", ".join(str(b) for b in blanks[:10])
    if len(blanks) > 10:
        listed += ", ..."
    report.add(
        "BLANKS", "INFO", None,
        f"{len(blanks)} blank page(s): page(s) {listed}.",
        "Blank pages are normal fillers so sections can open on a "
        "right-hand page."
    )
    for b in blanks:
        if b % 2 == 1 and b != n and not _blank(plans[b]):
            report.add(
                "BLANK_RECTO", "WARN", b,
                f"Page {b} is blank, but page {b + 1} (a left-hand page) "
                "carries content.",
                "New sections usually open on a right-hand page, so filler "
                "blanks normally sit on an even page. Check whether this "
                "blank was meant to be somewhere else."
            )
    # long blank runs
    blank_set = set(blanks)
    run_start = None
    run_len = 0
    for i in range(1, n + 2):
        if i <= n and i in blank_set:
            if run_len == 0:
                run_start = i
            run_len += 1
            continue
        if run_len >= 3:
            report.add(
                "BLANK_RUN", "WARN", run_start,
                f"Pages {run_start}-{run_start + run_len - 1} are all blank "
                f"({run_len} in a row).",
                "A run of blanks usually means a chapter disappeared between "
                "exports - or genuinely heavy padding."
            )
        run_len = 0
    if _blank(plans[-1]):
        report.add(
            "END_BLANK", "INFO", n,
            "The final page is blank - the classic, KDP-friendly ending.",
            ""
        )


def _norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9 ©]+", " ", re.sub(r"\s+", " ", s.lower())).strip()


def _check_front_back_matter(report: Report, plans: list[PagePlan]):
    n = len(plans)
    if n == 0:
        return
    texts = [p.text for p in plans]

    # --- title page ------------------------------------------------------------
    if not texts[0]:
        report.add(
            "TITLE", "WARN", 1,
            "Page 1 has no text; the title (or half-title) page usually "
            "sits there.",
            "If the blank first page is intentional padding, that's OK - "
            "just confirm the title page didn't get lost in the export."
        )
    else:
        first_norm = _norm_text(texts[0])
        if len(first_norm) > 10:
            for i in range(1, n):
                if _norm_text(texts[i]) == first_norm:
                    report.add(
                        "DUP_FRONT", "WARN", i + 1,
                        f"Page {i + 1} repeats page 1's title block.",
                        "Deliberate half-title repeats are fine; otherwise "
                        "remove the duplicate page."
                    )
                    break

    # --- copyright page ----------------------------------------------------------
    copy_pages = []
    for i in range(min(n, 12)):
        t = texts[i]
        if ("copyright" in t or "(c)" in t or "©" in t
                or "all rights reserved" in t or "publication" in t):
            copy_pages.append(i + 1)
    if not copy_pages:
        report.add(
            "COPYRIGHT", "WARN", None,
            "No copyright-style page found in the first 12 pages.",
            "Usually page 2 or 4: your name, year, ISBN, and 'All rights "
            "reserved.'"
        )
    elif len(copy_pages) > 1:
        report.add(
            "DUP_COPYRIGHT", "WARN", copy_pages[-1],
            f"Several copyright-style pages found: "
            f"{', '.join(map(str, copy_pages))}.",
            "Keep one (usually page 2 or 4) and delete template leftovers."
        )

    # --- duplicate page text ------------------------------------------------------
    seen: dict[str, int] = {}
    for i, t in enumerate(texts, start=1):
        if len(t) < 60:
            continue
        key = re.sub(r"\d+", "#", _norm_text(t))
        if key in seen:
            report.add(
                "DUP_PAGE", "WARN", i,
                f"Pages {seen[key]} and {i} carry the same text.",
                "Common after re-exports - delete the leftover copy unless "
                "it's an intentional repeated divider."
            )
        else:
            seen[key] = i

    # --- table of contents -----------------------------------------------------------
    toc_window = max(4, n // 4)
    for i, t in enumerate(texts, start=1):
        if i > toc_window:
            break
        if "contents" not in t:
            continue
        nums = [int(m.group(1)) for m in TOC_LINE.finditer(t)]
        if nums:
            over = sorted(x for x in nums if x > n)
            if over:
                sample = ", ".join(map(str, over[:6]))
                report.add(
                    "TOC_RANGE", "ERROR", i,
                    f"The table of contents on page {i} points to page(s) "
                    f"{sample}, past the end of this {n}-page file.",
                    "Chapters were probably removed after the TOC was "
                    "written - refresh its page numbers before uploading."
                )
            report.add(
                "TOC", "INFO", i,
                f"Table of contents on page {i} ({len(nums)} entries, "
                f"{len(over)} pointing past the end).",
                ""
            )
            break

    # --- back matter -------------------------------------------------------------------
    back_markers = ("about the author", "acknowledg", "afterword",
                    "glossary", "index", "about the publisher")
    found = []
    window = max(2, n // 4)
    for i in range(max(3, n - window), n):
        for key in back_markers:
            if key in texts[i]:
                found.append((i + 1, key))
                break
    if not found:
        report.add(
            "BACKMATTER", "INFO", None,
            "No 'About the Author' / acknowledgments / index detected in the "
            "back quarter of the book.",
            "Optional, but many readers look for an About the Author page - "
            "worth one last look."
        )
    else:
        listed = ", ".join(f"p{p} ({w})" for p, w in found[:5])
        report.add(
            "BACKMATTER", "INFO", found[0][0],
            f"Back matter spotted: {listed}.",
            ""
        )


def _check_images(report: Report, plans: list[PagePlan]):
    for plan in plans:
        page_box = plan.page_box
        for img in plan.images:
            dpi = img.dpi_min
            if dpi <= 0:
                continue
            if dpi < DPI_HARD:
                report.add(
                    "IMG_DPI", "ERROR", plan.index,
                    f"An image on page {plan.index} is roughly {dpi:.0f} DPI "
                    f"({img.w_px} x {img.h_px} px) at its placed size.",
                    "KDP wants 300 DPI at final print size; enlarge the "
                    "source image or shrink the frame on the page."
                )
            elif dpi < DPI_TARGET:
                report.add(
                    "IMG_DPI", "WARN", plan.index,
                    f"An image on page {plan.index} is about {dpi:.0f} DPI "
                    f"({img.w_px} x {img.h_px} px), under the 300 DPI target.",
                    "Bump it to 300 DPI at the printed size - enlarge the "
                    "original export or reduce the placed frame."
                )
            if img.dpi_x > 0 and img.dpi_y > 0:
                ratio = img.dpi_x / img.dpi_y
                if ratio > 1.07 or ratio < 0.93:
                    report.add(
                        "IMG_SQUASH", "WARN", plan.index,
                        f"An image on page {plan.index} appears stretched "
                        f"(width/height ratio about {ratio:.2f}).",
                        "Resize with proportions locked so pixel shape "
                        "matches the frame."
                    )
            r = img.rect
            if page_box:
                pw = page_box[2] - page_box[0]
                ph = page_box[3] - page_box[1]
                iw = max(r[2] - r[0], 0.01)
                ih = max(r[3] - r[1], 0.01)
                if pw and ph and (iw / pw) > 0.85 and (iw / ih) > 1.4:
                    report.add(
                        "SPREAD_SCAN", "WARN", plan.index,
                        f"Page {plan.index}: one extremely wide image fills "
                        "nearly the whole page.",
                        "If that's a two-page spread exported as one page, "
                        "KDP will print it shrunk onto a single trim page - "
                        "re-export the left and right halves separately."
                    )
                    break
