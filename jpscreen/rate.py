"""Size, filter and rate each catalyst: a direct port of the Korea Filing Radar's `evaluate` and `rate`.

Stars (same scale as Korea):
  5  act today: a priced edge (bid with a spread, large buyback at a small company, cash back worth a large
     part of the price) on a stock you can trade
  4  strong: open the filing and do the work this week
  3  worth a look: real event, edge not yet sized or partly priced in
  2  for the record: routine, small, or the company fails the profit and debt filters
  1  usually bad for minority shareholders, or untradeable

The stars themselves come from rating.py (edge + value − risk, the Korean radar's current system); this module
sizes each catalyst from its disclosure and applies the profit and debt filters.
"""
from __future__ import annotations

import math
from typing import Optional

from . import rating
from .classify import CAT_GROUP, CAT_LABEL, CATALYST_LABEL, SPECIAL_SITUATIONS, Tag
from .fundamentals import Fundamentals
from .prices import Quote
from .value import WATCH_LABEL, market_cap, net_current_assets, real_estate_value

LIMITS = dict(
    max_debt_equity=1.0,       # total liabilities / equity
    max_mcap=0.0,              # yen; 0 = no size limit
    adv_none=1e5,              # ¥/day: effectively no trading
    adv_very_thin=3e6,         # ≈ ₩30M in the Korean rules
    adv_thin=1.5e7,            # ≈ ₩150M
    small_cap=3e10,            # ≈ ₩300B
    large_cap=2e11,            # ≈ ₩2T
)



