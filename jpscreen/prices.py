"""Prices, market caps and liquidity.

Default source: Yahoo! Finance Japan quote + history pages (no key; covers TSE and the Nagoya/Fukuoka/
Sapporo-only listings via .N/.F/.S symbols). J-Quants can be plugged in for whole-market daily closes.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .http import Http, HttpError
from .util import nfkc, norm_code, parse_jp_number

log = logging.getLogger(__name__)

YJ_QUOTE = "https://finance.yahoo.co.jp/quote/{sym}"
YJ_HISTORY = "https://finance.yahoo.co.jp/quote/{sym}/history"


@dataclass
class Quote:
    code: str
    symbol: str = ""
    name: str = ""
    price: Optional[float] = None        # latest close (or last trade)
    price_date: Optional[str] = None
    mcap_issued: Optional[float] = None  # yen, on issued shares incl. treasury (Yahoo's 時価総額)
    shares_issued: Optional[float] = None
    pbr: Optional[float] = None
    per: Optional[float] = None
    eps: Optional[float] = None          # company forecast
    bps: Optional[float] = None
    div_yield: Optional[float] = None    # % company forecast
    dps: Optional[float] = None
    adv: Optional[float] = None          # average daily trading value, yen (last ~20 sessions)
    days_traded: Optional[int] = None
    year_high: Optional[float] = None
    year_low: Optional[float] = None
    next_results: Optional[str] = None
    market_label: str = ""
    history: list = field(default_factory=list)  # [(date, close, volume)]

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("history", None)
        return d


def _fields(html: str) -> dict:
    s = html.replace('\\"', '"')
    out = {}
    for m in re.finditer(r'"(\w+)":\{"name":"([^"]+)"(?:,"subText":"[^"]*")?,"value":"([^"]*)"', s):
        out.setdefault(m.group(1), m.group(3))
    return out


def _num(v: Optional[str]) -> Optional[float]:
    if v is None or v.strip() in ("", "---", "-"):
        return None
    return parse_jp_number(v)


def parse_quote_page(code: str, sym: str, html: str) -> Optional[Quote]:
    f = _fields(html)
    if not f.get("previousPrice") and not f.get("totalPrice"):
        return None
    s = html.replace('\\"', '"')
    q = Quote(code=code, symbol=sym)
    m = re.search(r'"name":"([^"]+?)【' + re.escape(sym) + r'】"', s)
    q.name = nfkc(m.group(1)) if m else ""
    m = re.search(r'"price":\{"value":"([^"]+)"', s)
    last = _num(m.group(1)) if m else None
    q.price = last if last else _num(f.get("previousPrice"))
    mc = _num(f.get("totalPrice"))
    q.mcap_issued = mc * 1e6 if mc is not None else None
    q.shares_issued = _num(f.get("sharesIssued"))
    q.pbr, q.per = _num(f.get("pbr")), _num(f.get("per"))
    q.eps, q.bps = _num(f.get("eps")), _num(f.get("bps"))
    q.div_yield, q.dps = _num(f.get("dividendYield")), _num(f.get("dps"))
    q.year_high, q.year_low = _num(f.get("yearHighPrice")), _num(f.get("yearLowPrice"))
    m = re.search(r'"pressReleaseDate":"([^"]+)"', s)
    q.next_results = m.group(1) if m else None
    m = re.search(r'_PriceBoardMenu__label[^>]*>([^<]+)<', html)
    q.market_label = m.group(1) if m else ""
    return q


def parse_history_page(html: str, adjusted: bool = False) -> list[tuple[str, float, float]]:
    """(date, close, volume) rows of the daily price table only (the page also carries margin-balance tables).
    adjusted=True takes the split-adjusted close (the row's sixth value), for returns across splits."""
    s = html.replace('\\"', '"')
    i = s.find('"histories":[')
    if i < 0:
        return []
    try:
        arr, _ = json.JSONDecoder().raw_decode(s[i + len('"histories":'):])
    except ValueError:
        return []
    out = []
    for row in arr if isinstance(arr, list) else []:
        vals = [v.get("value") for v in (row.get("values") or []) if isinstance(v, dict)]
        if not re.match(r"\d{4}-\d{2}-\d{2}$", str(row.get("date") or "")) or len(vals) < 5:
            continue
        close, vol = _num(vals[5] if adjusted and len(vals) > 5 else vals[3]), _num(vals[4])
        if close is not None:
            out.append((row["date"], close, vol or 0.0))
    return out


class YahooJapan:
    def __init__(self, http: Http, ttl: float = 6 * 3600):
        self.http = http
        self.ttl = ttl

    def quote(self, code: str, symbols: Optional[Iterable[str]] = None, history: bool = True) -> Optional[Quote]:
        code = norm_code(code)
        for sym in (symbols or [f"{code}.T", f"{code}.N", f"{code}.F", f"{code}.S"]):
            try:
                html = self.http.get_text(YJ_QUOTE.format(sym=sym), ttl=self.ttl, allow_404=True)
            except HttpError as e:
                # Yahoo's quote page fails intermittently (HTTP 500) while the history page still answers:
                # price, date and liquidity then come from the history; shares and valuation from the bulk ranking
                log.info("quote %s: %s", sym, e)
                html = None
                if history and e.status >= 500:
                    q = Quote(code=code, symbol=sym)
                    try:
                        self._add_history(q, sym)
                    except HttpError:
                        pass
                    if q.history:
                        return q
                continue
            if not html:
                continue
            q = parse_quote_page(code, sym, html)
            if not q:
                continue
            if history:
                self._add_history(q, sym)
            return q
        return None

    def _add_history(self, q: Quote, sym: str) -> None:
        try:
            h = self.http.get_text(YJ_HISTORY.format(sym=sym), ttl=self.ttl, allow_404=True)
            rows = parse_history_page(h or "")
        except HttpError:
            rows = []
        q.history = rows
        if rows:
            q.price_date = rows[0][0]
            if not q.price:
                q.price = rows[0][1]
            vals = [c * v for _, c, v in rows[:20]]
            q.adv = sum(vals) / len(vals) if vals else None
            q.days_traded = sum(1 for _, _, v in rows[:20] if v > 0)


YJ_RANKING = "https://finance.yahoo.co.jp/stocks/ranking/marketCapitalHigh"


def parse_ranking_page(html: str) -> list[dict]:
    s = html.replace('\\"', '"')
    out = []
    for m in re.finditer(r'\{"rank":"(\d+)","stockCode":"([0-9A-Z]{4})","marketName":"([^"]*)","stockName":"([^"]*)",'
                         r'"detailLink":"[^"]*","bbsUrl":"[^"]*","savePrice":"([^"]*)","date":"[^"]*",'
                         r'"rankingResult":\{.*?"totalPriceObj":\{"updateDateTime":"([^"]*)","sharesIssued":"([^"]*)","totalPrice":"([^"]*)"', s):
        out.append(dict(code=m.group(2), market=m.group(3), name=nfkc(m.group(4)), price=_num(m.group(5)),
                        asof=m.group(6), shares=_num(m.group(7)), mcap=(_num(m.group(8)) or 0) * 1e6 or None))
    return out


def bulk_prices(http: Http, ttl: float = 6 * 3600, max_pages: int = 120) -> dict[str, dict]:
    """Latest price, shares issued and market cap for every listed stock from Yahoo's market-cap ranking (50 a page)."""
    out: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        try:
            html = http.get_text(YJ_RANKING, params={"market": "all", "term": "daily", "page": page}, ttl=ttl)
        except HttpError as e:
            log.warning("ranking page %s: %s", page, e)
            break
        rows = parse_ranking_page(html or "")
        if not rows:
            break
        for r in rows:
            out.setdefault(r["code"], r)
    return out


class PriceBook:
    """Quotes keyed by code, persisted between runs (prices refresh after `ttl`)."""

    def __init__(self, http: Http, path: str | Path = ".cache/prices.json", ttl: float = 6 * 3600):
        self.yj = YahooJapan(http, ttl=ttl)
        self.path = Path(path)
        self.quotes: dict[str, Quote] = {}
        if self.path.exists():
            try:
                for k, v in json.loads(self.path.read_text()).items():
                    self.quotes[k] = Quote(**v)
            except Exception:
                self.quotes = {}

    def get(self, code: str, symbols: Optional[list[str]] = None, refresh: bool = True) -> Optional[Quote]:
        code = norm_code(code)
        if refresh or code not in self.quotes:
            q = self.yj.quote(code, symbols)
            if q:
                self._put(code, q)
        return self.quotes.get(code)

    def _put(self, code: str, q: Quote) -> None:
        """Store a fresh quote; what the quote page did not give (it failed, or the history page answered alone) is
        filled from the previous quote: names and share counts as they were, per-share ratios re-priced."""
        old = self.quotes.get(code)
        if old is not None and q.shares_issued is None:
            for k in ("name", "shares_issued", "market_label", "eps", "bps", "dps", "year_high", "year_low", "next_results"):
                if getattr(q, k) in (None, "") and getattr(old, k) not in (None, ""):
                    setattr(q, k, getattr(old, k))
            if q.price:
                if q.shares_issued:
                    q.mcap_issued = q.price * q.shares_issued
                q.per = round(q.price / q.eps, 2) if q.eps and q.eps > 0 else q.per
                q.pbr = round(q.price / q.bps, 2) if q.bps and q.bps > 0 else q.pbr
                q.div_yield = round(q.dps / q.price * 100, 2) if q.dps else q.div_yield
                q.year_high = max(q.year_high or 0, q.price) or None
                q.year_low = min(q.year_low or q.price, q.price)
        self.quotes[code] = q

    def prefetch(self, wanted: dict[str, list[str]], workers: int = 4) -> None:
        """Fetch many quotes in parallel (each worker has its own session; responses share the disk cache)."""
        from concurrent.futures import ThreadPoolExecutor
        todo = {norm_code(c): syms for c, syms in wanted.items()}
        if not todo:
            return
        http0 = self.yj.http

        def work(item):
            code, syms = item
            yj = YahooJapan(Http(cache_dir=http0.cache_dir, min_interval=http0.min_interval, offline=http0.offline,
                                 retries=2), ttl=self.yj.ttl)
            try:
                return code, yj.quote(code, syms)
            except Exception:
                return code, None

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for code, q in ex.map(work, todo.items()):
                if q:
                    self._put(code, q)

    def load_bulk(self, http: Http) -> int:
        """Fill quotes for every listed stock (price, shares, market cap; no history) from the ranking pages."""
        n = 0
        for code, r in bulk_prices(http).items():
            if code in self.quotes and self.quotes[code].history:
                continue      # a full quote (with trading history) is already here
            if r.get("price"):
                d = re.search(r"(\d{4})/(\d{2})/(\d{2})", r.get("asof") or "")
                self.quotes[code] = Quote(code=code, symbol=f"{code}.T", name=r["name"], price=r["price"],
                                          price_date=f"{d.group(1)}-{d.group(2)}-{d.group(3)}" if d else None,
                                          mcap_issued=r.get("mcap"), shares_issued=r.get("shares"), market_label=r.get("market") or "")
                n += 1
        return n

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({k: v.to_dict() for k, v in self.quotes.items()}, ensure_ascii=False))

    @property
    def asof(self) -> Optional[str]:
        dates = sorted(q.price_date for q in self.quotes.values() if q.price_date)
        return dates[-1] if dates else None
