"""Turn decoded page content streams into a geometric plan.

The interpreter follows the PDF graphics model on a simplified level:

* ``cm`` builds a transform stack (with q/Q)
* text operators (BT/ET, Tm, Td, TD, T*, Tf, Tj, TJ, ', ") produce
  approximate text spans with bounding boxes
* path operators (m, l, c, v, y, re) contribute their end points
* images (Do) are projected through the CTM; /Form XObjects recurse

Glyph advance widths use a generic average (about half an em). That is
plenty of precision for margin/bleed decisions - borderline manuscripts
get caught for real reasons, not two-point rounding noise.

This is a heuristic meter, not a full layout engine: it answers "where did
the ink land and how big is it", which is what a KDP pre-upload sweep needs.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .pdfreader import Name, Ref, StreamVal, resolve_ref

__all__ = ["PagePlan", "TextSpan", "PlacedImage", "plan_page"]

_DEFAULT_CHAR_WIDTH = 0.50  # fraction of an em, per glyph (approximation)
_WS = b" \t\n\r\f\x00"
_NUM_RE = re.compile(rb"[+-]?(\d+\.?\d*|\.\d+)")


def _mat_mul(m1, m2):
    a, b, c, d, e, f = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a * a2 + b * c2,
        a * b2 + b * d2,
        c * a2 + d * c2,
        c * b2 + d * d2,
        e * a2 + f * c2 + e2,
        e * b2 + f * d2 + f2,
    )


_STD = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


@dataclass
class TextSpan:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    font_size: float


@dataclass
class PlacedImage:
    name: str
    w_px: int
    h_px: int
    rect: tuple[float, float, float, float]  # on-page points x0, y0, x1, y1
    colorspace: str = ""
    bits: int = 8
    dpi_x: float = 0.0
    dpi_y: float = 0.0

    @property
    def dpi_min(self) -> float:
        vals = [d for d in (self.dpi_x, self.dpi_y) if d > 0]
        return round(min(vals), 1) if vals else 0.0


@dataclass
class PagePlan:
    index: int = 0
    page_box: tuple[float, float, float, float] | None = None  # MediaBox
    text_spans: list[TextSpan] = field(default_factory=list)
    path_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    images: list[PlacedImage] = field(default_factory=list)
    text: str = ""  # best-effort extraction, lowercased/normalized

    def all_boxes(self, want_text=True, want_paths=True, want_images=True):
        out = []
        if want_text:
            for s in self.text_spans:
                out.append((min(s.x0, s.x1), min(s.y0, s.y1), max(s.x0, s.x1), max(s.y0, s.y1)))
        if want_paths:
            out.extend(self.path_boxes)
        if want_images:
            for i in self.images:
                r = i.rect
                out.append((min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])))
        return out

    def union_box(self, want_text=True, want_paths=True, want_images=True):
        boxes = self.all_boxes(want_text, want_paths, want_images)
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def is_blank(self) -> bool:
        return not self.text_spans and not self.path_boxes and not self.images


def _decode_hex(raw: bytes) -> bytes:
    h = re.sub(rb"\s+", b"", raw)
    if len(h) % 2:
        h += b"0"
    try:
        return bytes.fromhex(h.decode("latin-1"))
    except ValueError:
        return b""


def _string_to_text(raw: bytes) -> str:
    if raw.startswith(b"\xfe\xff"):
        try:
            return raw[2:].decode("utf-16-be", "replace")
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1", "replace")


_ESC = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}


def _literal_string(d: bytes, i: int):
    """Parse a literal ( ) string starting at the open paren; returns
    (bytes, index_after_close)."""
    depth = 1
    j = i + 1
    n = len(d)
    buf = bytearray()
    while j < n:
        c2 = d[j:j + 1]
        if c2 == b"\\":
            nx = d[j + 1:j + 2]
            if not nx:
                j += 1
                break
            k = nx[0]
            if k in (10, 13):
                j += 2
                if k == 13 and d[j:j + 1] == b"\n":
                    j += 1
                continue
            if k == ord("n"):
                buf.append(10)
            elif k == ord("r"):
                buf.append(13)
            elif k == ord("t"):
                buf.append(9)
            elif k == ord("b"):
                buf.append(8)
            elif k == ord("f"):
                buf.append(12)
            elif k in map(ord, "()\\"):
                buf.append(k)
            elif k in b"01234567":
                m = re.match(rb"[0-7]{1,3}", d[j + 1:])
                if m:
                    buf.append(int(m.group(0), 8) & 0xFF)
                    j += len(m.group(0)) + 1
                    continue
                buf.append(k)
            j += 2
            continue
        if c2 == b"(":
            depth += 1
        elif c2 == b")":
            depth -= 1
            if depth == 0:
                return bytes(buf), j + 1
        buf.extend(c2)
        j += 1
    return bytes(buf), n


class _Tokenizer:
    def __init__(self, content: bytes):
        self.d = content
        self.i = 0
        self.n = len(content)

    def tokens(self):
        d, n = self.d, self.n
        i = 0
        while i < n:
            c = d[i:i + 1]
            if c in b" \t\n\r\f\x00":
                i += 1
                continue
            if c == b"%":
                j = d.find(b"\n", i + 1)
                i = n if j < 0 else j + 1
                continue
            if c == b"/":
                j = i + 1
                while j < n and d[j:j + 1] not in b"\x00\t\n\r\f ()<>[]{}/%":
                    j += 1
                yield ("name", d[i + 1:j].decode("latin-1"))
                i = j
                continue
            if c == b"<":
                if d[i + 1:i + 2] == b"<":
                    yield ("op", "<<")
                    i += 2
                    continue
                j = d.find(b">", i + 1)
                if j < 0:
                    yield ("str", _decode_hex(d[i + 1:]))
                    i = n
                    continue
                yield ("str", _decode_hex(d[i + 1:j]))
                i = j + 1
                continue
            if c == b"(":
                s, after = _literal_string(d, i)
                yield ("str", s)
                i = after
                continue
            if c == b"[":
                yield ("op", "[")
                i += 1
                continue
            if c == b"]":
                yield ("op", "]")
                i += 1
                continue
            if c == b"B" and d[i:i + 2] == b"BI":
                # inline image: skip to a boundary-aligned EI
                idx = d.find(b"EI", i + 2)
                while idx >= 0:
                    prev = d[idx - 1:idx]
                    nxt = d[idx + 2:idx + 3]
                    if (not prev or prev in _WS) and (not nxt or nxt in _WS):
                        i = idx + 2
                        break
                    idx = d.find(b"EI", idx + 2)
                if i <= idx or idx < 0:
                    self.i = n
                    i = n
                    continue
                i = idx + 2
                continue
            m = _NUM_RE.match(d, i)
            if m and (c.isdigit() or c in b"+-."):
                try:
                    yield ("num", float(m.group(0)))
                except ValueError:
                    pass
                i = m.end()
                continue
            if c.isalpha() or c in b"'\"":
                j = i + 1
                while j < n and (d[j:j + 1].isalpha() or d[j:j + 1] in b"'\""):
                    j += 1
                yield ("op", d[i:j].decode("latin-1"))
                i = j
                continue
            i += 1


class _Engine:
    """Runs the interpreter over a page (plus any embedded Form XObjects)."""

    def __init__(self, objs, depth: int = 0, max_depth: int = 8):
        self.objs = objs
        self.depth = depth
        self.max_depth = max_depth

    def run(self, content: bytes, resources, plan: PagePlan, ctm=_STD):
        gs = {"ctm": ctm}
        stack: list[tuple] = []
        tm = _STD
        font_size = 0.0
        leading = 0.0
        in_bt = False

        operands: list[tuple] = []

        def clear():
            operands.clear()

        def trm():
            return _mat_mul(gs["ctm"], tm)

        def world(p_tx, p_ty):
            return _apply(trm(), p_tx, p_ty)

        def emit_span(text: str, size: float):
            if size < 0.1:
                size = 12.0
            advance = max(len(text), 1) * _DEFAULT_CHAR_WIDTH * size
            p0 = world(0.0, 0.0)
            p1 = world(advance, 0.0)
            p2 = world(0.0, -size * 0.25)  # descender
            p3 = world(0.0, size)          # cap height-ish
            xs = [p0[0], p1[0], p2[0], p3[0]]
            ys = [p0[1], p1[1], p2[1], p3[1]]
            state_text = text.strip()
            if state_text:
                plan.text_spans.append(
                    TextSpan(min(xs), min(ys), max(xs), max(ys), text, size)
                )
            return advance

        def set_tm(new):
            nonlocal tm
            tm = tuple(new) + tuple(tm[6:])

        def shift_tm(dx, dy):
            nonlocal tm
            tm = (tm[0], tm[1], tm[2], tm[3], tm[4] + dx, tm[5] + dy)

        def shift_font_x(delta: float):
            shift_tm(delta, 0.0)

        def lookup_xobject(name: str):
            if not isinstance(resources, dict):
                return None
            xo = resolve_ref(self.objs, resources.get("XObjects"))
            if not isinstance(xo, dict):
                return None
            val = xo.get(name)
            return resolve_ref(self.objs, val)

        def do_image(name: str):
            xobj = lookup_xobject(name)
            if not isinstance(xobj, StreamVal):
                if isinstance(xobj, dict):
                    return
                return
            props = xobj.dict
            st = props.get("Subtype")
            if st == "Image" or st == Name("Image"):
                w_px = int(props.get("Width", 0) or 0)
                h_px = int(props.get("Height", 0) or 0)
                pts = [xform(gs, p) for p in ((0, 0), (1, 0), (1, 1), (0, 1))]
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                rect = (min(xs), min(ys), max(xs), max(ys))
                # in the common un-rotated case: width axis = edge (0,0)->(1,0)
                maybe_w = math.hypot(*_axis_lengths(pts[0], pts[1]))
                same_as_h = math.hypot(*_axis_lengths(pts[0], pts[3]))
                # Heuristic: the longer edge of the image usually corresponds to
                # the pixel-width axis unless the CTM is rotated 90 degrees,
                # in which case Height runs along it.
                rot = abs(gs["ctm"][1]) > 0.5
                w_in = same_as_h / 72.0 if rot else maybe_w / 72.0
                h_in = maybe_w / 72.0 if rot else same_as_h / 72.0
                img = PlacedImage(
                    name=name,
                    w_px=w_px,
                    h_px=h_px,
                    rect=rect,
                    colorspace=_colorspace_text(props.get("ColorSpace")),
                    bits=int(props.get("BitsPerComponent", 8) or 8),
                )
                if w_in > 0.005:
                    img.dpi_x = w_px / w_in
                if h_in > 0.005:
                    img.dpi_y = h_px / h_in
                plan.images.append(img)
            elif st == "Form" or st == Name("Form"):
                if self.depth + 1 > self.max_depth:
                    return
                sub_res = resolve_ref(self.objs, props.get("Resources"))
                if not isinstance(sub_res, dict):
                    sub_res = resources
                try:
                    sub_content = xobj.decoded()
                except Exception:
                    return
                sub_ctm = gs["ctm"]
                mat = props.get("Matrix")
                if isinstance(mat, list) and len(mat) == 6:
                    sub_ctm = _mat_mul(tuple(float(v) for v in mat), sub_ctm)
                engine = _Engine(self.objs, self.depth + 1, self.max_depth)
                engine.run(sub_content, sub_res, plan, ctm=sub_ctm)

        def flush_path(pts):
            pts = [xform(gs, p) for p in pts]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            plan.path_boxes.append((min(xs), min(ys), max(xs), max(ys)))

        arr_stack = []

        for kind, tok in _Tokenizer(content).tokens():
            if kind in ("num", "str", "name"):
                if kind == "num" and arr_stack:
                    arr_stack[-1].append((kind, tok))
                elif arr_stack:
                    arr_stack[-1].append((kind, tok))
                else:
                    operands.append((kind, tok))
                continue
            if kind == "op":
                if tok == "[":
                    arr_stack.append([])
                    continue
                if tok == "]":
                    if arr_stack:
                        contents = arr_stack.pop()
                        if arr_stack:
                            arr_stack[-1].append(("arr", contents))
                        else:
                            operands.append(("arr", contents))
                    continue
                if arr_stack:
                    # operator inside an array - treat as raw element
                    arr_stack[-1].append(("num", 0.0))
                    continue

                nums = [v for (k, v) in operands if k == "num"]
                names = [v for (k, v) in operands if k == "name"]
                strs = [v for (k, v) in operands if k == "str"]
                arrays = [v for (k, v) in operands if k == "arr"]

                if tok == "q":
                    stack.append(gs["ctm"])
                    if len(stack) > 64:
                        stack.clear()
                elif tok == "Q":
                    if stack:
                        gs["ctm"] = stack.pop()
                elif tok == "cm" and len(nums) >= 6:
                    gs["ctm"] = _mat_mul(tuple(nums[-6:]), gs["ctm"])
                elif tok == "BT":
                    in_bt = True
                    set_tm(_STD)
                elif tok == "ET":
                    in_bt = False
                elif tok == "Tf" and nums and names:
                    font_size = nums[-1]
                elif tok == "Tm" and len(nums) >= 6:
                    set_tm(tuple(nums[-6:]))
                elif tok == "Td" and len(nums) >= 2:
                    shift_tm(nums[-2], nums[-1])
                elif tok == "TD" and len(nums) >= 2:
                    leading = -nums[-1]
                    shift_tm(nums[-2], nums[-1])
                elif tok == "TL" and nums:
                    leading = nums[-1]
                elif tok == "T*":
                    shift_tm(0.0, -leading)
                elif tok == "Tj" and strs:
                    adv = emit_span(_string_to_text(strs[-1]), font_size)
                    shift_font_x(adv)
                elif tok == "TJ" and arrays:
                    arr = arrays[-1]
                    pieces = []
                    for (pk, pv) in arr:
                        if pk == "str":
                            pieces.append(_string_to_text(pv))
                    txt = "".join(pieces)
                    adv = emit_span(txt, font_size)
                    shift_font_x(adv)
                elif tok in ("'", '"'):
                    shift_tm(0.0, -leading)
                    if strs:
                        adv = emit_span(_string_to_text(strs[-1]), font_size)
                        shift_font_x(adv)
                elif tok == "re" and len(nums) >= 4:
                    x, y, w, h = nums[-4:]
                    flush_path([(x, y), (x + w, y), (x + w, y + h), (x, y + h)])
                elif tok in ("m", "l") and len(nums) >= 2:
                    p = xform(gs, (nums[-2], nums[-1]))
                    plan.path_boxes.append((p[0], p[1], p[0], p[1]))
                elif tok in ("c", "v", "y") and len(nums) >= 6:
                    flush_path([(nums[-6], nums[-5]), (nums[-4], nums[-3]), (nums[-2], nums[-1])])
                elif tok == "Do" and names:
                    do_image(names[-1])
                # end of op
                clear()
            elif kind == "kw":
                clear()
        return plan


def _axis_lengths(p0, p1):
    return (p1[0] - p0[0], p1[1] - p0[1])


def xform(gs, point):
    return _apply(gs["ctm"], *point)


def _colorspace_text(cs):
    if isinstance(cs, Ref):
        return ""
    if isinstance(cs, list):
        return ",".join(_colorspace_text(x) for x in cs)
    if isinstance(cs, Name):
        return str(cs)
    return str(cs) if cs else ""


def plan_page(page, objects: dict[int, object]) -> PagePlan:
    plan = PagePlan(index=page.index, page_box=tuple(page.mediabox))
    if not page.content:
        return plan
    engine = _Engine(objects)
    engine.run(page.content, page.resources, plan)
    plan.text = re.sub(
        r"\s+", " ", " ".join(s.text for s in plan.text_spans if s.text.strip())
    ).strip().lower()
    return plan
