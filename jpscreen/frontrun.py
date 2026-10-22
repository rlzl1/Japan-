"""Pre-deal radar: which cheap Japanese companies look most likely to get a bid, an MBO, a parent buy-out,
a squeeze-out or a forced capital-policy change BEFORE anything is announced.

Each signal carries a prior probability of a deal within ~2 years; they are combined with a noisy-OR
(P = 1 - prod(1 - p_i)). The priors are judgment calls drawn from how recent Japanese take-privates
looked beforehand (cheap on assets, controlled or founder-held, activist on the register, listing
criteria at risk, special committees); they are not yet backtested and live in PRIORS so they can be tuned.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .classify import classify
from .fundamentals import FINANCIAL_HOLDER, SELF_HELD, TRUST_BANKS, Fundamentals
from .value import ValueRow, is_corporate

PRIORS = {
    "listed_subsidiary": 0.22,      # corporate parent 50-90%: parent-subsidiary listings are being unwound
    "parent_cheap": 0.10,           # ... and trading below assets
    "strong_affiliate": 0.10,       # corporate holder 33-50%
    "founder_control": 0.15,        # founder/family/asset company + officers >= 25%
    "founder_cheap": 0.12,          # ... at P/B < 0.8 and a buyout-sized market cap
    "aging_founder": 0.08,          # representative director 70+ with a large stake
    "activist": 0.18,               # known activist on the register or filing
    "murakami": 0.10,               # Murakami-related funds: self-tenders / sales usually follow
    "creeping": 0.30,               # someone buying toward 1/3 (公開買付けに準ずる行為) or a takeover-defence fight
    "listing_breach": 0.22,         # TSE listing criteria not met / improvement plan
    "listing_breach_cheap": 0.10,   # ... while trading below assets
    "special_committee": 0.45,      # special committee / strategic review / confirmed sale talks
    "press_report": 0.30,           # answered a press report about a deal
    "proxy_fight": 0.12,
    "holder_exit": 0.06,            # large holder sold down: a block is for sale
    "self_financing": 0.10,         # net cash >= 50% of mcap and RE index < 0.7: a buyer pays with the company's cash
    "deep_asset_discount": 0.08,    # RE net-net index < 0.5
    "small_illiquid": 0.05,         # < ¥10bn mcap and < ¥10M/day: listing costs > benefits
    "hikari": 0.04,                 # Hikari Tsushin group stake: tends to tender
}

ACTIVIST_CACHE: list[tuple[re.Pattern, str, str]] = []


def load_activists(path) -> list[tuple[re.Pattern, str, str]]:
    import csv
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        out.append((re.compile(r["pattern"], re.I), r["display"], r["kind"]))
    return out


def match_activist(name: str, activists) -> Optional[tuple[str, str]]:
    for rx, disp, kind in activists:
        if rx.search(name or ""):
            return disp, kind
    return None


@dataclass
class FrontRun:
    code: str
    name: str
    market: str
    prob: float = 0.0
    stars: int = 1
    reasons: list = field(default_factory=list)
    signals: list = field(default_factory=list)
    re_index: Optional[float] = None
    pb: Optional[float] = None
    mcap: Optional[float] = None
    adv: Optional[float] = None
    am_mcap: Optional[float] = None
    events: list = field(default_factory=list)   # recent TDnet signals [{date, kind, title, url}]

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["prob"] = round(self.prob, 3)
        return d


def history_signals(disclosures: Iterable, since: dt.date) -> list[dict]:
    """Deal-relevant TDnet events in a company's own history (yanoshin keeps years of titles)."""
    out = []
    for d in disclosures:
        if d.date < since:
            continue
        tag = classify(d.title, d.code)
        if not tag:
            continue
        kind = tag.kind
        keep = kind in ("strategic", "rumor", "listing", "holding", "proxy_fight", "control_change", "control_dispute",
                        "tender_offer", "squeeze_out", "self_tender", "value_up", "buyback", "liquidation")
        if keep:
            out.append(dict(date=d.date.isoformat(), kind=kind, flags=tag.flags, title=d.title, url=d.pdf_url))
    return out


