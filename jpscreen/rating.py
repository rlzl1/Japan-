"""Stars on the Korea Filing Radar's current system (edge + value - risk), adapted to Japanese deals.

Stars come from three separate parts, each fact counted once:
  edge   what the event itself is worth, sized from the disclosure's own numbers (0-3; workouts 0-4)
  value  how cheap the company is on the single best of the classic tests (0-2; the tests are not added up)
  risk   can you trade it: thin trading, exchange designations, warnings on special situations
Workouts (tender offers, squeeze-outs, share exchanges, liquidations, cash returns) are priced on the deal: value is
shown but not counted. Everything else is a "general with a catalyst": value counts, but never for more than the edge
plus one, so cheapness alone can't lift a routine disclosure past two stars.
total = 1 + edge + value - risk   ->   5★ 4.75+, 4★ 3.75+, 3★ 2.75+, 2★ 1.75+, else 1★

Japan adds one value test the Korean radar does not have: the real-estate net-net index (market cap against net
current assets plus rental property at fair value and half the land), the measure behind the CHEAP ranking.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

STAR_CUTS = ((4.75, 5), (3.75, 4), (2.75, 3), (1.75, 2))
WORKOUTS = {"tender", "mna", "liquidation", "capital_return"}
TAX_ON_GAIN = 0.30

# yen equivalents of the Korean thresholds (₩1M, ₩30M, ₩150M a day; ₩300B and ₩2T market caps)
ADV_NONE, ADV_VERY_THIN, ADV_THIN = 1e5, 3e6, 1.5e7
SMALL_CAP, LARGE_CAP = 3e10, 2e11


def stars_for(total: float) -> int:
    return next((s for cut, s in STAR_CUTS if total >= cut), 1)


def value_points(v: dict) -> tuple[float, Optional[str]]:
    """0-2 on the single best test. v: net_mcap, ncav_mcap, re_index, pe, fcf3_yield, pb, de, op, ni, py, yk."""
    op, ni = v.get("op"), v.get("ni")
    earning = bool(op is not None and op > 0 and ni is not None and ni > 0)
    loss_note = " (loss-making)" if (op is not None and op <= 0) or (ni is not None and ni <= 0) else \
        " (profits not read)" if not earning else ""
    py, yk = v.get("py") or 0, v.get("yk") or 0
    n, nc, ri, pe, f3, pb, de = (v.get(k) for k in ("net_mcap", "ncav_mcap", "re_index", "pe", "fcf3_yield", "pb", "de"))
    tests = []
    if n is not None:
        if n >= 1.0:
            tests.append((2.0, f"cash+securities−all liabilities {n:.2f}× market cap", "asset"))
        elif n >= 0.6:
            tests.append((1.5, f"cash+securities−all liabilities {n:.2f}× market cap", "asset"))
        elif n >= 0.35:
            tests.append((1.0, f"cash+securities−all liabilities {n:.2f}× market cap", "asset"))
    if nc is not None and nc >= 1.0:
        tests.append((1.5 if earning else 1.0,
                      f"net current assets {nc:.2f}× market cap" + loss_note, "asset"))
    if ri is not None and ri > 0:
        pts = 2.0 if ri <= 0.5 else 1.5 if ri <= 0.7 else 1.0 if ri < 1.0 else 0
        if pts:
            tests.append((pts if earning or ri <= 0.5 else pts - 0.5,
                          f"RE net-net index {ri:.2f}: market cap below net current assets + real estate"
                          + loss_note, "asset"))
    if earning and pe:
        if f3 is not None:
            pts = 2.0 if (pe <= 6 and f3 >= 0.12) else 1.5 if (pe <= 8 and f3 >= 0.08) else \
                1.0 if (pe <= 11 and f3 >= 0.06) else 0.5 if (pe <= 15 and f3 >= 0.04) else 0
            if pts:
                tests.append((pts, f"P/E {pe:.1f} with FCF yield {f3*100:.0f}% (3-yr avg)", "earn"))
            elif pe <= 6:
                tests.append((0.5, f"P/E {pe:.1f} but FCF yield only {f3*100:.0f}%", "earn"))
        else:
            pts = 1.0 if pe <= 6 else (0.5 if pe <= 10 else 0)
            if pts:
                tests.append((pts, f"P/E {pe:.1f} (no cash-flow data)", "earn"))
        if f3 is not None and f3 >= 0.15 and pe > 8:
            tests.append((1.5, f"FCF yield {f3*100:.0f}% (3-yr avg), P/E {pe:.1f}", "earn"))
    if pb is not None and pb <= 0.5 and earning and yk and py == yk:
        tests.append((1.0, f"P/B {pb:.2f}, profitable every year", "book"))
    fit = v.get("fit")
    if fit is not None and earning:
        pts = 2.0 if fit >= 95 else 1.5 if fit >= 90 else 1.0 if fit >= 80 else 0
        if pts:
            tests.append((pts, f"FIT {fit:.0f}: top {100 - int(fit)}% on net cash, earnings yield, payout and ownership", "fit"))
    if not tests:
        return 0.0, None
    pts, why, kind = max(tests, key=lambda t: t[0])
    if kind == "earn":
        if yk >= 3 and py < yk - 1:
            pts -= 0.5
            why += f"; profitable only {py} of {yk} years"
        if de is not None and de > 1.5:
            pts -= 0.5
            why += f"; liabilities {de:.1f}× equity"
    return max(pts, 0.0), why


def _corporate(name: str) -> bool:
    return bool(name) and any(x in name for x in ("株式会社", "(株)", "㈱", "有限会社", "合同会社", "Co.", "Ltd", "Inc",
                                                   "LIMITED", "CORPORATION", "ホールディングス", "Holdings"))


def edge_points(h: dict, kind: str, cid: str, cat: dict, flags: dict) -> tuple[float, list[str]]:
    """What the event is worth on its own terms, with the reasons."""
    e, why = 0.0, []
    prem = h.get("premium_pct")
    if cid == "tender":
        ended = cat.get("end") and h.get("asof") and cat["end"] < h["asof"]
        if kind == "squeeze_out":
            e = 2.0 if (prem or 0) >= 5 else 1.0 if (prem or 0) >= 2 else 0.0
            why.append("post-bid squeeze-out: cash-out at the bid price" + (f", {prem:+.1f}% to it" if prem is not None else ""))
        elif kind == "self_tender":
            e = 1.0 if (prem or 0) >= 5 else 0.0
            why.append("self-tender: usually priced below market to buy out a cross-holder, so no edge for outside holders"
                       if (prem or 0) <= 0 else f"self-tender {prem:+.1f}% to the market")
        elif prem is None:
            e = 1.0 if cat.get("delisting") else 0.0
            why.append("offer price against the market not read")
        elif cat.get("delisting") and not cat.get("partial"):
            e = 4.0 if prem >= 10 else 3.0 if prem >= 5 else 2.0 if prem >= 2 else 1.0
            why.append(f"going-private bid, all shares can be tendered: {prem:+.1f}% to the offer"
                       if prem > 0 else "going-private bid, but trading at or above the offer: the market expects more")
        else:
            e = 2.0 if prem >= 20 else 1.5 if prem >= 10 else 1.0 if prem >= 5 else 0.0
            why.append(f"{prem:+.1f}% to the offer, but a partial tender: only part is bought and the rest usually "
                       "falls back once it closes")
        if cat.get("old_price") and kind == "tender_offer":
            e += 0.5
            why.append(f"bid already raised from ¥{cat['old_price']:,.0f}: contested or activist pressure")
        if (cat.get("board_view") or flags.get("board_view")) == "oppose":
            why.append("the board opposes the bid: a counter-bid or a higher price is possible")
        if flags.get("planned"):
            why.append("announced, not yet launched (approvals pending)")
        if ended:
            e = 0.0
            why.append(f"offer period ended {cat['end']}")
    elif cid in ("mna", "backdoor"):
        ap = h.get("appraisal_premium_pct")
        if kind == "reverse_merger" or cid == "backdoor":
            e = 1.0
            why.append("an unlisted business listing through this company: no price floor")
        elif ap is not None:
            e = 4.0 if ap >= 15 else 3.0 if ap >= 10 else 2.0 if ap >= 5 else 1.0 if ap >= 2 else 0.5
            why.append(f"exchange ratio × acquirer's price is {ap:+.1f}% vs the market")
        else:
            e = 0.5
            why.append("merger or share exchange; ratio not priced")
        if h.get("role") == "acquirer" and not h.get("target_listed"):
            e = min(e, 0.0)
            why.append("the company is the acquirer")
    elif cid == "liquidation":
        ry, nm = h.get("return_yield"), h.get("ncav_vs_mcap")
        if ry is not None:
            e = 4.0 if ry >= 130 else 3.0 if ry >= 100 else 2.0 if ry >= 80 else 1.0 if ry >= 60 else 0.0
            why.append(f"expected distribution {ry:.0f}% of the price")
        elif nm is not None:
            e = 4.0 if nm >= 1.3 else 3.0 if nm >= 1.0 else 2.0 if nm >= 0.8 else 1.0 if nm >= 0.6 else 0.0
            why.append(f"net current assets {nm:.2f}× market cap, before liquidation costs and tax")
        else:
            e = 1.0
            why.append("balance sheet not read: size the payout from the disclosure")
    elif cid == "capital_return":
        y = h.get("return_yield")
        if y is not None:
            e = 3.5 if y >= 20 else 2.5 if y >= 10 else 1.5 if y >= 5 else 0.5
            why.append(f"cash returned = {y:.1f}% of the price")
        else:
            e = 1.0
            why.append("amount per share not read")
    elif cid == "asset_sale":
        mcap = h.get("mcap")
        gain, proceeds, book = cat.get("gain"), cat.get("proceeds"), cat.get("book_value")
        if cat.get("cross_holdings") and not cat.get("real_estate"):
            e = 0.0
            why.append("cross-shareholdings are already carried at market value: the sale adds cash, not value")
        elif mcap and (gain or (proceeds and book is not None)):
            g = gain if gain else proceeds - book
            add = g * (1 - TAX_ON_GAIN) / mcap
            e = 3.0 if add >= 1 else 2.0 if add >= 0.5 else 1.0 if add >= 0.25 else 0.5 if add >= 0.1 else 0.0
            why.append(f"gain over book {g / mcap * 100:.0f}% of market cap, about {add * 100:.0f}% after tax"
                       + (": hidden real-estate value booked" if cat.get("real_estate") else ""))
        elif proceeds and mcap:
            why.append(f"sale price {proceeds / mcap:.2f}× market cap; book value not read")
        else:
            why.append("sale price or gain not read")
        pf = h.get("pro_forma_vs_mcap")
        if pf is not None:
            why.append(f"cash+securities−liabilities after the sale {pf:.2f}× market cap")
    elif cid == "sale_process":
        kws = " ".join(cat.get("keywords") or [])
        e = 0.5
        if cat.get("confirmed_review"):
            why.append("company confirms it is considering a deal")
        if cat.get("special_committee"):
            e += 0.5
            why.append("special committee formed: the usual step before an MBO or parent buy-out")
        if cat.get("proposal"):
            e += 0.5
            why.append("a takeover / buy-out proposal has been received")
        if any(x in kws for x in ("公開買付", "TOB", "MBO", "非公開化", "完全子会社化")):
            e += 0.5
            why.append("mentions a tender offer or going private")
        elif "売却" in kws and "当社による" in (cat.get("snippet") or ""):
            e = 0.0
            why.append("the company is selling a business, not being bought")
        e = min(e, 2.0)
    elif cid == "control":
        holder = cat.get("holder") or ""
        if cat.get("direction") == "down" or flags.get("selling"):
            why.append("a large holder sold down: overhang, sometimes a block looking for a buyer")
        elif kind == "control_change":
            e = 1.0
            why.append("a new parent or controlling shareholder: buy-outs of the remaining minority often follow")
        elif cat.get("activist"):
            e = 1.0
            why.append("a known activist crossed 10%")
        elif _corporate(holder):
            e = 0.5
            why.append("a company crossed 10%: a strategic holder, sometimes the eventual buyer")
        else:
            e = 0.25
            why.append("a holder crossed 10%: who it is matters")
    elif cid == "activist":
        e = 0.5
        if cat.get("activist"):
            e = 1.0
            why.append("known activist fund")
        if flags.get("creeping"):
            e += 0.5
            why.append("buying toward ⅓ without a bid (公開買付けに準ずる行為)")
        if flags.get("defense"):
            e += 0.5
            why.append("takeover-defence fight with an accumulator")
        if cat.get("control"):
            e += 0.5
            why.append("filed for influence over management")
    elif cid == "proxy":
        e = 1.0
        why.append("shareholder proposal or meeting request: proxy fights beat the market by 6% over three months (Korean backtest)")
        if cat.get("activist"):
            e += 0.5
            why.append("run by a known activist")
    elif cid == "lawsuit":
        e = 0.5
        why.append("court fight over control")
    elif cid == "spinoff":
        if cat.get("physical_split") or flags.get("physical_split"):
            e = -0.5
            why.append("business moved into a subsidiary (holding-company move): nothing handed to shareholders")
        elif cat.get("spinoff") or flags.get("spinoff"):
            e = 1.0
            why.append("spin-off: shareholders get the new shares")
        else:
            e = 0.5
    elif cid == "listing":
        if flags.get("resolved"):
            why.append("meets the listing criteria again: the exchange review is lifted (the earlier warning is resolved)")
        if flags.get("breach"):
            e = 0.5
            why.append("does not meet TSE listing criteria: MBOs, parent buy-outs and market changes cluster here")
            if (h.get("re_index") or 9) < 1 or (h.get("pb") or 9) < 0.8:
                e += 0.5
                why.append("cheap on assets while under listing pressure")
            if flags.get("improvement_period"):
                e += 0.5
                why.append("improvement period running")
            if cat.get("considering_options"):
                e += 0.5
                why.append("the plan mentions going private, the parent or a market change")
        if flags.get("upgrade"):
            e = max(e, 0.5)
            why.append("moving up to Prime: index and fund demand")
        if flags.get("voluntary_delisting"):
            e = max(e, 1.0)
            why.append("delisting: check how minorities are cashed out")
    elif cid == "buyback":
        b = h.get("buyback_vs_mcap")
        if b is not None:
            e = 2.5 if b >= 0.05 else 2.0 if b >= 0.03 else 1.5 if b >= 0.02 else 0.5 if b >= 0.01 else 0.0
            why.append(f"buyback {b*100:.1f}% of market cap")
        if h.get("mcap") and h["mcap"] < SMALL_CAP and (b or 0) >= 0.02:
            e += 0.5
            why.append("small cap: the buying is large against daily trading")
        if cat.get("to_cancel") or flags.get("to_cancel"):
            why.append("shares to be cancelled")
        e = min(e, 3.0)
        if cat.get("tostnet") or flags.get("tostnet") or cat.get("specific_holder") or flags.get("specific_holder"):
            e = min(e, 1.0)
            why.append("an off-market block from one holder (ToSTNeT), not buying in the market")
    elif cid == "cancellation":
        p = cat.get("pct_of_issued")
        if p is not None:
            e = 0.5 if p >= 5 else 0.0
            why.append(f"cancels {p:.1f}% of shares")
    elif cid == "dividend":
        y = h.get("div_yield") or 0
        e = 1.5 if y >= 7 else 1.0 if y >= 5 else 0.5 if y >= 4 else 0.0
        if y:
            why.append(f"{y:.1f}% yield")
        if (cat.get("raise_pct") or 0) >= 50 or cat.get("special") or cat.get("initiation") or flags.get("special") \
                or flags.get("initiation"):
            e += 0.5
            why.append("big raise, special or first dividend")
        elif cat.get("raise_pct") is not None:
            why.append(f"dividend {cat['raise_pct']:+.0f}% vs last year")
    elif cid == "valueup":
        ments = cat.get("mentions") or cat.get("keywords") or []
        e = 0.5
        if h.get("mcap") and h["mcap"] >= LARGE_CAP:
            e += 0.5
            why.append("large company: value-up plans moved large caps most (Korean backtest)")
        if any(x in ments for x in ("消却", "自己株式")) and any(x in ments for x in ("総還元性向", "DOE", "累進配当", "株主還元")):
            e += 0.5
            why.append("commits to buybacks or cancellations and a payout target")
        if any(x in ments for x in ("政策保有株式", "非事業用資産", "不動産の売却")):
            e += 0.5
            why.append("plans to sell cross-holdings or non-core property")
    elif cid == "guidance":
        oc = cat.get("op_chg")
        if oc is not None:
            e = 1.0 if oc >= 30 else 0.5 if oc >= 10 else 0.0
            why.append(f"operating-profit forecast {oc:+.0f}% vs "
                       + ("last year's result (first forecast after one was withheld)" if cat.get("first_forecast") else "the previous one"))
        if (cat.get("div_raise_pct") or 0) >= 20:
            e += 0.5
            why.append("dividend raised with it")
    elif cid == "contract":
        p = h.get("contract_pct")
        if p is not None:
            why.append(f"order {p:.0f}% of revenue")
            if p >= 100:
                why.append("orders this large were usually sold into (Korean backtest)")
    return e, why


def risk_points(adv: Optional[float], designation: Optional[str], warnings: list[str], group: str) -> tuple[float, list[str]]:
    p, why = 0.0, []
    if adv is not None:
        if adv < ADV_NONE:
            p += 2.0
            why.append("no trading in the last 20 sessions (suspended?)")
        elif adv < ADV_VERY_THIN:
            p += 1.5
            why.append(f"very illiquid: ¥{adv/1e6:.1f}M traded a day")
        elif adv < ADV_THIN:
            p += 0.5
            why.append(f"thin trading: ¥{adv/1e6:.1f}M a day")
    if designation:
        p += 1.0
        why.append("exchange designation: " + designation)
    if group == "special":
        bad = [w for w in (warnings or []) if not w.startswith("no ")]
        if bad:
            p += 0.5
            why.append(bad[0])
    return p, why


def value_inputs(h: dict) -> dict:
    return dict(net_mcap=h.get("asset_margin_vs_mcap"), ncav_mcap=h.get("ncav_vs_mcap"), re_index=h.get("re_index"), fit=h.get("fit"),
                pe=h.get("pe"), fcf3_yield=h.get("fcf3_yield"), pb=h.get("pb"), de=h.get("debt_equity"),
                op=h.get("op_income"), ni=h.get("net_income"), py=h.get("profitable_years"), yk=h.get("years_known"))


def rate(h: dict, kind: str, cid: str, group: str, cat: dict, flags: dict) -> tuple[Optional[int], list[str], Optional[dict]]:
    """1-5 stars, the reasons and the parts behind them. Warnings get no stars."""
    if group == "warning":
        return None, [], None
    e, e_why = edge_points(h, kind, cid, cat, flags)
    v, v_why = value_points(value_inputs(h))
    r, r_why = risk_points(h.get("adv"), h.get("designation"), h.get("warnings") or [], group)
    workout = cid in WORKOUTS
    v_used = 0.0 if workout else min(v, max(e, 0.0) + 1.0)
    total = 1.0 + e + v_used - r
    cap, floor = None, False
    if not h.get("passes", True):
        total, cap = min(total, 2.0), "fails the profit and debt filters: " + ", ".join(h.get("fail_reasons") or [])
    if e >= 0 and r < 1.0 and total < 1.75:
        total, floor = 1.75, True     # one star is kept for events that hurt minorities and for stocks you can't trade
    stars = stars_for(total)
    why = list(e_why)
    if v_why:
        why.append(v_why + ("" if v_used or workout else " (not counted without a stronger event)")
                   + (" (shown, not counted: the deal sets the price)" if workout else ""))
    why += r_why
    if cap:
        why.append(cap)
    rating = dict(edge=round(e, 2), value=round(v, 2), value_used=round(v_used, 2), risk=round(r, 2),
                  total=round(total, 2), workout=workout, cap=cap, floor=floor or None)
    return stars, why, rating


# ---------------------------------------------------------------- holdings

def days_between(a: str, b: str) -> int:
    try:
        return (dt.date.fromisoformat(b[:10]) - dt.date.fromisoformat(a[:10])).days
    except (TypeError, ValueError):
        return 9999


def holding_rating(snap: dict, events: list[dict], standing: list[tuple[float, str]], today: str,
                   designation: Optional[str] = None) -> tuple[int, dict]:
    """Stars for a holding on the radar's system: edge from its own radar events (last 120 days, half weight after 60)
    or its standing catalysts, value from its snapshot, risk from trading and recent warnings."""
    v, v_why = value_points(dict(net_mcap=snap.get("net_mcap"), ncav_mcap=snap.get("ncav_mcap"), re_index=snap.get("re_index"), fit=snap.get("fit"),
                                 pe=snap.get("pe"), fcf3_yield=snap.get("fcf3_yield"), pb=snap.get("pb"), de=snap.get("de"),
                                 op=snap.get("op"), ni=snap.get("ni"), py=snap.get("py"), yk=snap.get("yk")))
    e, e_why, src = 0.0, [], None
    for ev in events:
        if ev.get("warn") or ev.get("edge") is None:
            continue
        age = days_between(ev["date"], today)
        w = 1.0 if age <= 60 else 0.5 if age <= 120 else 0.0
        if ev["edge"] * w > e:
            e, src = ev["edge"] * w, ev
    if standing and standing[0][0] > e:
        e, src = standing[0][0], None
        e_why.append(standing[0][1])
    elif src:
        e_why.append(f"{src.get('label') or 'radar event'} on {src['date']}: {(src.get('headline') or '')[:90]}"
                     + (" (counts half: over 60 days old)" if days_between(src["date"], today) > 60 else ""))
    else:
        e_why.append("no catalyst: nothing on the radar in 120 days, no activist, buyback, listing pressure or 5%+ yield")
    e_why += [f"also: {r}" for _, r in standing[1:3]]
    r, r_why = risk_points(snap.get("adv"), designation, [], "holding")
    recent = [ev for ev in events if ev.get("warn") and days_between(ev["date"], today) <= 120]
    if any(ev["warn"] == "distress" for ev in recent):
        r += 1.0
        r_why.append("distress warning in the last 120 days")
    elif any(ev["warn"] == "dilution" for ev in recent):
        r += 0.5
        r_why.append("dilution in the last 120 days")
    v_used = min(v, e + 1.0)
    total = 1.0 + e + v_used - r
    floor = False
    if r < 1.0 and total < 1.75:
        total, floor = 1.75, True
    stars = stars_for(total)
    why = e_why + ([v_why + ("" if v_used >= v else f" (counts {v_used:.1f} of {v:.1f} without a stronger catalyst)")]
                   if v_why else ["no value test passed"]) + r_why
    return stars, dict(edge=round(e, 2), value=round(v, 2), value_used=round(v_used, 2), risk=round(r, 2),
                       total=round(total, 2), floor=floor or None, why=why)


def standing_catalysts(events_hist: list[dict], snap: dict, frontrun: Optional[dict], holders: list[dict],
                       activists, today: str) -> list[tuple[float, str]]:
    """What could unlock value beyond recent radar events, from two years of disclosures and the snapshot.
    The edge takes the strongest; they are not added."""
    out = []
    seen_act = set()
    for h in holders or []:
        for rx, disp, kind in activists or []:
            if kind in ("activist", "murakami") and rx.search(h.get("name") or "") and disp not in seen_act:
                seen_act.add(disp)
                out.append((1.0, f"known activist {disp} on the register ({h.get('pct', 0):.1f}%)"))
    counts: dict[str, list[str]] = {}
    for ev in events_hist:
        age = days_between(ev["date"], today)
        k = ev.get("kind")
        if age <= 365 and k in ("buyback", "cancellation", "value_up", "tender_offer", "squeeze_out", "strategic", "rumor",
                                "listing", "holding", "proxy_fight"):
            counts.setdefault(k, []).append(ev["date"])
        if age <= 730 and k == "holding" and (ev.get("flags") or {}).get("activist"):
            disp = ev["flags"]["activist"]
            if disp not in seen_act:
                seen_act.add(disp)
                out.append((1.0, f"known activist {disp} filed a 5% report ({ev['date']})"))
    bb = len(counts.get("buyback", [])) + len(counts.get("cancellation", []))
    if bb:
        out.append((1.0 if bb >= 3 else 0.5, f"{bb} buyback or cancellation decision{'s' if bb > 1 else ''} in the last 12 months"))
    if counts.get("value_up"):
        out.append((0.5, f"value-up / capital-cost plan disclosed {max(counts['value_up'])}"))
    if counts.get("tender_offer") or counts.get("squeeze_out"):
        out.append((1.0, f"tender offer disclosed {max(counts.get('tender_offer', []) + counts.get('squeeze_out', []))}"))
    if counts.get("strategic") or counts.get("rumor"):
        out.append((1.5, f"strategic review / press report on a deal {max(counts.get('strategic', []) + counts.get('rumor', []))}"))
    if counts.get("listing"):
        out.append((1.0, f"TSE listing-criteria plan or breach {max(counts['listing'])}: going private is one way out"))
    if counts.get("proxy_fight"):
        out.append((1.0, f"shareholder proposal {max(counts['proxy_fight'])}"))
    dy = snap.get("div_yield")
    if dy and dy >= 0.05:
        out.append((1.0 if dy >= 0.07 else 0.5, f"dividend yield {dy*100:.1f}%: paid to wait"))
    tp = snap.get("treasury")
    if tp and tp >= 10:
        out.append((0.5, f"treasury shares {tp:.1f}% of issued: cancellation or a block for an acquirer"))
    if frontrun and frontrun.get("prob", 0) >= 0.45:
        out.append((1.0, f"pre-deal radar {frontrun['prob']*100:.0f}%: " + "; ".join((frontrun.get("reasons") or [])[:2])))
    elif frontrun and "listed_subsidiary" in (frontrun.get("signals") or []):
        out.append((1.0, next(r for r in frontrun["reasons"] if r.startswith("listed subsidiary"))))
    return sorted(out, key=lambda x: -x[0])
