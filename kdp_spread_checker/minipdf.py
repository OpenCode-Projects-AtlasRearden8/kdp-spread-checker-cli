"""A tiny stdlib-only PDF writer.

This is intentionally small: it exists so the kdp-spread-checker tests can
craft good and bad interior PDFs without any third-party dependency, and so
the project can also emit small helper PDFs. It supports:

* pages with explicit MediaBox sizes
* Helvetica / Helvetica-Bold text placement (hex strings, no escaping pain)
* axis-aligned rectangles (filled or stroked ink)
* RGB images placed with a "tx ty w h" placement (via ``cm``)

Everything is real, readable PDF 1.4. Content streams are zlib-compressed,
which also exercises the checker's FlateDecode path.

Coordinates: bottom-left origin of the MediaBox, points (1/72 inch), y-up.
"""

from __future__ import annotations

import zlib

__all__ = ["MiniPDF", "Page"]


# Approximate Helvetica advance widths (per 1000 units). Only good enough
# for fixture-sized text; the checker treats widths as estimates anyway.
_DEFAULT_WIDTH = 556  # average-ish for Helvetica
_CHAR_WIDTHS = {
    "i": 222, "I": 278, "l": 222, "j": 222, "f": 278, "t": 278,
    "r": 333, "m": 833, "w": 722, "W": 944, "M": 833,
    " ": 278, ".": 278, ",": 278, "'": 222, "|": 260,
}


def _text_width(s: str, size: float) -> float:
    return sum(_CHAR_WIDTHS.get(ch, _DEFAULT_WIDTH) for ch in s) / 1000.0 * size


class _Obj:
    def __init__(self, num: int, data: bytes | bytearray | str):
        self.num = num
        self.raw: bytes = data if isinstance(data, bytes) else data.encode("latin-1")

    def serialize(self, body: bytes) -> bytes:
        head = b"%d 0 obj\n" % self.num
        tail = b"\nendobj\n"
        return head + body + tail


def _stream_dict(data: bytes, extra: str = "") -> bytes:
    return f"<< /Length {len(data)} {extra} >>\nstream\n".encode("latin-1") + data + b"\nendstream"


def _hexstr(text: str) -> str:
    # UTF-16BE with BOM so readers can tell the encoding at a glance
    return "<" + ("\ufeff" + text).encode("utf-16-be").hex().upper() + ">"


