"""No-brainers: the owner's own rules on every listed company, plus the workouts on the deal board.

What the holdings have in common, written as rules and run over the weekly screen: a real business (profits, and
profits most years), no debt above equity, enough trading to buy, and then at least one reason from the list:
cash above the market cap with something coming out of it, a net-net that earns, the RE net-net index well below
one, a 4% dividend on a single-digit P/E below book, a cheap company shrinking its share count, an owner big enough
to take it private, or a cheap quality business with a catalyst filed. Workouts come from the deal board with the
return set by the bid. Claude's written review (nobrainers/current) adds tiers, a line on the business, a verdict,
and the names that pass the rules but should be avoided.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from .util import OKU

KINDS = {
    "cash": ("Cash > market cap", "cash + securities + investments cover the whole market cap after every liability, "
             "the business still earns, and something comes out of the pile (a dividend, treasury shares, a buyback or a catalyst)"),
    "netnet": ("Net-net with earnings", "net current assets at 1.2× the market cap or more, and a P/E of 12 or less"),
    "re": ("RE net-net, earning", "the real-estate net-net index at 0.6 or below (the ranking you screen on) with a P/E of 20 or less"),
    "div": ("4% dividend · P/E ≤ 10 · below book or cash", "a 4% forecast yield on a P/E of 10 or less, below book or with half the cap in net cash"),
    "buy": ("Cheap and shrinking its share count", "below 0.7× book, or the RE index at 0.8, or half the cap in net cash; and 10% of the shares in "
            "treasury or repeated buybacks or a cancellation in the last three years; P/E 15 or less"),
    "owner": ("Owner can take it private", "a holder between 30% and two thirds, cheap on assets (RE index 0.8, net cash 0.7× the cap or P/B 0.6), "
              "and a 3★+ pre-deal score or FIT 85+"),
    "quality": ("Cheap quality with a catalyst", "forward P/E 8 or less, a profit every year on record, free cash flow 6% of the cap, and a "
                "3★+ event on the radar in the last 120 days or a 4★+ pre-deal score"),
    "treasury": ("Treasury pile below book", "30% or more of the issued shares held in treasury at 0.7× book or less, still profitable: "
                 "a cancellation or a block sale to an acquirer rewrites the share count"),
    "wo": ("Workout", "an open bid on the deal board: 2% or more to the bid, or 1%+ worth 6% a year, or a contested situation (bump score 2+)"),
}
GATES = dict(min_adv=2e6, max_de=1.0, min_profit_share=0.8)


def _n(v) -> Optional[float]:
    return v if isinstance(v, (int, float)) and v == v else None


def _oku(v) -> str:
    return f"¥{v / OKU:,.0f}億" if _n(v) else "—"


def _pct(v, d=1) -> str:
    return f"{v:.{d}f}%" if _n(v) is not None else "—"


def general(r: dict, f, events: list[dict], hits: list[dict], fr: Optional[dict], today: dt.date) -> Optional[dict]:
    """One company against the rules; None when it passes no gate or no rule."""
    pe = _n(r.get("pe_fwd")) or _n(r.get("pe"))
    ni, py, yk = _n(r.get("ni")), r.get("profit_years") or 0, r.get("years") or 0
    if not _n(r.get("mcap")) or not r.get("source") or ni is None or ni <= 0:
        return None
    if yk >= 3 and py < GATES["min_profit_share"] * yk:
        return None                                      # losses in more than one of the recent years
    if f is not None and f.going_concern:
        return None
    de = (f.liabilities / f.equity) if f is not None and f.liabilities is not None and f.equity else None
    if de is not None and de > GATES["max_de"]:
        return None
    adv = _n(r.get("adv"))
    if adv is not None and adv < GATES["min_adv"]:
        return None
    fcf = [v for v in (f.fcf_history if f is not None else [])[:3] if v is not None]
    fcf3 = (sum(fcf) / len(fcf) / r["mcap"]) if fcf else None
    am, nc, ri, pb = _n(r.get("am_mcap")), _n(r.get("ncav_mcap")), _n(r.get("re_index")), _n(r.get("pb"))
    dy, tp, th, fit = _n(r.get("div_yield")), _n(r.get("treasury_pct")), _n(r.get("top_holder_pct")), _n(r.get("fit"))
    since = (today - dt.timedelta(days=3 * 365)).isoformat()
    buybacks = [e for e in events if e.get("kind") == "buyback" and (e.get("date") or "") >= since]
    cancels = [e for e in events if e.get("kind") == "cancellation" and (e.get("date") or "") >= since]
    recent = (today - dt.timedelta(days=120)).isoformat()
    cat = next((h for h in sorted(hits, key=lambda h: -(h.get("stars") or 0)) if (h.get("stars") or 0) >= 3
                and (h.get("date") or "") >= recent.replace("-", "")), None)
    pre = (fr or {}).get("stars") or 0
    kinds, why, check, flags = [], [], [], []
    pays = (dy is not None and dy >= 1.0) or (tp is not None and tp >= 3) or bool(buybacks) or bool(cat)
    if am is not None and am >= 1.0 and (fcf3 is None or fcf3 >= 0.03) and pe is not None and pe <= 20 and pays:
        kinds.append("cash")
        why.append(f"cash, securities and investments cover {am:.2f}× the market cap after every liability"
                   + (f"; free cash flow {fcf3 * 100:.0f}% of the cap a year" if fcf3 is not None else "")
                   + f"; P/E {pe:.1f}" + (f"; dividend {dy:.1f}%" if dy else "") + (f"; {tp:.1f}% in treasury" if tp else ""))
        check.append("that the cash is cash (deposits and listed securities, not loans to affiliates) and that the owner "
                     "will ever pay it out: a cash box with a deaf controller stays cheap")
    elif am is not None and am >= 1.0:
        flags.append("cash above the market cap, but nothing comes out: no dividend to speak of, no treasury stock, no buyback, no catalyst"
                     if not pays else f"cash above the market cap but P/E {pe:.0f}" if pe else "cash above the market cap but earnings not read")
    if nc is not None and nc >= 1.2 and pe is not None and pe <= 12:
        kinds.append("netnet")
        why.append(f"net current assets {nc:.2f}× the market cap (Graham's test) and it earns: P/E {pe:.1f}")
        check.append("what the current assets are (inventory and receivables melt at a loss-maker; cash does not) and whether the controller is buying")
    if ri is not None and 0 < ri <= 0.6 and pe is not None and pe <= 20:
        kinds.append("re")
        why.append(f"RE net-net index {ri:.2f}: market cap at {ri * 100:.0f}% of net current assets plus real estate at fair value; P/E {pe:.1f}")
        check.append("the rental-property note (what the fair value rests on) and whether the land is operating land the company will never sell")
    if dy is not None and dy >= 4.0 and pe is not None and pe <= 10 and ((pb is not None and pb <= 1.0) or (am is not None and am >= 0.5)):
        kinds.append("div")
        why.append(f"{dy:.1f}% forecast dividend on P/E {pe:.1f}" + (f", P/B {pb:.2f}" if pb is not None else "")
                   + (f", net cash {am:.2f}× the cap" if am is not None and am >= 0.5 else ""))
        check.append("the dividend record and payout policy in the annual report: the yield is on this year's forecast")
    shrinking = (tp is not None and tp >= 10) or len(buybacks) >= 2 or bool(cancels)
    if shrinking and pe is not None and pe <= 15 and ((pb is not None and pb <= 0.7) or (ri is not None and 0 < ri <= 0.8) or (am is not None and am >= 0.5)):
        kinds.append("buy")
        bits = []
        if tp is not None and tp >= 10:
            bits.append(f"{tp:.1f}% of the issued shares held in treasury")
        if buybacks:
            bits.append(f"{len(buybacks)} buyback decisions in three years (last {buybacks[0].get('date')})")
        if cancels:
            bits.append(f"cancelled shares ({cancels[0].get('date')})")
        why.append("; ".join(bits) + f"; P/E {pe:.1f}" + (f", P/B {pb:.2f}" if pb is not None else ""))
        check.append("whether the treasury shares get cancelled or sit as the owner's war chest, and who sells into the buybacks")
    if th is not None and 30 <= th <= 67 and ((ri is not None and 0 < ri <= 0.8) or (am is not None and am >= 0.7) or (pb is not None and pb <= 0.6)) \
            and (pre >= 3 or (fit is not None and fit >= 85)):
        kinds.append("owner")
        why.append(f"{r.get('top_holder') or 'the top holder'} owns {th:.1f}%" + (f"; pre-deal {pre}★" if pre else "")
                   + (f"; FIT {fit:.0f}" if fit is not None else "") + ": cheap enough on assets for an MBO or a parent buy-out to pay for itself")
        check.append("the owner's age and succession (the annual report lists the officers), and any value-up plan the exchange has asked for")
    if tp is not None and tp >= 30 and pb is not None and pb <= 0.7:
        kinds.append("treasury")
        why.append(f"{tp:.1f}% of the issued shares sit in treasury at P/B {pb:.2f}" + (f"; RE index {ri:.2f}" if ri is not None else "")
                   + (f"; net cash {am:.2f}× the cap" if am is not None and am > 0 else ""))
        check.append("what the company says it will do with the treasury shares (cancel, sell to a partner, or nothing) and whether the buybacks go on")
    if pe is not None and pe <= 8 and yk >= 5 and py == yk and fcf3 is not None and fcf3 >= 0.06 and (cat or pre >= 4):
        kinds.append("quality")
        why.append(f"P/E {pe:.1f}, profitable every one of the last {yk} years, free cash flow {fcf3 * 100:.0f}% of the cap, and "
                   + (f"a {cat.get('stars')}★ event on the radar: {cat.get('headline') or cat.get('kind_label')}" if cat else f"a {pre}★ pre-deal score"))
        check.append("whether last year's profit is the run rate (this year's quarterly reports) and the timing of the catalyst")
    if not kinds:
        return None
    if cat and "quality" not in kinds:
        why.append(f"on the radar ({cat.get('date')}): {cat.get('headline') or cat.get('kind_label')}")
    if fr and fr.get("reasons") and "owner" not in kinds and pre >= 3:
        why.append(f"pre-deal {pre}★: " + "; ".join(fr["reasons"][:2]))
    if adv is None:
        flags.append("trading volume not read: check it can be bought")
    elif adv < 1e7:
        flags.append(f"thin: ¥{adv / 1e6:.1f}M a day")
    if de is not None and de > 0.7:
        flags.append(f"liabilities {de:.1f}× equity")
    if yk and py < yk:
        flags.append(f"profitable {py} of the last {yk} years")
    live = dict(price=r.get("price"), price_date=r.get("price_date"), mcap=r.get("mcap"), pe=r.get("pe"), pe_fwd=r.get("pe_fwd"), pb=pb,
                am_mcap=am, ncav_mcap=nc, re_index=ri, div_yield=dy, treasury_pct=tp, top_holder_pct=th, adv=adv, fit=fit,
                fcf3=round(fcf3, 4) if fcf3 is not None else None, pre=pre or None)
    score = len(kinds) * 10 + (fit or 0) / 10 + (pre or 0)
    return dict(code=r["code"], name=r.get("name"), market=r.get("market"), kinds=kinds, why=why, check=check, flags=flags,
                score=round(score, 1), top=len(kinds) >= 3 or (len(kinds) == 2 and fit is not None and fit >= 90), live=live, mine=bool(r.get("mine")))


def workout(d: dict, today: dt.date) -> Optional[dict]:
    if d.get("status") not in ("open", "announced") or not _n(d.get("price")) or not _n(d.get("last")):
        return None
    spread, days = _n(d.get("spread")), d.get("days_left")
    ann = (spread / max(days, 1) * 365) if spread is not None and days is not None and days > 0 else None
    contested = (d.get("bump_score") or 0) >= 2
    if not ((spread is not None and spread >= 0.02) or (ann is not None and ann >= 0.06 and spread >= 0.01) or contested):
        return None
    kind = {"tender_offer": "Tender offer", "self_tender": "Self-tender", "squeeze_out": "Squeeze-out"}.get(d.get("kind"), d.get("kind"))
    why = [f"{kind}" + (" (MBO)" if d.get("mbo") else "") + (f" by {d['bidder']}" if d.get("bidder") else "")
           + f": bid ¥{d['price']:,.0f}, last ¥{d['last']:,.0f} ({d.get('last_date') or 'last close'}), {spread * 100:+.1f}% to the bid"
           + (f", {ann * 100:.0f}% a year with {days} days to {d['end']}" if ann is not None and spread > 0 else (f", ends {d['end']}" if d.get("end") else ""))]
    if d.get("premium") is not None:
        why.append(f"premium {d['premium'] * 100:+.0f}% to the close before the first filing")
    if contested:
        why.append("contested: " + ", ".join(x for x in d.get("flags") or [] if x not in ("partial",)))
    check = []
    if d.get("partial"):
        check.append("partial bid: only part of what you tender is bought; the rest stays listed")
    if d.get("lower_limit"):
        check.append(f"minimum of {d['lower_limit']:,.0f} shares must be tendered or the bid lapses")
    if d.get("board_view") not in ("support", None):
        check.append(f"board view: {d['board_view']}")
    if spread is not None and spread < 0:
        check.append("the market trades above the bid: a raise is expected, and nothing is owed if it never comes")
    live = dict(price=d.get("last"), bid=d.get("price"), spread=spread, days_left=days, end=d.get("end"), offer_pb=d.get("offer_pb"),
                offer_net_cash=d.get("offer_net_cash"), bump_score=d.get("bump_score"))
    return dict(code=d["code"], name=d.get("name"), kinds=["wo"], why=why, check=check, flags=list(d.get("flags") or []),
                score=round((d.get("bump_score") or 0) * 10 + (spread or 0) * 100, 1), top=contested or (spread or 0) >= 0.05, live=live,
                status=d.get("status"))


def build(ctx, value: dict, frontrun_rows: list[dict], deals_rows: list[dict], hits: list[dict], today: dt.date,
          mine: Optional[set] = None, max_generals: int = 150) -> dict:
    financial = {c.code for c in ctx.universe if getattr(c, "financial", False)}
    fr_by = {r["code"]: r for r in frontrun_rows}
    hits_by: dict[str, list] = {}
    for h in hits:
        hits_by.setdefault(h.get("code"), []).append(h)
    gens = []
    for r in value.get("rows_all") or []:
        if r["code"] in financial:
            continue
        r = dict(r, mine=r["code"] in (mine or set()))
        fr = fr_by.get(r["code"])
        g = general(r, ctx.store.fundamentals(r["code"]), (fr or {}).get("events") or [], hits_by.get(r["code"], []), fr, today)
        if g:
            gens.append(g)
    gens.sort(key=lambda g: (-g["top"], -len(g["kinds"]), -g["score"]))
    gens = gens[:max_generals]
    wos = [w for w in (workout(d, today) for d in deals_rows) if w]
    wos.sort(key=lambda w: (-w["top"], -w["score"]))
    rows = wos + gens
    for i, x in enumerate(rows):
        x["rank"] = i + 1
    meta = (value.get("meta") or {})
    return dict(date=today.isoformat(), price_date=meta.get("price_date"), screened=len(value.get("rows_all") or []),
                workouts=len(wos), generals=len(gens), top=sum(1 for x in rows if x["top"]), rows=rows, rules=KINDS, gates=GATES)
