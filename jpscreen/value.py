"""Whole-market value screen (the Japanese version of the Korean 'Value watchlist').

Lists (a stock can be on several):
  re_netnet   不動産込みネットネット指数 < 1: market cap below net current assets + real estate (the cheapness tab)
  net_net     current assets - ALL liabilities above market cap (Graham)
  cash_rich   cash + securities + investment securities - all liabilities >= 70% of market cap, profitable
  deep_value  P/E <= 6 and P/B <= 0.6, profitable in at least 4 of the last 5 years
  treasury    treasury shares >= 10% of issued at P/B <= 1 (cancellation candidates)
  parent_sub  listed subsidiaries (a corporate holder >= 50%) at P/B <= 1 (parent buy-out candidates)

RE net-net index (exactly the reference ranking's definition):
  index = market cap / (net current assets + rental real-estate fair value + non-overlapping land book x 50%)
  net current assets = cash + trade receivables + securities + investment securities
                       - total liabilities - non-controlling interests      (inventories excluded)
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .fundamentals import FINANCIAL_HOLDER, SELF_HELD, TRUST_BANKS, Fundamentals
from .prices import Quote
from .universe import Company
from .util import OKU, norm_code, safe_div

CRITERIA = dict(
    re_index_max=1.0,
    land_haircut=0.5,
    cash_rich_min=0.7,
    deep_pe_max=6.0,
    deep_pb_max=0.6,
    deep_profit_years=4,     # of the last 5
    treasury_min=10.0,
    treasury_pb_max=1.0,
    parent_min=50.0,
    parent_pb_max=1.0,
    list_max=150,
    market_cap_basis="ex_treasury",   # or "issued"
)

WATCH_LABEL = {"re_netnet": "RE net-net", "net_net": "net-net", "cash_rich": "cash-rich", "deep_value": "deep value",
               "treasury": "treasury pile", "parent_sub": "listed subsidiary"}


@dataclass
class ValueRow:
    code: str
    name: str
    market: str
    sector: str = ""
    price: Optional[float] = None
    price_date: Optional[str] = None
    mcap: Optional[float] = None
    nca: Optional[float] = None              # 正味流動資産 (screen definition)
    real_estate: Optional[float] = None      # rental fair value + 50% non-overlapping land
    re_index: Optional[float] = None
    ncav: Optional[float] = None             # current assets - liabilities
    ncav_mcap: Optional[float] = None
    am: Optional[float] = None               # cash + securities + inv. securities - liabilities
    am_mcap: Optional[float] = None
    pe: Optional[float] = None
    pb: Optional[float] = None
    treasury_pct: Optional[float] = None
    profit_years: Optional[int] = None
    years: Optional[int] = None
    ni: Optional[float] = None
    op: Optional[float] = None
    top_holder: Optional[str] = None
    top_holder_pct: Optional[float] = None
    adv: Optional[float] = None
    source: str = ""
    supplementary_mcap: bool = False
    lists: list = field(default_factory=list)
    flags: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in list(d.items()):
            if isinstance(v, float):
                d[k] = round(v, 4) if abs(v) < 1000 else round(v)
        return d


def market_cap(f: Optional[Fundamentals], q: Optional[Quote], basis: str = "ex_treasury") -> Optional[float]:
    if q is None or q.price is None:
        return None
    issued = (f.shares_issued if f and f.shares_issued else None) or q.shares_issued
    if issued is None:
        return q.mcap_issued
    treasury = (f.treasury_shares or 0) if (f and basis == "ex_treasury") else 0
    return q.price * max(issued - treasury, 0)


def net_current_assets(f: Fundamentals) -> Optional[float]:
    if f.liabilities is None:
        return None
    parts = [f.cash, f.receivables, f.securities, f.investment_securities]
    if all(p is None for p in parts):
        return None
    return sum(p or 0 for p in parts) - f.liabilities - (f.nci or 0)


def real_estate_value(f: Fundamentals, land_haircut: float = 0.5) -> float:
    fv = f.rental_fair_value or 0.0
    overlap = f.rental_book_value if f.rental_book_value is not None else fv
    land = max((f.land or 0.0) - (overlap or 0.0), 0.0)
    return fv + land_haircut * land


def top_corporate_holder(f: Fundamentals) -> tuple[Optional[str], Optional[float]]:
    for h in f.major_holders:
        nm = h.get("name") or ""
        if TRUST_BANKS.search(nm) or SELF_HELD.search(nm):
            continue
        return nm, h.get("pct")
    return None, None


def is_corporate(name: str) -> bool:
    return bool(name) and any(x in name for x in ("株式会社", "(株)", "㈱", "有限会社", "合同会社", "Co.", "Ltd", "Inc",
                                                   "LIMITED", "CORPORATION", "ホールディングス"))


def build_row(c: Company, f: Optional[Fundamentals], q: Optional[Quote], crit: dict = CRITERIA) -> ValueRow:
    r = ValueRow(code=c.code, name=c.name, market=c.market, sector=c.sector)
    if q:
        r.price, r.price_date, r.adv = q.price, q.price_date, q.adv
    r.mcap = market_cap(f, q, crit["market_cap_basis"])
    if f is None:
        return r
    r.source = f.source
    r.nca = net_current_assets(f)
    r.real_estate = real_estate_value(f, crit["land_haircut"])
    denom = (r.nca or 0) + (r.real_estate or 0) if r.nca is not None else None
    if r.mcap and denom is not None and denom > 0:
        r.re_index = r.mcap / denom
    if f.current_assets is not None and f.liabilities is not None:
        r.ncav = f.current_assets - f.liabilities
        r.ncav_mcap = safe_div(r.ncav, r.mcap)
    if f.liabilities is not None:
        r.am = (f.cash or 0) + (f.securities or 0) + (f.investment_securities or 0) - f.liabilities
        r.am_mcap = safe_div(r.am, r.mcap)
    r.ni, r.op = f.net_income, f.op_income
    if r.mcap and f.net_income and f.net_income > 0:
        r.pe = r.mcap / f.net_income
    if r.mcap and f.equity and f.equity > 0:
        r.pb = r.mcap / f.equity
    if f.shares_issued and f.treasury_shares is not None:
        r.treasury_pct = f.treasury_shares / f.shares_issued * 100
    hist = [v for v in (f.net_income_history or []) if v is not None]
    if hist:
        r.years = len(hist)
        r.profit_years = sum(1 for v in hist if v > 0)
    r.top_holder, r.top_holder_pct = top_corporate_holder(f)
    return r


def reference_row(ref: dict, q: Optional[Quote]) -> ValueRow:
    """A row from the reference ranking (values in 億円), with market cap moved by the price since its date."""
    mcap = float(ref["market_cap_oku"]) * OKU
    nca = float(ref["net_current_assets_oku"]) * OKU
    re_ = float(ref["real_estate_oku"] or 0) * OKU
    r = ValueRow(code=norm_code(ref["code"]), name=ref["name"], market=ref["market"], mcap=mcap, nca=nca,
                 real_estate=re_, source="reference " + ref.get("asof", ""),
                 supplementary_mcap=str(ref.get("market_cap_supplementary")) == "1")
    r.pb = float(ref["pbr"]) if ref.get("pbr") else None
    if q and q.price:
        r.price, r.price_date, r.adv = q.price, q.price_date, q.adv
        # close on the reference date, or the last trade before it for stocks that did not trade that day
        base = next((c for d, c, _ in sorted(q.history, reverse=True) if d <= ref.get("asof", "")), None)
        if base:
            move = q.price / base
            r.mcap = mcap * move
            r.pb = r.pb * move if r.pb else None
            r.flags["price_move_since_ref"] = round((move - 1) * 100, 2)
        if q.per:
            r.pe = q.per
    r.re_index = r.mcap / (nca + re_) if (nca + re_) > 0 else None
    r.flags["ref_rank"] = int(ref["rank"])
    r.flags["ref_index"] = float(ref["index"])
    return r


def load_reference(path: str | Path) -> list[dict]:
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    asof = Path(path).stem.split("_")[-1]
    for r in rows:
        r["asof"] = asof
    return rows


def build_lists(rows: Iterable[ValueRow], crit: dict = CRITERIA, financial_codes: Optional[set] = None) -> dict:
    financial_codes = financial_codes or set()
    rows = [r for r in rows if r.code not in financial_codes]

    def profitable(r):
        return r.ni is not None and r.ni > 0

    L = {}
    L["re_netnet"] = sorted([r for r in rows if r.re_index is not None and r.re_index < crit["re_index_max"]],
                            key=lambda r: r.re_index)
    L["net_net"] = sorted([r for r in rows if (r.ncav_mcap or 0) >= 1.0], key=lambda r: (-profitable(r), -r.ncav_mcap))
    L["cash_rich"] = sorted([r for r in rows if (r.am_mcap or 0) >= crit["cash_rich_min"] and profitable(r)],
                            key=lambda r: -r.am_mcap)
    L["deep_value"] = sorted([r for r in rows if r.pe and r.pe <= crit["deep_pe_max"] and r.pb and r.pb <= crit["deep_pb_max"]
                              and (r.op or 0) > 0 and r.years
                              and r.profit_years >= round(crit["deep_profit_years"] * min(r.years, 5) / 5)],
                             key=lambda r: r.pe * r.pb)
    L["treasury"] = sorted([r for r in rows if (r.treasury_pct or 0) >= crit["treasury_min"] and r.pb
                            and r.pb <= crit["treasury_pb_max"]], key=lambda r: -r.treasury_pct)
    L["parent_sub"] = sorted([r for r in rows if (r.top_holder_pct or 0) >= crit["parent_min"] and is_corporate(r.top_holder or "")
                              and r.top_holder_pct < 100 and r.pb and r.pb <= crit["parent_pb_max"]],
                             key=lambda r: r.pb)
    out = {}
    tags: dict[str, list[str]] = {}
    for k, v in L.items():
        for r in v:
            tags.setdefault(r.code, []).append(k)
            if k not in r.lists:
                r.lists.append(k)
        out[k] = dict(count=len(v), rows=[r.to_dict() for r in v[:crit["list_max"]]])
    out["_tags"] = tags
    return out