class Page:
    """Builder for a single page. Keep track of everything drawn so that
    both fixtures and the checker agree on what a page contains."""

    def __init__(self, doc: "MiniPDF", width_in: float, height_in: float):
        self.doc = doc
        self.w = width_in * 72.0
        self.h = height_in * 72.0
        self.ops: list[str] = []
        self.images: dict[str, str] = {}  # resource name -> xobject object id

    # -- low level ---------------------------------------------------------
    def _add(self, ops: str) -> None:
        self.ops.append(ops)

    # -- text --------------------------------------------------------------
    def text(self, x: float, y: float, s: str, size: float = 12.0, font: str = "F1"):
        """Place text with its left/baseline at (x, y) in page points."""
        self._add(f"BT /{font} {size:g} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm {_hexstr(s)} Tj ET")

    def textbox(self, x: float, y: float, s: str, size: float = 12.0,
                font: str = "F1", width: float = None) -> float:
        """Place text and return approximate drawn width in points."""
        self.text(x, y, s, size=size, font=font)
        w = width if width is not None else _text_width(s, size)
        return x + w

    # -- vector ink --------------------------------------------------------
    def rect(self, x: float, y: float, w: float, h: float, fill: bool = True):
        op = "re f" if fill else "re S"
        self._add(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} {op}")

    def hline(self, x1: float, x2: float, y: float, width: float = 0.5):
        self._add(f"{width:g} w {x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S")

    # -- images ------------------------------------------------------------
    def add_rgb_image(self, name: str, width_px: int, height_px: int,
                      px: tuple[int, int, int], *, place: tuple[float, float, float, float] | None = None,
                      fit_w: float | None = None, fit_h: float | None = None,
                      aspect: float = None):
        """Embed an image (pure color) and place it.

        Either provide ``place=(x, y, w, h)`` in points, or ``fit_w``/``fit_h``
        plus ``aspect`` (w/h of the image layout on the page) to position it
        relative to expected pixel proportions.
        """
        data = bytes(px) * (width_px * height_px)
        data = zlib.compress(data)
        self.doc._register_image(name, width_px, height_px, data)
        self.images[name] = name
        if place is not None:
            x, y, w, h = place
        else:
            if aspect is None:
                aspect = width_px / height_px
            if fit_w is not None:
                w = fit_w
                h = w / aspect
            else:
                h = fit_h
                w = h * aspect
            x = (self.w - w) / 2.0
            y = (self.h - h) / 2.0
        self._add(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm /{name} Do Q")

    # -- final assembly ----------------------------------------------------
    def content_stream(self) -> bytes:
        s = " ".join(self.ops)
        return zlib.compress(s.encode("latin-1"))


class MiniPDF:
    def __init__(self):
        self.pages: list[Page] = []
        self._objs: list[_Obj] = []
        self._next = 1
        self._fonts: dict[str, int] = {}
        self._images: dict[str, int] = {}

    # -- object plumbing ----------------------------------------------------
    def _newobj(self, data) -> _Obj:
        o = _Obj(self._next, data)
        self._next += 1
        self._objs.append(o)
        return o

    def _register_image(self, name: str, w: int, h: int, zlib_data: bytes):
        extra = (
            f"/Type /XObject /Subtype /Image /Width {w} /Height {h} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 "
            f"/Filter /FlateDecode /Name /{name}"
        )
        obj = self._newobj(_stream_dict(zlib_data, extra))
        self._images[name] = obj.num

    def font(self, key: str, base: str = "Helvetica"):
        """Register (once) a simple Type1 base-14 font."""
        if key in self._fonts:
            return
        o = self._newobj(f"<< /Type /Font /Subtype /Type1 /BaseFont /{base} >>")
        self._fonts[key] = o.num

    # -- pages ---------------------------------------------------------------
    def add_page(self, width_in: float = 6.0, height_in: float = 9.0) -> Page:
        p = Page(self, width_in, height_in)
        self.pages.append(p)
        return p

    def blank(self, width_in: float = 6.0, height_in: float = 9.0) -> Page:
        p = self.add_page(width_in, height_in)
        return p

    def tobytes(self) -> bytes:
        # Font resources dictionary for pages that draw text/images.
        xobj_entries = " ".join(f"/{k} {v} 0 R" for k, v in self._images.items())
        font_entries = " ".join(f"/{k} {v} 0 R" for k, v in self._fonts.items())
        if not self._fonts:
            # Always provide at least one font so pages look uniform.
            self.font("F1")
            font_entries = " ".join(f"/{k} {v} 0 R" for k, v in self._fonts.items())

        page_obj_nums: list[int] = []
        for p in self.pages:
            contents = self._newobj(_stream_dict(p.content_stream(), "/Filter /FlateDecode"))
            resources = (f"<< /Fonts << {font_entries} >> "
                         f"/XObjects << {xobj_entries} >> >>")
            pd = (f"<< /Type /Page /Parent PAGES_R /MediaBox [0 0 {p.w:.2f} {p.h:.2f}] "
                  f"/Contents {contents.num} 0 R /Resources {resources} >>").encode("latin-1")
            page_obj_nums.append(self._newobj(pd).num)

        kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
        pages_obj = self._newobj(
            f"<< /Type /Pages /Count {len(page_obj_nums)} /Kids [ {kids} ] >>")
        for o in self._objs:
            if o.raw and b"PAGES_R" in o.raw:
                o.raw = o.raw.replace(b"PAGES_R", f"{pages_obj.num} 0 R".encode("latin-1"))

        catalog = self._newobj(f"<< /Type /Catalog /Pages {pages_obj.num} 0 R >>")

        out = bytearray()
        image_data: dict[int, bytes] = {}
        header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        out += header
        offsets: dict[int, int] = {}
        for o in self._objs:
            offsets[o.num] = len(out)
            out += o.serialize(o.raw)
        xref_at = len(out)
        count = self._next
        out += b"xref\n"
        out += b"0 %d\n" % count
        out += b"0000000000 65535 f \n"
        for i in range(1, count):
            if i in offsets:
                out += (b"%010d 00000 n \n" % offsets[i])
            else:
                out += b"0000000000 00000 n \n"
        out += b"trailer\n"
        out += (f"<< /Size {count} /Root {catalog.num} 0 R >>\n").encode("latin-1")
        out += b"startxref\n" + str(xref_at).encode() + b"\n%%EOF\n"
        return bytes(out)


def build_doc() -> MiniPDF:
    """Convenience factory that pre-registers the standard fixture fonts."""
    d = MiniPDF()
    d.font("F1", "Helvetica")
    d.font("F2", "Helvetica-Bold")
    return d
