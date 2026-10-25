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
    pe: Optional[float] = None               # on the last full year's net income
    pe_fwd: Optional[float] = None           # on the company's forecast EPS (Yahoo quote page)
    pb: Optional[float] = None
    div_yield: Optional[float] = None        # % forecast dividend on the price
    treasury_pct: Optional[float] = None
    fit: Optional[float] = None              # 0-100 composite in the user's style (fit_scores)
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


SPLIT_RATIOS = (2, 3, 4, 5, 10, 20, 1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 3 / 2, 5 / 4)


def shares_now(f: Optional[Fundamentals], q: Optional[Quote]) -> tuple[Optional[float], float]:
    """(issued, treasury) today: the filing's counts, carried forward to the quote's current issued count.

    A split or reverse split since the filing scales treasury shares by the same ratio; a cancellation removes the
    cancelled shares from treasury; an issuance leaves treasury as it was."""
    f_issued = f.shares_issued if f and f.shares_issued else None
    treasury = (f.treasury_shares or 0.0) if f else 0.0
    q_issued = q.shares_issued if q and q.shares_issued else None
    if f_issued is None or q_issued is None:
        return f_issued or q_issued, treasury if f_issued else 0.0
    r = q_issued / f_issued
    if abs(r - 1) < 0.005:
        return f_issued, treasury
    if any(abs(r / k - 1) < 0.01 for k in SPLIT_RATIOS):
        return q_issued, treasury * r
    if q_issued < f_issued:
        return q_issued, max(treasury - (f_issued - q_issued), 0.0)
    return q_issued, treasury


def market_cap(f: Optional[Fundamentals], q: Optional[Quote], basis: str = "ex_treasury") -> Optional[float]:
    if q is None or q.price is None:
        return None
    issued, treasury = shares_now(f, q)
    if issued is None:
        return q.mcap_issued
    return q.price * max(issued - (treasury if basis == "ex_treasury" else 0), 0)


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
        if q.price and q.eps and q.eps > 0:
            r.pe_fwd = round(q.price / q.eps, 2)
        r.div_yield = q.div_yield if q.div_yield is not None else (round(q.dps / q.price * 100, 2) if q.dps and q.price else None)
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


# ---------------------------------------------------------------- FIT: the user's own style, as one number
FIT_WEIGHTS = dict(net_cash=0.35, earnings_yield=0.25, treasury=0.10, div_yield=0.10, record=0.10, owner=0.10)


def _pct_ranks(vals: dict[str, Optional[float]]) -> dict[str, float]:
    """0-1 percentile rank of each value among those present (None → 0)."""
    present = sorted((v, k) for k, v in vals.items() if v is not None)
    out = {k: 0.0 for k in vals}
    n = len(present)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and present[j + 1][0] == present[i][0]:
            j += 1
        rank = (i + j) / 2 / max(n - 1, 1)
        for _, k in present[i:j + 1]:
            out[k] = rank
        i = j + 1
    return out


def fit_scores(rows: Iterable[ValueRow], financial_codes: Optional[set] = None, weights: dict = FIT_WEIGHTS) -> int:
    """Score every row 0-100 on what the owner's holdings have in common: cash + securities above the market cap,
    a single-digit P/E, buybacks (treasury), a dividend, profits every year, and an owner big enough to take the
    company private. Each part is a percentile rank among non-financial companies with fundamentals; FIT is the
    weighted mean, given as a percentile (FIT 95 = better than 95% of companies). Loss-makers score zero on earnings."""
    financial_codes = financial_codes or set()
    rows = [r for r in rows if r.code not in financial_codes and r.mcap and r.source]
    def backing(r):     # the better of cash + securities − liabilities and net current assets, against market cap
        vals = [x for x in (r.am_mcap, r.ncav_mcap) if x is not None]
        return max(vals) if vals else None
    parts = {
        "net_cash": {r.code: backing(r) for r in rows},
        "earnings_yield": {r.code: (1 / (r.pe_fwd or r.pe) if (r.pe_fwd or r.pe) else (0.0 if r.ni is not None else None)) for r in rows},
        "treasury": {r.code: r.treasury_pct for r in rows},
        "div_yield": {r.code: r.div_yield for r in rows},
        "record": {r.code: (r.profit_years / r.years if r.years else None) for r in rows},
        # an owner between a third and two thirds can take the company out; above that there is little left to buy
        "owner": {r.code: (min(r.top_holder_pct, 66.7) / 66.7 if r.top_holder_pct else None) for r in rows},
    }
    ranks = {k: _pct_ranks(v) for k, v in parts.items()}
    composite = {r.code: sum(weights[k] * ranks[k][r.code] for k in weights) for r in rows}
    fit = _pct_ranks(composite)             # FIT 95 = better than 95% of the market on the weighted mix
    for r in rows:
        r.fit = round(100 * fit[r.code], 1)
    return len(rows)


PRICE_SCALED = ("mcap", "re_index", "pe", "pe_fwd", "pb")      # move with the price
PRICE_INVERSE = ("ncav_mcap", "am_mcap", "div_yield")           # move against it


def reprice(value: dict, quotes: dict) -> int:
    """Carry a saved value screen to today's prices: every ratio that has the market cap in it is scaled by the
    price change, so the cheapness table stays current between the weekly screens. quotes: code -> Quote."""
    n = 0
    seen: dict[str, tuple] = {}

    def fix(row: dict) -> None:
        nonlocal n
        q = quotes.get(row["code"])
        old = row.get("price")
        if not q or not q.price or not old or q.price == old or (q.price_date or "") <= (row.get("price_date") or ""):
            return
        k = q.price / old
        for f in PRICE_SCALED:
            if row.get(f) is not None:
                row[f] = round(row[f] * k, 4) if abs(row[f]) < 1000 else round(row[f] * k)
        for f in PRICE_INVERSE:
            if row.get(f) is not None:
                row[f] = round(row[f] / k, 4)
        row["price"], row["price_date"] = q.price, q.price_date
        if row["code"] not in seen:
            seen[row["code"]] = (old, q.price)
            n += 1

    for row in value.get("rows_all") or []:
        fix(row)
    for k, lst in (value.get("lists") or {}).items():
        if isinstance(lst, dict):
            for row in lst.get("rows") or []:
                fix(row)
            if k == "re_netnet":
                lst["rows"].sort(key=lambda r: r.get("re_index") if r.get("re_index") is not None else 9)
    dates = [r.get("price_date") for r in (value.get("rows_all") or []) if r.get("price_date")]
    if dates and value.get("meta") is not None:
        value["meta"]["price_date"] = max(dates)
    return n
