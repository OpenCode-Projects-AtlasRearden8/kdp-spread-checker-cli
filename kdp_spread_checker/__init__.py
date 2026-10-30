"""Check a finished KDP *interior* PDF before you upload it.

A local, stdlib-only command line tool in the spirit of "AI explained like
a friend would": plain English, ranked fix list, no cloud, no accounts.

Pairs with kdp-cover-bleed-checker-cli (the cover side of a KDP upload).
"""

from .checks import (
    MAX_PAGES,
    MIN_PAGES,
    TRIMS,
    Options,
    Report,
    check_doc,
    gutter_margin,
)
from .pdfreader import PdfError, load_pdf

__version__ = "0.1.0"

__all__ = [
    "Options",
    "Report",
    "check_doc",
    "gutter_margin",
    "load_pdf",
    "PdfError",
    "TRIMS",
    "MIN_PAGES",
    "MAX_PAGES",
    "__version__",
]
