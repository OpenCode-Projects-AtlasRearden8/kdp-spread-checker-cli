"""Crafted good/bad KDP interiors, checked end to end with the CLI helper."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kdp_spread_checker.minipdf import build_doc  # noqa: E402
from tests.fixtures import (  # noqa: E402
    make_good_interior,
    make_thin_interior,
    make_gutter_crash,
    make_wrong_trim,
    make_odd_bleed,
    make_dup_matter,
    make_blank_recto,
)

HERE = Path(__file__).parent


def run_cli(pdf_bytes: bytes, *args: str) -> tuple[int, str, dict | None]:
    path = HERE / "tmp_interior.pdf"
    path.write_bytes(pdf_bytes)
    proc = subprocess.run(
        [sys.executable, "-m", "kdp_spread_checker", str(path), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    doc = None
    if "--json" in args and proc.returncode in (0, 1):
        try:
            doc = json.loads(proc.stdout)
        except ValueError:
            doc = None
    return proc.returncode, proc.stdout + proc.stderr, doc


# ---------------------------------------------------------------------------
# the good book: everything sat puts
# ---------------------------------------------------------------------------

def test_good_interior_is_clean():
    code, out, doc = run_cli(make_good_interior(), "--json", "--trim", "6x9")
    assert code == 0, out
    assert doc is not None
    assert doc["counts"]["errors"] == 0, out
    assert doc["counts"]["warnings"] == 0, out
    assert doc["pages"] == 48
    assert doc["bleed"] == "no"
    codes = {f["code"] for f in doc["findings"]}
    assert "GUTTER" not in codes
    assert "IMG_DPI" not in codes
    assert "TOC_RANGE" not in codes


def test_good_interior_human_output_mentions_trim():
    code, out, _ = run_cli(make_good_interior(), "--trim", "6x9")
    assert code == 0
    assert "no blockers" in out.lower() or "no warnings" in out.lower()
    assert "48 page" in out


# ---------------------------------------------------------------------------
# crafted bad books
# ---------------------------------------------------------------------------

def test_thin_interior_flags_minimum_and_parity():
    code, out, doc = run_cli(make_thin_interior(), "--json")
    assert code == 1
    codes = {f["code"] for f in doc["findings"]}
    assert "COUNT" in codes      # below 24 pages
    assert "PARITY" in codes     # odd page count


def test_gutter_crash_flags_text_in_spine():
    code, out, doc = run_cli(make_gutter_crash(), "--json")
    assert code == 1
    codes = {f["code"] for f in doc["findings"]}
    assert "GUTTER" in codes
    page = next(f["page"] for f in doc["findings"] if f["code"] == "GUTTER")
    assert page == 1


def test_wrong_trim_flags_size():
    code, out, doc = run_cli(make_wrong_trim(), "--json", "--trim", "6x9")
    assert code == 1
    codes = {f["code"] for f in doc["findings"]}
    assert "SIZE" in codes
    msg = next(f["message"] for f in doc["findings"] if f["code"] == "SIZE" and f["page"])
    assert "5.5 x 8.5" in msg


def test_odd_bleed_interior_detects_bleed_and_off_trim_text():
    code, out, doc = run_cli(make_odd_bleed(), "--json", "--trim", "6x9")
    assert code == 1
    assert doc["bleed"] == "yes"
    codes = {f["code"] for f in doc["findings"]}
    assert "PARITY" in codes          # 49 pages, odd
    assert "OFF_TRIM" in codes or "MARGIN" in codes


def test_duplicate_and_missing_matter():
    code, out, doc = run_cli(make_dup_matter(), "--json")
    assert code == 1
    codes = {f["code"] for f in doc["findings"]}
    assert "DUP_FRONT" in codes       # repeated title block
    assert "DUP_PAGE" in codes        # identical long body text, twice
    assert "COPYRIGHT" in codes       # no copyright page found


def test_blank_on_right_page_warns():
    code, out, doc = run_cli(make_blank_recto(), "--json")
    assert code == 0                  # a placement warning alone is not a blocker
    assert doc["counts"]["warnings"] == 1
    f = next(x for x in doc["findings"] if x["code"] == "BLANK_RECTO")
    assert f["page"] == 5
    assert "page 6" in f["message"]


# ---------------------------------------------------------------------------
# crafted bad images
# ---------------------------------------------------------------------------

def _make_bad_image_interior() -> bytes:
    doc = build_doc()
    for i in range(30):
        page = doc.add_page(6.0, 9.0)
        if i == 4:
            # 100x100 px placed at 2x3 inches => ~50 DPI and squashed
            page.add_rgb_image(
                "BADPIC", 100, 100, (200, 200, 200),
                place=(100.0, 300.0, 144.0, 216.0),
            )
            continue
        if i == 8:
            # very wide image spanning the page => spread-scan hint
            page.add_rgb_image(
                "WIDEPIC", 1600, 400, (220, 220, 220),
                place=(20.0, 60.0, 392.0, 98.0),
            )
            continue
        from tests.fixtures import _body_line  # noqa: PLC0415
        _body_line(page, 560.0, i=i, k=(i % 3))
    return doc.tobytes()


def test_blurry_and_stretched_images_flagged():
    code, out, doc = run_cli(_make_bad_image_interior(), "--json")
    assert code == 1
    codes = {f["code"] for f in doc["findings"]}
    assert "IMG_DPI" in codes     # 50 DPI < 150 hard floor
    assert "IMG_SQUASH" in codes  # 2x3 inches vs square pixels
    assert "SPREAD_SCAN" in codes # whole-page wide image


# ---------------------------------------------------------------------------
# CLI behaviours
# ---------------------------------------------------------------------------

def test_json_and_quiet_commands():
    code, doc, json_doc = run_cli(make_good_interior(), "--json")
    assert code == 0
    assert json_doc["counts"]["errors"] == 0


def test_list_trims_shows_sizes():
    from kdp_spread_checker.cli import main as cli_main
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli_main(["--list-trims"])
    assert code == 0
    assert "6x9" in buf.getvalue()


def test_garbage_file_friendly_error(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"this is not really a pdf at all\n" * 4)
    from kdp_spread_checker.cli import main as cli_main
    import contextlib, io
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = cli_main([str(bad)])
    assert code == 2
    assert "does not look like a PDF" in err.getvalue()


def test_missing_file_exit_code_2():
    from kdp_spread_checker.cli import main as cli_main
    import contextlib, io
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = cli_main([str(HERE / "nope_missing.pdf")])
    assert code == 2
    assert "not found" in err.getvalue()
