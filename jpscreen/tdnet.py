"""TDnet timely disclosures: lists (via the free yanoshin mirror, with a direct TDnet fallback),
document text (PDF) and earnings-release XBRL.

TDnet itself only keeps documents for ~31 days, so the scan reads documents the day they come out and
caches everything it parsed.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .http import Http, HttpError
from .util import nfkc, norm_code, parse_date
from .xbrl import FactSet, parse_zip

log = logging.getLogger(__name__)

YANOSHIN = "https://webapi.yanoshin.jp/webapi/tdnet/list/{cond}.json"
TDNET_DAY = "https://www.release.tdnet.info/inbs/I_list_{page:03d}_{ymd}.html"
TDNET_BASE = "https://www.release.tdnet.info/inbs/"


@dataclass
class Disclosure:
    id: str
    time: dt.datetime
    code: str
    company: str
    title: str
    pdf_url: str
    xbrl_url: Optional[str] = None
    markets: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def date(self) -> dt.date:
        return self.time.date()

    @property
    def ymd(self) -> str:
        return self.time.strftime("%Y%m%d")


def _strip_redirect(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"rd\.php\?(https?://.+)$", url)
    return m.group(1) if m else url


def _from_yanoshin(item: dict) -> Optional[Disclosure]:
    t = item.get("Tdnet", item)
    try:
        when = dt.datetime.strptime(t["pubdate"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, ValueError):
        return None
    return Disclosure(
        id=str(t.get("id")), time=when, code=norm_code(t.get("company_code")),
        company=nfkc(t.get("company_name") or "").strip(), title=nfkc(t.get("title") or "").strip(),
        pdf_url=_strip_redirect(t.get("document_url")) or "", xbrl_url=_strip_redirect(t.get("url_xbrl")),
        markets=t.get("markets_string") or "",
    )


class TDnet:
    def __init__(self, http: Http):
        self.http = http

    # ------------------------------------------------------------------ lists
    def list_range(self, start: dt.date, end: dt.date, ttl: Optional[float] = None) -> list[Disclosure]:
        """All disclosures between start and end (inclusive), newest first."""
        out: list[Disclosure] = []
        # one call per day keeps each response small and lets past days be cached forever
        d = end
        today = dt.date.today()
        while d >= start:
            day_ttl = ttl if ttl is not None else (900 if (today - d).days <= 2 else None)
            out.extend(self.list_day(d, ttl=day_ttl))
            d -= dt.timedelta(days=1)
        seen, uniq = set(), []
        for x in out:
            if x.id in seen:
                continue
            seen.add(x.id)
            uniq.append(x)
        return sorted(uniq, key=lambda x: x.time, reverse=True)

    def list_day(self, day: dt.date, ttl: Optional[float] = None) -> list[Disclosure]:
        ymd = day.strftime("%Y%m%d")
        try:
            js = self.http.get_json(YANOSHIN.format(cond=ymd), params={"limit": 10000}, ttl=ttl)
            items = [_from_yanoshin(i) for i in (js or {}).get("items", [])]
            items = [i for i in items if i]
            if items or day.weekday() >= 5:
                return items
        except (HttpError, ValueError) as e:
            log.warning("yanoshin %s failed (%s); falling back to TDnet", ymd, e)
        return self._list_day_direct(day, ttl=ttl)

    def list_company(self, code: str, limit: int = 300, ttl: float = 6 * 3600) -> list[Disclosure]:
        """Disclosure history of one company (years back; documents older than ~31 days may be gone)."""
        js = self.http.get_json(YANOSHIN.format(cond=norm_code(code)), params={"limit": limit}, ttl=ttl)
        items = [_from_yanoshin(i) for i in (js or {}).get("items", [])]
        return [i for i in items if i]

    def prefetch_companies(self, codes: list[str], limit: int = 300, workers: int = 4) -> None:
        from concurrent.futures import ThreadPoolExecutor
        h0 = self.http

        def work(code):
            try:
                TDnet(Http(cache_dir=h0.cache_dir, min_interval=h0.min_interval, offline=h0.offline)).list_company(code, limit)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, codes))

    def _list_day_direct(self, day: dt.date, ttl: Optional[float] = None) -> list[Disclosure]:
        """Scrape TDnet's own daily list pages (only the last ~31 days exist)."""
        ymd = day.strftime("%Y%m%d")
        out: list[Disclosure] = []
        for page in range(1, 60):
            html = self.http.get_text(TDNET_DAY.format(page=page, ymd=ymd), ttl=ttl, allow_404=True)
            if not html:
                break
            rows = re.findall(r"<tr>(.*?)</tr>", html, re.S)
            n = 0
            for row in rows:
                cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
                if len(cells) < 4:
                    continue
                clean = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
                m = re.search(r'href="([^"]+\.pdf)"', row)
                if not m or not re.match(r"\d{2}:\d{2}", clean[0]):
                    continue
                hh, mm = clean[0].split(":")
                xm = re.search(r'href="([^"]+\.zip)"', row)
                out.append(Disclosure(
                    id=Path(m.group(1)).stem, time=dt.datetime(day.year, day.month, day.day, int(hh), int(mm)),
                    code=norm_code(clean[1]), company=nfkc(clean[2]), title=nfkc(re.sub(r"\s+", " ", clean[3])),
                    pdf_url=TDNET_BASE + m.group(1), xbrl_url=(TDNET_BASE + xm.group(1)) if xm else None,
                    markets=clean[5] if len(clean) > 5 else ""))
                n += 1
            if n == 0:
                break
        return out

    # ------------------------------------------------------------------ documents
    def pdf_text(self, d: Disclosure) -> Optional[str]:
        """Text of the disclosure PDF (layout-preserving), or None once TDnet has deleted it."""
        if not d.pdf_url:
            return None
        try:
            data = self.http.get(d.pdf_url, allow_404=True)
        except HttpError as e:
            log.info("pdf %s: %s", d.pdf_url, e)
            return None
        if not data or not data.startswith(b"%PDF"):
            return None
        return pdf_bytes_to_text(data)

    def prefetch(self, urls: list[str], workers: int = 4) -> None:
        """Download documents into the cache in parallel; parsing stays sequential."""
        from concurrent.futures import ThreadPoolExecutor
        h0 = self.http

        def work(url):
            try:
                Http(cache_dir=h0.cache_dir, min_interval=h0.min_interval, offline=h0.offline).get(url, allow_404=True)
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(work, [u for u in urls if u]))

    def xbrl_facts(self, d: Disclosure) -> Optional[FactSet]:
        if not d.xbrl_url:
            return None
        try:
            data = self.http.get(d.xbrl_url, allow_404=True)
        except HttpError:
            return None
        if not data or not data.startswith(b"PK"):
            return None
        try:
            return parse_zip(data, prefer="inline")
        except Exception as e:  # malformed package: skip, never fail the scan
            log.info("xbrl %s: %s", d.xbrl_url, e)
            return None


