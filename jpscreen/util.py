"""Small helpers shared across the package: Japanese number parsing, code normalisation, dates."""
from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata
from typing import Iterable, Optional

JST = dt.timezone(dt.timedelta(hours=9))
OKU = 100_000_000  # 1億円


def nfkc(s: Optional[str]) -> str:
    """Normalise full-width digits/letters/spaces so regexes can be written in ASCII."""
    if s is None:
        return ""
    return unicodedata.normalize("NFKC", str(s))


_NUM_RE = re.compile(r"[△▲\-−–]?\s*\(?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s*\)?")


def parse_jp_number(text: Optional[str]) -> Optional[float]:
    """Parse '1,234', '△1,234', '▲12.5', '(1,234)', '－' -> float or None.

    △ / ▲ / parentheses denote negatives in Japanese filings. Dashes on their own mean nil.
    """
    if text is None:
        return None
    s = nfkc(text).strip()
    if not s or s in {"-", "―", "—", "－", "‐", "–", "─"}:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    tok = m.group(0)
    neg = tok.lstrip().startswith(("△", "▲", "-", "−", "–")) or ("(" in tok and ")" in tok)
    digits = re.sub(r"[^0-9.]", "", tok)
    if not digits or digits == ".":
        return None
    val = float(digits)
    return -val if neg else val


def all_jp_numbers(text: str) -> list[float]:
    out = []
    for m in _NUM_RE.finditer(nfkc(text)):
        v = parse_jp_number(m.group(0))
        if v is not None:
            out.append(v)
    return out


def norm_code(code) -> str:
    """Normalise a securities code to the 4-character form ('4624', '130A').

    TDnet/EDINET use a 5-digit form with a trailing check digit '0' ('46240').
    """
    if code is None:
        return ""
    if isinstance(code, float):
        if math.isnan(code):
            return ""
        code = str(int(code))
    s = nfkc(str(code)).strip().upper()
    s = re.sub(r"\.0$", "", s)
    if len(s) == 5 and s.endswith("0"):
        s = s[:4]
    return s


def today_jst() -> dt.date:
    return dt.datetime.now(JST).date()


def parse_date(s) -> Optional[dt.date]:
    if s is None:
        return None
    if isinstance(s, dt.datetime):
        return s.date()
    if isinstance(s, dt.date):
        return s
    t = nfkc(str(s)).strip()
    m = re.search(r"(\d{4})[-/年\.]\s*(\d{1,2})[-/月\.]\s*(\d{1,2})", t)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def daterange(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def safe_div(a, b) -> Optional[float]:
    try:
        if a is None or b is None or b == 0:
            return None
        if isinstance(a, float) and math.isnan(a):
            return None
        if isinstance(b, float) and math.isnan(b):
            return None
        return a / b
    except TypeError:
        return None


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def lerp_score(x: Optional[float], points: list[tuple[float, float]]) -> Optional[float]:
    """Piecewise-linear map of x through (x, score) breakpoints sorted by x."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def to_oku(yen: Optional[float]) -> Optional[float]:
    return None if yen is None else yen / OKU