def yen(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1e12:
        return f"¥{v/1e12:.2f}T"
    if a >= 1e8:
        return f"¥{v/1e8:,.1f}億"
    if a >= 1e6:
        return f"¥{v/1e6:,.0f}M"
    return f"¥{v:,.0f}"


def evaluate(h: dict, tag: Tag, cat: dict, q: Optional[Quote], f: Optional[Fundamentals],
             watch_tags: list[str], limits: dict = LIMITS, activists=None, extra: Optional[dict] = None) -> dict:
    """Fill `h` (one hit) with sizing, filters, headline detail, stars and score."""
    extra = extra or {}
    mcap = market_cap(f, q) if q else None
    if mcap is None and q:
        mcap = q.mcap_issued
    price = q.price if q else None
    h.update(mcap=mcap, price=price, adv=q.adv if q else None, price_date=q.price_date if q else None)
    reasons: list[str] = []
    # ---- profit and debt filters
    if f and (f.op_income is not None or f.net_income is not None):
        if f.op_income is None or f.op_income <= 0:
            reasons.append("latest FY operating loss" if f.op_income is not None else "no FY operating income data")
        if f.net_income is None or f.net_income <= 0:
            reasons.append("latest FY net loss" if f.net_income is not None else "no FY net income data")
    elif q and q.eps is not None and q.eps <= 0:
        reasons.append("loss forecast (company EPS ≤ 0)")
    de = None
    if f and f.liabilities is not None and f.equity:
        if f.equity <= 0:
            reasons.append("negative equity")
        else:
            de = f.liabilities / f.equity
            if de > limits["max_debt_equity"]:
                reasons.append(f"liabilities/equity {de:.1f}x")
    h["debt_equity"] = de
    if limits.get("max_mcap") and mcap and mcap > limits["max_mcap"]:
        reasons.append(f"market cap {yen(mcap)} above {yen(limits['max_mcap'])}")
    if f and f.going_concern:
        reasons.append("going-concern note")
    hist = [v for v in ((f.net_income_history if f else None) or []) if v is not None]
    h["profitable_years"] = sum(1 for v in hist if v > 0)
    h["years_known"] = len(hist)
    # ---- value metrics
    if f and mcap:
        if f.liabilities is not None:
            am = (f.cash or 0) + (f.securities or 0) + (f.investment_securities or 0) - f.liabilities
            h["asset_margin_vs_mcap"] = am / mcap
            if f.current_assets is not None:
                h["ncav_vs_mcap"] = (f.current_assets - f.liabilities) / mcap
            nca = net_current_assets(f)
            if nca is not None:
                denom = nca + real_estate_value(f)
                h["re_index"] = mcap / denom if denom > 0 else None
        if f.net_income and f.net_income > 0:
            h["pe"] = mcap / f.net_income
        if f.shares_issued and f.treasury_shares:
            h["treasury_pct"] = f.treasury_shares / f.shares_issued * 100
    elif q and q.per:
        h["pe"] = q.per
    if extra.get("re_index") is not None and h.get("re_index") is None:
        h["re_index"] = extra["re_index"]
    if extra.get("am_mcap") is not None and h.get("asset_margin_vs_mcap") is None:
        h["asset_margin_vs_mcap"] = extra["am_mcap"]
    if q and q.pbr:
        h["pb"] = q.pbr
    if f and mcap and f.fcf_history:
        fc = [x for x in f.fcf_history[:3] if x is not None]
        if fc:
            h["fcf3_yield"] = sum(fc) / len(fc) / mcap
    if f and (f.op_income is not None or f.net_income is not None):
        h["op_income"], h["net_income"] = f.op_income, f.net_income
    elif q and q.eps is not None and q.shares_issued:
        h["op_income"] = h["net_income"] = q.eps * q.shares_issued   # company forecast, when no filing was read
    # ---- size the catalyst
    k = tag.kind
    bits: list[str] = []
    if k in ("tender_offer", "self_tender"):
        offer = cat.get("price")
        if offer and price and offer < 0.3 * price:
            offer = None
        if offer:
            bits.append(f"¥{offer:,.0f}/share")
            if cat.get("old_price"):
                bits.append(f"raised from ¥{cat['old_price']:,.0f}")
            if price:
                h["premium_pct"] = (offer / price - 1) * 100
                bits.append(f"{h['premium_pct']:+.1f}% vs last ¥{price:,.0f}")
        if k == "self_tender":
            bits.append("self-tender by the company")
        elif h.get("bidder"):
            bits.append(f"by {h['bidder']}")
        if cat.get("mbo") or tag.flags.get("mbo"):
            bits.append("MBO")
        if tag.flags.get("parent"):
            bits.append("parent buy-out")
        if cat.get("partial"):
            bits.append("partial (cap on shares bought)")
        if cat.get("delisting"):
            bits.append("going private")
        if cat.get("end"):
            bits.append(f"ends {cat['end']}")
        bv = cat.get("board_view") or tag.flags.get("board_view")
        if bv:
            bits.append({"support": "board supports", "neutral": "board neutral", "oppose": "board opposes",
                         "reserve": "board reserves its view"}[bv])
        if tag.flags.get("planned"):
            bits.append("bid announced, not yet launched")
    elif k == "squeeze_out":
        if cat.get("price") and price:
            h["premium_pct"] = (cat["price"] / price - 1) * 100
            bits.append(f"¥{cat['price']:,.0f}/share cash-out, {h['premium_pct']:+.1f}% vs last")
        if cat.get("delisting_date"):
            bits.append(f"delisting {cat['delisting_date']}")
    elif k in ("merger", "share_swap"):
        if cat.get("ratio_target") and extra.get("counter_price") and price:
            implied = cat["ratio_target"] * extra["counter_price"]
            h["appraisal_premium_pct"] = (implied / price - 1) * 100
            bits.append(f"ratio 1:{cat['ratio_target']:g} → implied ¥{implied:,.0f} ({h['appraisal_premium_pct']:+.1f}% vs last)")
        elif cat.get("ratio_target"):
            bits.append(f"ratio {cat.get('ratio_parent', 1):g}:{cat['ratio_target']:g}")
        if cat.get("delisting") and tag.role != "acquirer":
            bits.append("delisting")
        if cat.get("effective"):
            bits.append(f"effective {cat['effective']}")
    elif k == "split":
        if cat.get("spinoff") or tag.flags.get("spinoff"):
            bits.append("spin-off: shareholders receive the new shares")
        elif cat.get("physical_split") or tag.flags.get("physical_split"):
            bits.append("business moved into a subsidiary (holding-company structure)")
    elif k in ("control_change", "holding"):
        if cat.get("holder"):
            bits.append(cat["holder"])
        pb_, pa_ = cat.get("pct_before"), cat.get("pct_after")
        if pb_ and pa_ and not tag.flags.get("creeping"):
            bits.append(f"{pb_:.1f}% → {pa_:.1f}%")
        elif pa_ and not tag.flags.get("creeping"):
            bits.append(f"{pa_:.1f}%")
        if tag.flags.get("creeping"):
            bits.append("buying toward 1/3 without a bid (公開買付けに準ずる行為)")
        if tag.flags.get("defense"):
            bits.append("takeover-defence fight")
        a = _activist(cat.get("holder") or h.get("title", ""), activists)
        if a:
            cat["activist"] = a
            bits.append(f"known activist ({a})")
    elif k == "proxy_fight":
        if cat.get("holder"):
            bits.append(f"by {cat['holder']}")
        if cat.get("agenda"):
            bits.append(cat["agenda"][:80])
        a = _activist(cat.get("holder") or "", activists)
        if a:
            cat["activist"] = a
    elif k == "liquidation":
        if cat.get("distribution_per_share") and price:
            h["return_yield"] = cat["distribution_per_share"] / price * 100
            bits.append(f"expected distribution ¥{cat['distribution_per_share']:,.0f}/share = {h['return_yield']:.0f}% of price")
        if h.get("ncav_vs_mcap") is not None:
            bits.append(f"NCAV {h['ncav_vs_mcap']:.2f}x mcap")
        if cat.get("reason"):
            bits.append(cat["reason"][:80])
    elif k == "listing":
        if tag.flags.get("resolved"):
            bits.append("listing criteria met, exchange review lifted")
        if cat.get("criteria"):
            bits.append("criteria: " + "/".join(cat["criteria"]))
        if tag.flags.get("improvement_period"):
            bits.append("in the improvement period")
        if cat.get("deadline"):
            bits.append(f"deadline {cat['deadline']}")
        if tag.flags.get("upgrade"):
            bits.append("moving up to Prime")
        if tag.flags.get("voluntary_delisting"):
            bits.append("delisting")
    elif k == "buyback":
        amt = cat.get("amount")
        if amt and mcap:
            h["buyback_vs_mcap"] = amt / mcap
        elif cat.get("pct_of_shares"):
            h["buyback_vs_mcap"] = cat["pct_of_shares"] / 100
        if amt:
            bits.append(yen(amt))
        if h.get("buyback_vs_mcap") is not None:
            bits.append(f"{h['buyback_vs_mcap']*100:.1f}% of mcap")
        if cat.get("tostnet") or tag.flags.get("tostnet"):
            bits.append("off-market block (ToSTNeT)")
        if cat.get("specific_holder") or tag.flags.get("specific_holder"):
            bits.append("from a specific holder")
        if cat.get("to_cancel") or tag.flags.get("to_cancel"):
            bits.append("to be cancelled")
    elif k == "cancellation":
        if cat.get("pct_of_issued") is not None:
            bits.append(f"{cat['pct_of_issued']:.1f}% of issued shares")
    elif k == "dividend":
        dps = cat.get("dps")
        if dps:
            bits.append(f"¥{dps:,.1f}/share")
        if dps and price:
            h["div_yield"] = dps / price * 100
        elif q and q.dps and price:
            h["div_yield"] = q.dps / price * 100
        if h.get("div_yield"):
            bits.append(f"{h['div_yield']:.1f}% yield")
        if cat.get("raise_pct") is not None:
            bits.append(f"{cat['raise_pct']:+.0f}% vs last year")
        if cat.get("special") or tag.flags.get("special"):
            bits.append("special/commemorative")
        if cat.get("initiation") or tag.flags.get("initiation"):
            bits.append("first or resumed dividend")
    elif k == "value_up":
        if cat.get("mentions"):
            bits.append("mentions " + ", ".join(cat["mentions"][:5]))
        for key, lab in (("payout_target", "payout"), ("doe_target", "DOE"), ("roe_target", "ROE")):
            if cat.get(key):
                bits.append(f"{lab} {cat[key]:g}%")
    elif k in ("rumor", "strategic"):
        if cat.get("confirmed_review"):
            bits.append("company confirms it is considering")
        if cat.get("proposal"):
            bits.append("proposal received")
        if cat.get("special_committee"):
            bits.append("special committee")
        if cat.get("keywords"):
            bits.append("mentions " + ", ".join(cat["keywords"][:4]))
        if cat.get("snippet"):
            bits.append("“" + cat["snippet"][:140] + "…”")
    elif k == "guidance":
        vs = "last year (first forecast)" if cat.get("first_forecast") else "prior forecast"
        for key, lab in (("op_chg", "op. profit"), ("ni_chg", "net profit")):
            if cat.get(key) is not None:
                bits.append(f"{lab} {cat[key]:+.0f}% vs {vs}")
        if cat.get("div_raise_pct") is not None:
            bits.append(f"dividend {cat['div_raise_pct']:+.0f}%")
    elif k == "asset_sale":
        if cat.get("asset"):
            bits.append(cat["asset"])
        if cat.get("proceeds"):
            bits.append(f"for {yen(cat['proceeds'])}")
        if cat.get("gain"):
            bits.append(f"gain {yen(cat['gain'])}")
            if mcap:
                h["gain_vs_mcap"] = cat["gain"] / mcap
                bits.append(f"{h['gain_vs_mcap']*100:.1f}% of mcap")
        if h.get("asset_margin_vs_mcap") is not None and cat.get("proceeds") and mcap:
            h["pro_forma_vs_mcap"] = h["asset_margin_vs_mcap"] + cat["proceeds"] / mcap
    elif k == "contract":
        if cat.get("amount"):
            bits.append(yen(cat["amount"]))
            if f and f.revenue:
                h["contract_pct"] = cat["amount"] / f.revenue * 100
                bits.append(f"{h['contract_pct']:.0f}% of revenue")
            if mcap:
                h["contract_vs_mcap"] = cat["amount"] / mcap
    h["catalyst_detail"] = "; ".join(b for b in bits if b)
    # ---- filters: special situations are priced on deal terms, not earnings
    special = k in SPECIAL_SITUATIONS
    if special:
        h["warnings"] = [r for r in reasons if not r.startswith("market cap")]
        reasons = [r for r in reasons if r.startswith("market cap")]
    else:
        h["warnings"] = []
    h["passes"] = not reasons
    h["fail_reasons"] = reasons
    h["watch"] = [WATCH_LABEL.get(t, t) for t in watch_tags]
    h["stars"], h["why"], h["rating"] = rating.rate(h, tag.kind, tag.cat, tag.group, cat, tag.flags)
    h["score"] = round(_score(h, tag, cat) + 20 * (h["stars"] or 0), 1)
    return h


def _activist(name: str, activists) -> Optional[str]:
    if not activists or not name:
        return None
    for rx, disp, kind in activists:
        if rx.search(name):
            return disp
    return None


def _score(h: dict, tag: Tag, cat: dict) -> float:
    s = {"tender_offer": 15, "self_tender": 6, "squeeze_out": 3, "strategic": 12, "rumor": 10, "control_change": 8,
         "holding": 10 if cat.get("activist") or tag.flags.get("creeping") else 5, "merger": 5, "share_swap": 6,
         "split": 2, "liquidation": 12, "reverse_merger": 8, "proxy_fight": 10, "control_dispute": 8,
         "capital_return": 12, "listing": 8, "value_up": 6, "buyback": 5, "cancellation": 4, "dividend": 4,
         "guidance": 4, "asset_sale": 6, "contract": 3}.get(tag.kind, 3)
    s += 6 if cat.get("activist") else 0
    s += min(max(h.get("appraisal_premium_pct") or 0, 0) / 2, 15)
    s += min(h.get("return_yield") or 0, 15)
    s += min((h.get("buyback_vs_mcap") or 0) * 200, 10)
    s += min(cat.get("pct_of_issued") or 0, 10) + min(h.get("div_yield") or 0, 8)
    s += min(max(h.get("premium_pct") or 0, 0) / 5, 10)
    s += min((h.get("gain_vs_mcap") or 0) * 50, 10)
    s += max(min((h.get("asset_margin_vs_mcap") or 0) * 10, 10), 0)
    if h.get("re_index") is not None and h["re_index"] < 1:
        s += min((1 - h["re_index"]) * 15, 10)
    if h.get("pe"):
        s += max(0, (10 - h["pe"]) / 2)
    if h.get("watch"):
        s += 15 + 5 * (len(h["watch"]) - 1)
    return s


def headline(h: dict, tag: Tag) -> str:
    if tag.signal == "warning":
        return tag.label or "Warning"
    lab = CATALYST_LABEL.get(tag.kind, "Catalyst")
    return lab + (f": {h['catalyst_detail']}" if h.get("catalyst_detail") else "")


def fin_line(h: dict, f: Optional[Fundamentals]) -> str:
    parts = []
    if f and (f.revenue or f.op_income or f.net_income):
        parts.append(f"FY revenue {yen(f.revenue)}, op. {yen(f.op_income)}, net {yen(f.net_income)}")
    if h.get("years_known"):
        parts.append(f"profitable {h['profitable_years']} of last {h['years_known']} yrs")
    de = h.get("debt_equity")
    parts.append(f"liab/equity {de:.2f}x" if de is not None else "liab/equity n/a")
    parts.append(f"mcap {yen(h.get('mcap'))}" if h.get("mcap") else "mcap n/a")
    if h.get("pe"):
        parts.append(f"P/E {h['pe']:.1f}")
    if h.get("pb"):
        parts.append(f"P/B {h['pb']:.2f}")
    if h.get("re_index") is not None:
        parts.append(f"RE net-net index {h['re_index']:.2f}")
    if h.get("asset_margin_vs_mcap") is not None:
        parts.append(f"cash+sec−liab {h['asset_margin_vs_mcap']:.2f}x mcap")
    if h.get("treasury_pct"):
        parts.append(f"treasury {h['treasury_pct']:.1f}%")
    if h.get("warnings"):
        parts.append("note: " + ", ".join(h["warnings"]))
    return " · ".join(parts)


def group_and_cat(tag: Tag) -> tuple[str, str, str]:
    return tag.group, tag.cat, CAT_LABEL.get(tag.cat, tag.cat)
