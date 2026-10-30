"""Shared fixture builders: crafted good/bad KDP interiors (stdlib only)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kdp_spread_checker.minipdf import build_doc  # noqa: E402

TRIM = (6.0, 9.0)
W, H = 6 * 72, 9 * 72  # 432 x 648 points
GUTTER = 0.375 * 72    # 27 pt for a 24-150 page book
OUT = 0.25 * 72        # 18 pt minimum edge margin


_BODY_VOCAB = [
    "apple", "birch", "cedar", "dawn", "ember", "ferns", "gale", "hinge",
    "iris", "jasper", "kite", "latch", "moss", "nettle", "oats", "pine",
    "quarry", "river", "sage", "tarn", "umber", "vine", "wren", "sable",
    "yarrow", "zest", "amber", "brook", "crest", "drift", "eave", "flask",
    "grove", "haze", "inlet", "juno", "knoll", "lily", "mesa", "north",
    "opal", "pier", "quince", "reef", "sprig", "tide", "umbra", "vale",
]


def _body_line(page, y, i: int = 0, k: int = 0, size=11.0):
    """Recto-safe centered body line, varied enough to dodge the dupe check."""
    w1 = _BODY_VOCAB[(i * 2 + k) % len(_BODY_VOCAB)]
    w2 = _BODY_VOCAB[(i * 3 + k) % len(_BODY_VOCAB)]
    text = f"The {w1} near the {w2} absorbs the potter's care"
    width = 0.5 * size * max(len(text), 1)
    x = GUTTER + 8 + (W - 2 * (GUTTER + 8) - width) / 2
    page.text(x, y, text, size=size)


def make_good_interior() -> bytes:
    """48-page 6x9 interior, no bleed, sane margins, all rules satisfied."""
    doc = build_doc()
    for i in range(1, 49):
        page = doc.add_page(6.0, 9.0)
        if i == 1:
            page.text(GUTTER + 30, 540.0, "The Quiet Ledger", size=20.0)
            page.text(GUTTER + 30, 500.0, "A Novel", size=12.0)
            continue
        if i == 2:
            page.text(60.0, 520.0, "Copyright (c) 2026 by Dana Example", size=10.0)
            page.text(60.0, 500.0, "All rights reserved. No part of this book", size=10.0)
            page.text(60.0, 480.0, "may be reproduced without permission.", size=10.0)
            continue
        if i == 3:
            # table of contents with sane numbers
            page.text(70.0, 600.0, "Table of Contents", size=14.0)
            page.text(70.0, 560.0, "Chapter One ......... 5", size=11.0)
            page.text(70.0, 540.0, "Chapter Two ........ 18", size=11.0)
            page.text(70.0, 520.0, "Chapter Three ...... 31", size=11.0)
            continue
        if i in (18, 48):
            # intentional blank filler (even pages) and final blank verso
            continue
        if i == 6:
            # a properly sized image: 900x600 px placed 3x2 inches => 300 DPI
            page.add_rgb_image(
                "GOODPIC", 900, 600, (240, 240, 245),
                place=(100.0, 300.0, 216.0, 144.0),
            )
            _body_line(page, 180.0)
            continue
        if i == 40:
            page.text(GUTTER + 40, 90.0, "About the Author", size=12.0)
            _body_line(page, 70.0)
            continue
        # body pages: a few lines, safely inside 0.25 in outer margins
        for k, y in enumerate((600.0, 570.0, 540.0)):
            _body_line(page, y, i=i + k)
    return doc.tobytes()


def make_thin_interior() -> bytes:
    """23 pages: below KDP minimum and odd count."""
    doc = build_doc()
    for i in range(23):
        page = doc.add_page(6.0, 9.0)
        if i not in (0, 23 - 1):
            _body_line(page, 560.0)
    return doc.tobytes()


def make_gutter_crash() -> bytes:
    """Body text sits inside the spine margin on a recto page."""
    doc = build_doc()
    for i in range(30):
        page = doc.add_page(6.0, 9.0)
        if i == 0:
            page.text(10.0, 560.0, "Body text pressed right against the spine", size=11.0)
        elif i not in (1, 2):
            _body_line(page, 560.0)
    return doc.tobytes()


def make_wrong_trim() -> bytes:
    """Pages are 5.5x8.5 while the checker looks for 6x9."""
    doc = build_doc()
    for i in range(30):
        page = doc.add_page(5.5, 8.5)
        if i == 1:
            page.text(60.0, 520.0, "Copyright (c) 2026 by Dana Example", size=10.0)
            continue
        if i not in (0,):
            _body_line(page, 540.0)
    return doc.tobytes()


def make_odd_bleed() -> bytes:
    """49 pages at 6.25x9.25 (bleed size); text runs past the trim top."""
    doc = build_doc()
    for i in range(49):
        page = doc.add_page(6.25, 9.25)
        if i == 0:
            page.text(GUTTER + 30, 600.0, "The Quiet Ledger", size=20.0)
            continue
        if i == 42:
            # recto page (odd): deliberately cross the spine margin
            page.text(20.0, 380.0, "This verse dives deep into the spine zone", size=12.0)
            continue
        if i == 44:
            # trim top edge is at 9 in minus 0.125 bleed => y=648.
            # push text above the trim (inside the bleed band)
            page.text(GUTTER + 30, 640.0, "This heading drifts into bleeding", size=12.0)
            continue
        if i == 1:
            page.text(60.0, 520.0, "Copyright (c) 2026 by Dana Example", size=10.0)
            continue
        if i != 48:
            _body_line(page, 560.0)
    return doc.tobytes()


def make_dup_matter() -> bytes:
    """Duplicate title block + duplicate long page + no copyright page."""
    doc = build_doc()
    title = "The Quiet Ledger"
    body = ("She kept the household accounts like a ledger of small fortunes, "
            "every line a quiet promise to the family that she would see them safe.")
    for i in range(30):
        page = doc.add_page(6.0, 9.0)
        if i == 0 or i == 5:
            page.text(GUTTER + 30, 540.0, title, size=20.0)
            continue
        if i == 3 or i == 7:
            page.text(60.0, 560.0, body, size=11.0)
            continue
        if i == 10:
            page.text(GUTTER + 40, 300.0, "About the Author", size=12.0)
            continue
        # no copyright anywhere on pages 1-12
        _body_line(page, 540.0)
    return doc.tobytes()


def make_blank_recto() -> bytes:
    """A blank filler on an odd page, immediately followed by content."""
    doc = build_doc()
    for i in range(30):
        page = doc.add_page(6.0, 9.0)
        if i == 0:
            page.text(GUTTER + 30, 540.0, "The Quiet Ledger", size=20.0)
            continue
        if i in (4,):  # 1-based page 5: a blank right-hand page
            continue
        if i == 1:
            page.text(60.0, 520.0, "Copyright (c) 2026 by Dana Example", size=10.0)
            continue
        _body_line(page, 540.0)
    return doc.tobytes()