def pdf_bytes_to_text(data: bytes) -> str:
    """pdftotext -layout when available (keeps table rows on one line); pypdf otherwise."""
    if shutil.which("pdftotext"):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "d.pdf"
            src.write_bytes(data)
            try:
                res = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(src), "-"],
                                     capture_output=True, timeout=120)
                if res.returncode == 0 and res.stdout:
                    return nfkc(res.stdout.decode("utf-8", errors="replace"))
            except subprocess.TimeoutExpired:
                pass
    try:
        import io

        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return nfkc("\n".join((p.extract_text() or "") for p in reader.pages))
    except Exception:
        return ""


def iter_codes_in_title(title: str) -> Iterable[str]:
    """Securities codes quoted in a title: '（証券コード：4714）' -> '4714'."""
    for m in re.finditer(r"(?:証券)?コード\s*[:：]?\s*([0-9][0-9A-Z]{3})", nfkc(title)):
        yield norm_code(m.group(1))


def disclosure_from_dict(d: dict) -> Disclosure:
    return Disclosure(id=d["id"], time=dt.datetime.fromisoformat(d["time"]), code=d["code"], company=d["company"],
                      title=d["title"], pdf_url=d.get("pdf_url", ""), xbrl_url=d.get("xbrl_url"),
                      markets=d.get("markets", ""))


def disclosure_to_dict(d: Disclosure) -> dict:
    return dict(id=d.id, time=d.time.isoformat(), code=d.code, company=d.company, title=d.title,
                pdf_url=d.pdf_url, xbrl_url=d.xbrl_url, markets=d.markets)


def parse_pubdate(s: str) -> Optional[dt.date]:
    return parse_date(s)
