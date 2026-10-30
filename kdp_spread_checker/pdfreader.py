"""A small, forgiving PDF reader built on the Python standard library.

It scans the byte stream for indirect objects (a technique that tolerates
broken or missing xref tables), decodes Flate streams with ``zlib``, and
supports object streams (modern tools embed objects that way).

What it deliberately does *not* handle (and reports as such):

* encrypted PDFs (KDP interiors should never be encrypted)
* exotic image filters like JBIG2 (that image is simply skipped)
* CMap-driven glyph re-encoding (text extraction is best-effort)

All text/image geometry comes from the content stream operators, which is
exactly what KDP cares about: where the ink actually lands on the page.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field

__all__ = ["PdfError", "load_pdf", "RawPage", "PdfDoc", "Name", "Ref", "StreamVal"]


class PdfError(Exception):
    """A human-readable problem with the file itself."""


class Name(str):
    __slots__ = ()


@dataclass
class Ref:
    num: int
    gen: int


@dataclass
class StreamVal:
    dict: dict
    data: bytes  # as stored (still encoded)

    def decoded(self) -> bytes:
        filt = self.dict.get("Filter")
        if isinstance(filt, list):
            filt = filt[0] if filt else None
        if filt == Name("FlateDecode"):
            try:
                return zlib.decompress(self.data)
            except zlib.error:
                d = zlib.decompressobj()
                try:
                    return d.decompress(self.data)
                except zlib.error:
                    return b""
        return self.data


_SKIP = re.compile(rb"[\x00\t\n\r\f ]*(?:%[^\r\n]*[\x00\t\n\r\f ]*)*")


class _Parser:
    def __init__(self, data: bytes, i: int = 0):
        self.d = data
        self.i = i

    def skip_ws(self):
        m = _SKIP.match(self.d, self.i)
        self.i = m.end() if m else len(self.d)

    def at_end(self) -> bool:
        return self.i >= len(self.d)

    def parse(self):
        self.skip_ws()
        if self.at_end():
            return None
        d, i = self.d, self.i
        ch = d[i:i + 1]
        if ch == b"<<":
            return self._parse_dict()
        if ch == b">>":
            self.i += 2
            return None
        if ch == b"[":
            self.i += 1
            out = []
            while True:
                self.skip_ws()
                if self.at_end() or self.d[self.i:self.i + 1] == b"]":
                    self.i += 1
                    break
                out.append(self.parse())
            return out
        if ch == b"<":
            if d[i + 1:i + 2] == b"<":
                return self._parse_dict()
            end = d.find(b">", i + 1)
            if end < 0:
                self.i = len(d)
                return b""
            raw = d[i + 1:end]
            try:
                return bytes.fromhex(raw.decode("latin-1"))
            except (ValueError, TypeError):
                self.i = end + 1
                return b""
        if ch == b"/":
            return self._parse_name()
        if ch == b"(":
            return self._parse_literal_string()
        if ch.isdigit() or (ch in b"+-.") or ch == b"\n":
            m = re.match(rb"[+-]?(\d+\.?\d*|\.\d+)", d[i:])
            if not m:
                self.i += 1
                return None
            tok = m.group(0)
            self.i = i + len(tok)
            m2 = re.match(rb"[\x00\t\n\r\f ]+(\d+)[\x00\t\n\r\f ]+R(?![0-9A-Za-z])", d[self.i:])
            if m2:
                self.i += m2.end()
                return Ref(int(m.group(1)), int(m2.group(1)))
            try:
                return float(tok)
            except ValueError:
                return None
        if ch.isalpha() or ch in b"'\"":
            m = re.match(rb"[A-Za-z']+", d[i:])
            word = m.group(0)
            self.i = i + len(word)
            if word == b"true":
                return True
            if word == b"false":
                return False
            return word.decode("latin-1")
        self.i += 1
        return None

    def _parse_dict(self) -> dict:
        self.i += 2
        out: dict = {}
        while True:
            self.skip_ws()
            if self.at_end():
                return out
            two = self.d[self.i:self.i + 2]
            if two == b">>":
                self.i += 2
                return out
            if self.d[self.i:self.i + 1] != b"/":
                self.i += 1
                continue
            key = self._parse_name()
            self.skip_ws()
            two = self.d[self.i:self.i + 2]
            if two == b">>" or self.at_end():
                self.i += 1
                continue
            out[key] = self.parse()

    def _parse_name(self) -> Name:
        self.i += 1
        start = self.i
        d = self.d
        while self.i < len(d):
            c = d[self.i]
            if c in b"\x00\t\n\r\f ()<>[]{}/%":
                break
            self.i += 1
        return Name(d[start:self.i].decode("latin-1"))

    def _parse_literal_string(self) -> bytes:
        d = self.d
        depth = 0
        i = self.i
        out = bytearray()
        while i < len(d):
            ch = d[i:i + 1]
            if ch == b"\\":
                nxt = d[i + 1:i + 2]
                if not nxt:
                    i += 2
                    break
                if nxt == b"n":
                    out.append(10)
                elif nxt == b"r":
                    out.append(13)
                elif nxt == b"t":
                    out.append(9)
                elif nxt == b"b":
                    out.append(8)
                elif nxt == b"f":
                    out.append(12)
                elif nxt in b"()\\":
                    out.append(nxt)
                elif nxt.isdigit():
                    m = re.match(rb"[0-7]{1,3}", d[i + 1:])
                    if m:
                        out.append(int(m.group(0), 8) & 0xFF)
                        i += 1 + len(m.group(0))
                        continue
                    out.append(d[i + 1])
                elif nxt in b"\r\n":
                    if d[i + 2:i + 3] == b"\n" or nxt == b"\r" and d[i + 2:i + 3] == b"\n":
                        i += 3
                        continue
                    i += 2
                    continue
                else:
                    out.append(d[i + 1])
                i += 2
                continue
            if ch == b"(":
                depth += 1
                i += 1
                continue
            if ch == b")":
                depth -= 1
                i += 1
                if depth <= 0:
                    break
                continue
            out.append(d[i])
            i += 1
        self.i = i
        return bytes(out)


_OBJ_HEAD = re.compile(rb"(?<![0-9A-Za-z])(\d+)[\x00\t\n\r\f ]+(\d+)[\x00\t\n\r\f ]+obj")


def _parse_indirect(data: bytes, start: int):
    p = _Parser(data, start)
    try:
        val = p.parse()
    except Exception:
        return None
    if val is None:
        return None
    if isinstance(val, dict):
        save = p.i
        p.skip_ws()
        if data[p.i:p.i + 6] == b"stream":
            p.i += 6
            if data[p.i:p.i + 2] == b"\r\n":
                p.i += 2
            elif data[p.i:p.i + 1] in (b"\r", b"\n"):
                p.i += 1
            length = val.get("Length")
            raw = None
            if isinstance(length, (int, float)) and float(length).is_integer() and length >= 0:
                n = int(length)
                candidate = data[p.i:p.i + n]
                after = data[p.i + n:p.i + n + 16]
                if len(candidate) == n and re.match(rb"[\x00\t\n\r\f ]*endstream", after):
                    raw = candidate
                    p.i = p.i + n
            if raw is None:
                idx = data.find(b"endstream", p.i)
                if idx < 0:
                    raw = data[p.i:]
                    p.i = len(data)
                else:
                    raw = data[p.i:idx]
                    p.i = idx + len(b"endstream")
            val = StreamVal(val, raw)
    return val


def _scan_objects(data: bytes) -> dict[int, object]:
    """Indirect objects found by scanning, keyed by object number."""
    found: dict[int, object] = {}
    for m in _OBJ_HEAD.finditer(data):
        num = int(m.group(1))
        if num in found:
            continue
        val = _parse_indirect(data, m.end())
        if val is not None:
            found[num] = val
    return found


def _expand_objstms(objs: dict[int, object]) -> None:
    """Pull objects out of /Type /ObjStm compressed-object-streams."""
    for val in list(objs.values()):
        if not isinstance(val, StreamVal):
            continue
        d = val.dict
        if d.get("Type") != Name("ObjStm"):
            continue
        try:
            first = int(d.get("First", 0))
            n = int(d.get("N", 0))
        except (ValueError, TypeError):
            continue
        try:
            content = val.decoded()
        except Exception:
            continue
        header = content[:first].split()
        try:
            pairs = [(int(header[2 * j]), int(header[2 * j + 1])) for j in range(n)]
        except (ValueError, IndexError):
            continue
        for objnum, off in pairs:
            if objnum in objs:
                continue
            later = [o for o, o2 in pairs if o2 > off]
            end_abs = first + min(later) if later else len(content)
            chunk = content[first + off:end_abs]
            try:
                v = _Parser(chunk).parse()
            except Exception:
                v = None
            if v is not None:
                objs.setdefault(objnum, v)


@dataclass
class RawPage:
    index: int  # 1-based document order
    src_num: int
    mediabox: tuple[float, float, float, float]
    resources: object  # resolved Resources dict (may contain inner Refs)
    content: bytes     # concatenated, decoded content streams


@dataclass
class PdfDoc:
    pages: list[RawPage] = field(default_factory=list)
    objects: dict[int, object] = field(default_factory=dict)
    has_encrypt: bool = False


def load_pdf(data: bytes) -> PdfDoc:
    if not data.startswith(b"%PDF-"):
        idx = data.find(b"%PDF-")
        if idx < 0:
            raise PdfError(
                "This file does not look like a PDF at all. Re-export your "
                "interior from your word processor or layout tool, then try again."
            )
        data = data[idx:]

    objs = _scan_objects(data)
    _expand_objstms(objs)
    doc = PdfDoc(objects=objs, has_encrypt=bool(re.search(rb"/Encrypt\b", data)))

    if doc.has_encrypt:
        raise PdfError(
            "This PDF is encrypted or password-protected. KDP interiors must "
            "be uploaded without security settings - re-export without any "
            "protection enabled."
        )

    # Locate the page tree (via the catalog when possible).
    catalog = None
    pages_root = None
    for val in objs.values():
        if isinstance(val, dict) and val.get("Type") == Name("Catalog"):
            catalog = val
            break
    if catalog is not None:
        pr = catalog.get("Pages")
        if isinstance(pr, Ref):
            pages_root = objs.get(pr.num)
        elif isinstance(pr, dict):
            pages_root = pr
    if pages_root is None:
        for val in objs.values():
            if isinstance(val, dict) and val.get("Type") == Name("Pages"):
                pages_root = val
                break

    ordered: list[dict] = []

    def walk(node, depth: int = 0):
        if depth > 128 or not isinstance(node, dict):
            return
        kids = node.get("Kids")
        if isinstance(kids, Ref):
            kids = objs.get(kids.num)
        if isinstance(kids, list):
            for k in kids:
                kd = objs.get(k.num) if isinstance(k, Ref) else k
                if isinstance(kd, dict):
                    if isinstance(k, Ref):
                        kd = dict(kd)
                        kd["__num"] = k.num
                    kd["__parent"] = node
                    walk(kd, depth + 1)
            return
        t = node.get("Type")
        if isinstance(t, Ref):
            t = objs.get(t.num)
        if t == Name("Page"):
            ordered.append(node)

    walk(pages_root)

    if not ordered:
        # Last resort: any object typed /Page, ordered by object number.
        found = []
        for num, val in objs.items():
            if isinstance(val, dict) and val.get("Type") == Name("Page"):
                v = dict(val)
                v["__num"] = num
                found.append((num, v))
        found.sort()
        ordered = [v for _, v in found]

    page_index = 0
    for pd in ordered:
        page_index += 1
        num = pd.get("__num", -1)

        # MediaBox / Resources: inherit up the parent chain.
        cur = pd
        box, res = None, None
        depth = 0
        while cur is not None and depth < 64:
            if box is None and "MediaBox" in cur:
                box = cur.get("MediaBox")
            if res is None and "Resources" in cur:
                r = cur.get("Resources")
                if isinstance(r, Ref):
                    r = objs.get(r.num)
                if isinstance(r, dict):
                    res = r
            cur = cur.get("__parent")
            depth += 1
        if box is None or not isinstance(box, list):
            box = [0.0, 0.0, 612.0, 792.0]
        nums = []
        for v in box:
            if isinstance(v, Ref):
                v = objs.get(v.num)
            if isinstance(v, (int, float)):
                nums.append(float(v))
        if len(nums) < 4:
            nums = [0.0, 0.0, 612.0, 792.0]

        contents = pd.get("Contents")
        if isinstance(contents, Ref):
            contents = objs.get(contents.num)
        parts: list[bytes] = []
        if isinstance(contents, StreamVal):
            parts.append(contents.decoded())
        elif isinstance(contents, list):
            for c in contents:
                if isinstance(c, Ref):
                    c = objs.get(c.num)
                if isinstance(c, StreamVal):
                    parts.append(c.decoded())
        content = b"".join(parts)

        doc.pages.append(
            RawPage(
                index=page_index,
                src_num=num,
                mediabox=tuple(nums[:4]),
                resources=res if isinstance(res, dict) else {},
                content=content,
            )
        )
    return doc


def resolve_ref(objs: dict[int, object], thing):
    """One-step reference resolution helper used by the content engine."""
    while isinstance(thing, Ref):
        thing = objs.get(thing.num)
    return thing