def score(row: ValueRow, f: Optional[Fundamentals], events: list[dict], activists, today: dt.date) -> FrontRun:
    fr = FrontRun(code=row.code, name=row.name, market=row.market, re_index=row.re_index, pb=row.pb, mcap=row.mcap,
                  adv=row.adv, am_mcap=row.am_mcap, events=events[:8])
    sig: list[tuple[str, float, str]] = []

    def add(key, why, p=None):
        sig.append((key, PRIORS[key] if p is None else p, why))

    cheap = (row.re_index is not None and row.re_index < 1.0) or (row.pb is not None and row.pb < 0.8)
    # ---- ownership (needs the annual report)
    if f and f.major_holders:
        top_name, top_pct = None, None
        for h in f.major_holders:
            nm = h.get("name") or ""
            if TRUST_BANKS.search(nm) or SELF_HELD.search(nm):
                continue
            top_name, top_pct = nm, h.get("pct")
            break
        if top_name and top_pct:
            corporate = is_corporate(top_name) and not FINANCIAL_HOLDER.search(top_name)
            if corporate and 50 <= top_pct < 90:
                add("listed_subsidiary", f"listed subsidiary: {top_name} holds {top_pct:.1f}%")
                if cheap:
                    add("parent_cheap", "parent could buy the minority below asset value")
            elif corporate and 33.4 <= top_pct < 50:
                add("strong_affiliate", f"{top_name} holds {top_pct:.1f}% (veto stake)")
        insiders = sum(h["pct"] for h in f.major_holders
                       if not TRUST_BANKS.search(h["name"]) and not FINANCIAL_HOLDER.search(h["name"])
                       and not is_corporate(h["name"]) and not SELF_HELD.search(h["name"]))
        asset_cos = sum(h["pct"] for h in f.major_holders if re.search(r"興産|商事|企画|資産管理|ホールディング|有限会社", h["name"])
                        and h["pct"] < 50 and not FINANCIAL_HOLDER.search(h["name"]))
        family = insiders + (asset_cos if insiders >= 3 else 0)
        if family >= 25:
            add("founder_control", f"founder/family holders ≈{family:.0f}%")
            if (row.pb or 9) < 0.8 and (row.mcap or 0) < 5e10:
                add("founder_cheap", "founder-held, P/B < 0.8, buyout-sized (< ¥50bn)")
            if f.ceo_age and f.ceo_age >= 70:
                add("aging_founder", f"representative director aged {f.ceo_age}")
        for h in f.major_holders:
            a = match_activist(h["name"], activists)
            if a:
                key = "murakami" if a[1] == "murakami" else ("hikari" if a[1] == "event_holder" else "activist")
                add(key, f"{a[0]} on the register ({h['pct']:.1f}%)")
                if key == "murakami":
                    add("activist", "activist pressure", PRIORS["activist"])
    # ---- assets
    if row.am_mcap is not None and row.am_mcap >= 0.5 and (row.re_index or 9) < 0.7:
        add("self_financing", f"cash+securities−liabilities {row.am_mcap:.2f}× mcap: a buyer can fund the bid from the company")
    if row.re_index is not None and row.re_index < 0.5:
        add("deep_asset_discount", f"RE net-net index {row.re_index:.2f}")
    if row.mcap and row.mcap < 1e10 and row.adv is not None and row.adv < 1e7:
        add("small_illiquid", f"mcap ¥{row.mcap/1e8:.0f}億, ¥{row.adv/1e6:.1f}M/day traded")
    # ---- disclosed events
    seen = set()
    for e in events:
        k, fl = e["kind"], e.get("flags") or {}
        age = (today - dt.date.fromisoformat(e["date"])).days
        decay = 1.0 if age <= 365 else 0.5
        if k in ("strategic",) and "strategic" not in seen:
            seen.add("strategic"); add("special_committee", f"{e['date']}: {e['title'][:60]}", PRIORS["special_committee"] * decay)
        elif k == "rumor" and "rumor" not in seen:
            seen.add("rumor"); add("press_report", f"{e['date']}: answered a press report", PRIORS["press_report"] * decay)
        elif k == "listing" and fl.get("breach") and "listing" not in seen:
            seen.add("listing"); add("listing_breach", f"{e['date']}: listing-criteria plan / non-compliance", PRIORS["listing_breach"] * decay)
            if cheap:
                add("listing_breach_cheap", "listing pressure on a stock below asset value")
        elif k == "holding" and (fl.get("creeping") or fl.get("defense")) and "creeping" not in seen:
            seen.add("creeping"); add("creeping", f"{e['date']}: {e['title'][:60]}", PRIORS["creeping"] * decay)
        elif k == "proxy_fight" and "proxy" not in seen:
            seen.add("proxy"); add("proxy_fight", f"{e['date']}: shareholder proposal / EGM request", PRIORS["proxy_fight"] * decay)
        elif k == "control_change" and fl.get("selling") and "exit" not in seen:
            seen.add("exit"); add("holder_exit", f"{e['date']}: large holder selling", PRIORS["holder_exit"] * decay)
    # ---- combine
    p_not = 1.0
    for _, p, _ in sig:
        p_not *= (1 - p)
    fr.prob = 1 - p_not
    fr.signals = [k for k, _, _ in sig]
    fr.reasons = [w for _, _, w in sig]
    fr.stars = 5 if fr.prob >= 0.6 else 4 if fr.prob >= 0.45 else 3 if fr.prob >= 0.3 else 2 if fr.prob >= 0.15 else 1
    return fr
