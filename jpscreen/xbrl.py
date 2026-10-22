"""Minimal XBRL readers for EDINET instance files (.xbrl) and inline XBRL (EDINET/TDnet *.htm).

Only what the screen needs: numeric facts (already scaled to yen) and text blocks (raw HTML).
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from lxml import etree

from .util import parse_jp_number

IX_NS = {"http://www.xbrl.org/2013/inlineXBRL", "http://www.xbrl.org/2008/inlineXBRL"}
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


@dataclass
class Fact:
    name: str            # prefix:local
    context: str
    value: Optional[float] = None   # numeric facts, in base units (yen, shares, %)
    text: Optional[str] = None      # non-numeric facts; text blocks keep their HTML
    unit: Optional[str] = None
    decimals: Optional[str] = None

    @property
    def local(self) -> str:
        return self.name.split(":", 1)[-1]

    @property
    def prefix(self) -> str:
        return self.name.split(":", 1)[0] if ":" in self.name else ""

    @property
    def is_numeric(self) -> bool:
        return self.value is not None


CURRENT_INSTANT = re.compile(r"^(Current\w*Instant|InterimInstant|CurrentAccumulatedQ\dInstant)$")
CURRENT_DURATION = re.compile(r"^(CurrentYearDuration|CurrentYTDDuration|InterimDuration|"
                              r"CurrentAccumulatedQ\dDuration|CurrentQuarterDuration)$")
PRIOR_DURATION = re.compile(r"^(Prior1YearDuration|Prior1YTDDuration|PriorAccumulatedQ\dDuration|Prior1InterimDuration)$")


def split_context(ctx: str) -> tuple[str, list[str]]:
    """'CurrentYearInstant_NonConsolidatedMember' -> ('CurrentYearInstant', ['NonConsolidatedMember'])."""
    parts = ctx.split("_")
    return parts[0], parts[1:]


class FactSet:
    def __init__(self, facts: Iterable[Fact]):
        self.facts = list(facts)
        self.by_local: dict[str, list[Fact]] = {}
        for f in self.facts:
            self.by_local.setdefault(f.local, []).append(f)

    def __len__(self) -> int:
        return len(self.facts)

    def extend(self, other: "FactSet") -> None:
        for f in other.facts:
            self.facts.append(f)
            self.by_local.setdefault(f.local, []).append(f)

    def find(self, local: str, pred: Optional[Callable[[str], bool]] = None) -> list[Fact]:
        out = self.by_local.get(local, [])
        return [f for f in out if pred is None or pred(f.context)]

    def num(self, local: str, pred: Optional[Callable[[str], bool]] = None) -> Optional[float]:
        for f in self.find(local, pred):
            if f.value is not None:
                return f.value
        return None

    def text(self, local: str, pred: Optional[Callable[[str], bool]] = None) -> Optional[str]:
        for f in self.find(local, pred):
            if f.text:
                return f.text
        return None

    def matching(self, regex: str) -> list[Fact]:
        rx = re.compile(regex)
        return [f for f in self.facts if rx.search(f.local)]

    def text_blocks(self) -> list[Fact]:
        return [f for f in self.facts if f.local.endswith("TextBlock") and f.text]


# ---------------------------------------------------------------- instance (.xbrl)

def parse_instance(data: bytes) -> FactSet:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    root = etree.fromstring(data, parser=parser)
    facts: list[Fact] = []
    for el in root:
        if not isinstance(el.tag, str):
            continue
        ctx = el.get("contextRef")
        if not ctx:
            continue
        local = etree.QName(el).localname
        prefix = el.prefix or ""
        name = f"{prefix}:{local}" if prefix else local
        if el.get(XSI_NIL) == "true":
            facts.append(Fact(name, ctx))
            continue
        raw = el.text or ""
        unit = el.get("unitRef")
        if unit is not None:
            try:
                val = float(raw.strip())
            except ValueError:
                val = parse_jp_number(raw)
            facts.append(Fact(name, ctx, value=val, unit=unit, decimals=el.get("decimals")))
        else:
            facts.append(Fact(name, ctx, text=raw))
    return FactSet(facts)


# ---------------------------------------------------------------- inline XBRL (*.htm)

def _inner_html(el) -> str:
    parts = [el.text or ""]
    for child in el:
        parts.append(etree.tostring(child, encoding="unicode", with_tail=True))
    html = "".join(parts)
    # Drop namespace prefixes so the fragment parses as plain HTML later.
    return re.sub(r"</?ix:[^>]*>", "", html)


def parse_inline(data: bytes) -> FactSet:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False)
    root = etree.fromstring(data, parser=parser)
    if root is None:
        return FactSet([])
    continuations = {}
    for el in root.iter():
        if isinstance(el.tag, str) and etree.QName(el).namespace in IX_NS and etree.QName(el).localname == "continuation":
            continuations[el.get("id")] = el

    facts: list[Fact] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        q = etree.QName(el)
        if q.namespace not in IX_NS:
            continue
        name = el.get("name")
        ctx = el.get("contextRef")
        if not name or not ctx:
            continue
        if q.localname == "nonFraction":
            if el.get(XSI_NIL) == "true":
                facts.append(Fact(name, ctx))
                continue
            txt = "".join(el.itertext()).strip()
            fmt = el.get("format") or ""
            if "zero" in fmt or txt in {"-", "－", "―", "—"}:
                val = 0.0
            else:
                val = parse_jp_number(txt)
                if val is not None:
                    val = abs(val)
            if val is not None:
                scale = int(el.get("scale") or 0)
                val = val * (10 ** scale)
                if el.get("sign") == "-":
                    val = -val
            facts.append(Fact(name, ctx, value=val, unit=el.get("unitRef"), decimals=el.get("decimals")))
        elif q.localname == "nonNumeric":
            html = _inner_html(el)
            nxt = el.get("continuedAt")
            seen = set()
            while nxt and nxt in continuations and nxt not in seen:
                seen.add(nxt)
                c = continuations[nxt]
                html += _inner_html(c)
                nxt = c.get("continuedAt")
            facts.append(Fact(name, ctx, text=html))
    return FactSet(facts)


# ---------------------------------------------------------------- zip packages

def parse_zip(data: bytes, prefer: str = "instance") -> FactSet:
    """Parse an EDINET (XBRL/PublicDoc/*.xbrl) or TDnet (XBRLData/**.htm) package."""
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    instances = [n for n in names if n.lower().endswith(".xbrl") and "publicdoc" in n.lower()]
    if prefer == "instance" and instances:
        fs = FactSet([])
        for n in instances:
            fs.extend(parse_instance(zf.read(n)))
        return fs
    fs = FactSet([])
    for n in sorted(names):
        low = n.lower()
        if low.endswith(".htm") and "ixbrl" in low:
            fs.extend(parse_inline(zf.read(n)))
    if len(fs) == 0 and instances:
        for n in instances:
            fs.extend(parse_instance(zf.read(n)))
    return fs
