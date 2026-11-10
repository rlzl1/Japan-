"""The special-situations board: every open corporate action whose return is set by the deal, not the market.

  tender       cash tender offers for the company (going-private bids, MBOs, parent buy-outs, partial bids)
  self_tender  the company's own tender for its shares
  squeeze      the cash-out after a bid (株式併合 / 売渡請求): the price is fixed, only the vote and the date remain
  after        the weeks between a successful going-private tender and its squeeze-out: the stub trades below the
               bid although the cash-out comes at the same price
  ratio        share exchanges, mergers and share transfers between listed companies: the target trades at a spread
               to the exchange ratio times the acquirer's price
  liquidation  the listed company dissolves: the stated distribution (or net current assets) against the price
  return       cash returned from capital surplus (有償減資 / 資本剰余金配当) per share
  reverse      reverse mergers and backdoor listings (the exchange's grace period for 不適当な合併等)
  spinoff      spin-offs that hand shareholders the new shares

Tenders, self-tenders and squeeze-outs come from the deal memory (deals.py); the other kinds are kept here from the
first filing to the end. Each row carries the terms, today's spread, the days to the cash, the annualised return,
a risk grade (A: agreed cash deal with nothing unusual; B: one open question; C: contested, doubtful or unread) and
what can break it.
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
from typing import Iterable, Optional

from .classify import classify
from .deals import DealBook, doc_text
from .docparse import parse_catalyst, tight, to_num
from .util import nfkc, norm_code, parse_date

SIT_TYPE = {"share_swap": "ratio", "merger": "ratio", "liquidation": "liquidation", "capital_return": "return",
            "reverse_merger": "reverse", "split": "spinoff"}
TYPE_LABEL = {"tender": "Tender offer", "self_tender": "Self-tender", "squeeze": "Squeeze-out", "after": "After the tender",
              "ratio": "Exchange ratio", "liquidation": "Liquidation", "return": "Cash return", "reverse": "Reverse merger",
              "spinoff": "Spin-off"}
DEAL_TYPE = {"tender_offer": "tender", "self_tender": "self_tender", "squeeze_out": "squeeze"}
KEEP_DONE_DAYS = 30
MEMORY_DAYS = 300
AFTER_DAYS = 150            # how long a closed going-private tender is followed to its squeeze-out
DONE = re.compile(r"効力発生|完了|結了|終了")
CALLED_OFF = re.compile(r"中止|解消|解約|撤回|白紙|取りやめ")


def parse_return(text: str) -> dict:
    """Cash per share and the record date of a capital return."""
    t = tight(text)
    out: dict = {}
    m = re.search(r"1株(?:当たり|あたり|につき)(?:の)?(?:配当金?|払戻金?|交付金?)?(?:の額)?[^0-9]{0,24}?([\d,]+(?:\.\d+)?)円", t)
    out["per_share"] = to_num(m.group(1)) if m else None
    m = re.search(r"基準日[^0-9]{0,10}(\d{4}年\d{1,2}月\d{1,2}日)", t)
    d = parse_date(m.group(1)) if m else None
    out["record_date"] = d.isoformat() if d else None
    m = re.search(r"(?:効力発生日|支払開始日|払戻(?:し)?(?:の)?日)[^0-9]{0,10}(\d{4}年\d{1,2}月\d{1,2}日)", t)
    d = parse_date(m.group(1)) if m else None
    out["pay_date"] = d.isoformat() if d else None
    return out


class SituationBook:
    """Share exchanges, mergers, liquidations, cash returns, reverse mergers and spin-offs, followed across filings."""

    def __init__(self, store):
        self.store = store
        data = store.read_json("situations.json", {}) or {}
        self.sits: dict[str, dict] = data.get("sits") or {}

    def save(self, today: str) -> None:
        cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=MEMORY_DAYS)).isoformat()
        self.sits = {k: s for k, s in self.sits.items() if (s.get("last_date") or s.get("first_date") or today) >= cutoff}
        self.store.write_json("situations.json", dict(sits=self.sits, updated=today))

    def load_memory(self, sits: dict) -> None:
        for k, s in (sits or {}).items():
            cur = self.sits.get(k)
            if cur is None or (s.get("last_date") or "") > (cur.get("last_date") or ""):
                self.sits[k] = s

    def live(self, code: str, typ: Optional[str] = None) -> Optional[dict]:
        c = [s for s in self.sits.values() if s["code"] == code and (typ is None or s["type"] == typ)
             and s.get("status") not in ("done", "withdrawn")]
        return max(c, key=lambda s: s.get("last_date") or "") if c else None

    def update(self, ctx, items: Iterable, names, max_reads: int = 30, log_=print) -> int:
        reads = n = 0
        for d in sorted(items, key=lambda x: x.time):
            title = nfkc(d.title)
            tag = classify(d.title, d.code)
            if not tag:
                continue
            code = norm_code(d.code)
            if tag.signal == "warning":
                if tag.kind == "withdrawn":
                    s = self.live(code)
                    if s:
                        s.update(status="withdrawn", last_date=d.date.isoformat(), last_title=title[:120], last_url=d.pdf_url)
                continue
            typ = SIT_TYPE.get(tag.kind)
            if not typ or (typ == "spinoff" and not tag.flags.get("spinoff")):
                continue
            subject, counter = code, None
            if typ == "ratio":
                if tag.role == "acquirer":
                    other = tag.target_code or names.find(title, exclude=code)
                    if not other:
                        continue                    # an unlisted company absorbed: nothing to trade
                    subject, counter = norm_code(other), code
            if subject not in ctx.universe:
                continue
            s = self.live(subject, typ)
            if s is None:
                if DONE.search(title) and typ != "reverse":
                    continue                        # the end of something never seen from the start
                key = f"{subject}:{typ}:{d.ymd}"
                comp = ctx.universe.get(subject)
                s = self.sits.setdefault(key, dict(key=key, code=subject, type=typ, kind=tag.kind,
                                                    name=comp.name if comp else subject, first_date=d.date.isoformat(),
                                                    status="open", docs=[]))
            if counter and not s.get("counter"):
                s["counter"] = counter
            need = (typ == "ratio" and not s.get("ratio_target")) or (typ == "liquidation" and s.get("per_share") is None) \
                or (typ == "return" and s.get("per_share") is None) or not s.get("docs")
            cat = ctx.store.parsed(d.id)
            if (cat is None or (need and not cat.get("doc_read"))) and need and reads < max_reads:
                text = doc_text(ctx, d)
                reads += 1
                cat = parse_catalyst(tag.kind, text or "")
                if typ == "return" and text:
                    cat.update(parse_return(text))
                cat["doc_read"] = bool(text)
                ctx.store.put_parsed(d.id, cat)
            self._merge(s, cat or {}, d, title, ctx)
            n += 1
        if reads:
            log_(f"situations: {reads} documents read")
        return n

    def _merge(self, s: dict, cat: dict, d, title: str, ctx) -> None:
        day = d.date.isoformat()
        typ = s["type"]
        if typ == "ratio":
            if cat.get("ratio_target") and not s.get("ratio_target"):
                s["ratio_target"], s["ratio_parent"] = cat["ratio_target"], cat.get("ratio_parent") or 1
            if not s.get("counter"):
                for c in cat.get("codes") or []:
                    c = norm_code(c)
                    if c != s["code"] and c in ctx.universe:
                        s["counter"] = c
                        break
            for k in ("effective", "delisting", "wholly_owned"):
                if cat.get(k) and not s.get(k):
                    s[k] = cat[k]
        elif typ == "liquidation":
            if cat.get("distribution_per_share") and s.get("per_share") is None:
                s["per_share"] = cat["distribution_per_share"]
            if cat.get("reason") and not s.get("reason"):
                s["reason"] = cat["reason"]
        elif typ == "return":
            per = cat.get("per_share") or cat.get("dps")
            if per and s.get("per_share") is None:
                s["per_share"] = per
            for k in ("record_date", "pay_date"):
                if cat.get(k) and not s.get(k):
                    s[k] = cat[k]
        elif typ in ("reverse", "spinoff"):
            for k in ("effective", "backdoor", "spinoff"):
                if cat.get(k) and not s.get(k):
                    s[k] = cat[k]
        if CALLED_OFF.search(title):
            s["status"] = "withdrawn"
        elif DONE.search(title) and typ in ("ratio", "liquidation", "return", "spinoff"):
            s["status"] = "done"
        s["last_date"], s["last_title"], s["last_url"] = day, title[:120], d.pdf_url
        if d.id not in [x.get("id") for x in s["docs"]]:
            s["docs"] = (s["docs"] + [dict(id=d.id, date=day, title=title[:90], url=d.pdf_url)])[-10:]


# ---------------------------------------------------------------- grading
def grade_cash(r: dict) -> tuple[str, list[str], list[str]]:
    """Risk grade for tenders, self-tenders, squeeze-outs and after-tender stubs: (grade, what helps, what can break it)."""
    plus, con, c_level = [], [], 0          # c_level: 0 → A, 1 → B, 2 → C
    sp = r.get("spread")
    if r.get("price") is None:
        con.append("bid price not read from the filing: open it"); c_level = 2
    if r.get("status") == "announced":
        con.append("announced, not yet launched: approvals or conditions are outstanding"); c_level = max(c_level, 1)
    if r.get("partial"):
        con.append("partial bid: only part of what you tender is bought, the rest stays listed"); c_level = max(c_level, 1)
    if r["type"] == "self_tender":
        con.append("self-tender: pro-rated if oversubscribed, usually priced below the market, and the gain can be taxed "
                   "as a deemed dividend"); c_level = max(c_level, 1)
    bv = r.get("board_view")
    if bv == "oppose":
        con.append("the board opposes the bid: hostile, outcome open"); c_level = 2
    elif bv in ("neutral", "reserve"):
        con.append("the board does not recommend tendering"); c_level = max(c_level, 1)
    elif bv == "support":
        plus.append("board supports and recommends tendering")
    if sp is not None:
        if sp > 0.10 and r["type"] != "after":
            con.append(f"{sp * 100:.0f}% to an agreed bid: the market doubts it closes; read the conditions before counting it")
            c_level = 2
        elif sp < -0.03:
            con.append(f"trades {-sp * 100:.0f}% above the bid: you are paying for a raise or a rival bid that may not come")
            c_level = 2
        elif sp < -0.005:
            con.append("trades above the bid: a small raise is priced in"); c_level = max(c_level, 1)
        elif sp < 0.01:
            con.append("under 1% to the bid: costs and a slipped date eat it"); c_level = max(c_level, 1)
    if r.get("lower_limit") and r["type"] == "tender" and not r.get("parent"):
        con.append(f"minimum condition: lapses if fewer than {r['lower_limit']:,.0f} shares are tendered")
    if r.get("parent"):
        plus.append("the bidder is the parent: it already controls the vote")
    if r.get("mbo"):
        plus.append("MBO: management and its backers are the buyers")
    if r["type"] == "squeeze":
        plus.append("cash-out after a completed bid: only the shareholder vote and the date remain")
    if r["type"] == "after":
        plus.append("the tender succeeded: the squeeze-out pays the same price to whoever is left")
        con.append("the cash-out date is not set until the meeting is called: the wait can run three to four months")
        c_level = max(c_level, 1) if (sp or 0) < 0.01 else c_level
    if r.get("bumps"):
        plus.append(f"already raised {r['bumps']}×")
    if r.get("activists"):
        plus.append("activist on the register: " + ", ".join(r["activists"]))
    return "ABC"[c_level], plus, con


def _ann(spread: Optional[float], days: Optional[int]) -> Optional[float]:
    if spread is None or days is None or days <= 0 or spread <= 0:
        return None
    return round(spread / max(days, 5) * 365, 4)


def _price(ctx, code: str) -> tuple[Optional[float], Optional[str]]:
    q = ctx.prices.quotes.get(code)
    return (q.price, q.price_date) if q and q.price else (None, None)


def board(ctx, deals: DealBook, sits: SituationBook, today: str, activists=None) -> dict:
    t0 = dt.date.fromisoformat(today)
    rows = []
    live_rows = deals.mark(ctx, today, activists)
    squeezing = {r["code"] for r in live_rows if r.get("kind") == "squeeze_out" and r.get("status") in ("open", "announced")}
    for r in live_rows:
        typ = DEAL_TYPE.get(r.get("kind"), "tender")
        row = dict(r, type=typ, date=r.get("end"), days=r.get("days_left"), counter_name=r.get("bidder"))
        row["ann"] = _ann(r.get("spread"), r.get("days_left"))
        row["grade"], row["plus"], row["concerns"] = grade_cash(row)
        row["terms"] = (f"¥{r['price']:,.0f} cash" if r.get("price") else "price not read") + (" · partial" if r.get("partial") else "")
        rows.append(row)
    # the stub after a successful going-private tender, until its squeeze-out is filed
    for d in deals.deals.values():
        if d.get("kind") != "tender_offer" or d.get("status") != "closed" or d.get("success") is False or not d.get("price"):
            continue
        if not d.get("delisting") or d["code"] in squeezing or not d.get("last_date"):
            continue
        age = (t0 - dt.date.fromisoformat(d["last_date"])).days
        last, last_date = _price(ctx, d["code"])
        if age > AFTER_DAYS or not last or (last_date or "") < d["last_date"]:
            continue                                # delisted already, or no trade since the result
        sp = round(d["price"] / last - 1, 4)
        if abs(sp) > 0.15:
            continue                                # not pinned to the bid: something else is going on
        row = dict(key=d["key"] + ":after", type="after", code=d["code"], name=d["name"], status="open", price=d["price"],
                   last=last, px_date=last_date, spread=sp, counter_name=d.get("bidder"), mbo=d.get("mbo"), parent=d.get("parent"),
                   date=None, days=max(90 - age, 30), terms=f"¥{d['price']:,.0f} cash at the squeeze-out",
                   last_date=d.get("last_date"), last_title=d.get("last_title"), last_url=d.get("last_url"),
                   first_date=d.get("first_date"), flags=[])
        row["ann"] = _ann(sp, row["days"])
        row["grade"], row["plus"], row["concerns"] = grade_cash(row)
        rows.append(row)
    for s in sits.sits.values():
        typ = s["type"]
        last, last_date = _price(ctx, s["code"])
        row = dict(key=s["key"], type=typ, code=s["code"], name=s["name"], status=s.get("status") or "open", last=last,
                   px_date=last_date, last_date=s.get("last_date"), last_title=s.get("last_title"), last_url=s.get("last_url"),
                   first_date=s.get("first_date"), flags=[], plus=[], concerns=[], grade="B")
        if typ == "ratio":
            cq, _ = _price(ctx, s["counter"]) if s.get("counter") else (None, None)
            comp = ctx.universe.get(s["counter"]) if s.get("counter") else None
            row.update(counter=s.get("counter"), counter_name=comp.name if comp else None, date=s.get("effective"))
            if s.get("effective"):
                row["days"] = (dt.date.fromisoformat(s["effective"]) - t0).days
                if row["days"] < -3 and row["status"] == "open":
                    row["status"] = s["status"] = "done"
            if s.get("ratio_target") and cq and last:
                implied = s["ratio_target"] / (s.get("ratio_parent") or 1) * cq
                sp = implied / last - 1
                row.update(price=round(implied, 1), spread=round(sp, 4), ann=_ann(sp, row.get("days")),
                           terms=f"1 : {s['ratio_target']:g} × ¥{cq:,.0f}")
                if abs(sp) > 0.25:
                    row["grade"] = "C"
                    row["concerns"].append(f"implied value is {sp * 100:+.0f}% from the market: the ratio's direction or the "
                                           "counterparty was probably misread; open the filing")
                else:
                    row["concerns"].append("unhedged you are long the acquirer until the effective date: the spread is only "
                                           "locked by shorting it")
                    if sp < 0.01:
                        row["concerns"].append("under 1%: nothing left after costs")
                    row["plus"].append("the ratio is fixed: the target follows the acquirer's price")
            else:
                row["grade"] = "C"
                row["terms"] = f"1 : {s['ratio_target']:g}" if s.get("ratio_target") else "ratio not read"
                row["concerns"].append("ratio or listed counterparty not read from the filing: open it")
            if s.get("delisting"):
                row["flags"].append("target delists")
        elif typ == "liquidation":
            per = s.get("per_share")
            f = ctx.store.fundamentals(s["code"])
            if per and last:
                row.update(price=per, spread=round(per / last - 1, 4), terms=f"¥{per:,.0f} a share stated")
                row["concerns"].append("the distribution is the company's estimate: taxes, costs and timing can move it")
            else:
                from .value import net_current_assets, shares_now
                est = None
                if f is not None:
                    q = ctx.prices.quotes.get(s["code"])
                    issued, treasury = shares_now(f, q)
                    nca = net_current_assets(f)
                    if nca and issued and issued - (treasury or 0) > 0:
                        est = nca / (issued - (treasury or 0))
                row["grade"] = "C"
                if est and last:
                    row.update(price=round(est), spread=round(est / last - 1, 4), terms=f"no figure given · net current assets ≈ ¥{est:,.0f} a share")
                else:
                    row["terms"] = "distribution not stated"
                row["concerns"].append("no distribution stated: the figure is net current assets from the last balance sheet, "
                                       "before liquidation costs and taxes")
            if s.get("reason"):
                row["flags"].append(s["reason"][:60])
        elif typ == "return":
            per = s.get("per_share")
            row.update(date=s.get("record_date") or s.get("pay_date"))
            if row["date"]:
                row["days"] = (dt.date.fromisoformat(row["date"]) - t0).days
                if row["days"] < -3 and row["status"] == "open":
                    row["status"] = s["status"] = "done"
            if per and last:
                row.update(price=per, yield_=round(per / last, 4), terms=f"¥{per:,.0f} a share in cash ({per / last * 100:.1f}% of the price)")
                row["plus"].append("paid from capital surplus: for many holders a return of capital, not taxed as a dividend")
                row["concerns"].append("the share price drops by about the same amount on the ex-date: the gain is the tax treatment "
                                       "and what the return says about the owner")
            else:
                row["grade"], row["terms"] = "C", "amount not read"
        elif typ == "reverse":
            row.update(terms="reverse merger / backdoor listing", grade="C")
            row["concerns"].append("the exchange gives a grace period to re-qualify (実質的存続性): failure means delisting; "
                                   "success means a new business in an old shell")
        elif typ == "spinoff":
            row.update(terms="spin-off: holders receive the new shares", date=s.get("effective"))
            if s.get("effective"):
                row["days"] = (dt.date.fromisoformat(s["effective"]) - t0).days
            row["plus"].append("spin-offs are sold by holders who never chose them: the stub or the new share is often mispriced")
        rows.append(row)
    cutoff = (t0 - dt.timedelta(days=KEEP_DONE_DAYS)).isoformat()
    live = ("open", "announced")
    rows = [r for r in rows if r.get("status") in live or max(r.get("last_date") or "", r.get("date") or "") >= cutoff
            or (r.get("type") in ("tender", "self_tender", "squeeze") and (r.get("last_date") or "") >= cutoff)]
    order = {"A": 0, "B": 1, "C": 2}
    rows.sort(key=lambda r: (r.get("status") not in live, order.get(r.get("grade"), 3), -(r.get("ann") or 0), -(r.get("spread") or -9)))
    for r in rows:
        r.pop("docs", None)
    return dict(date=today, rows=rows, live=sum(1 for r in rows if r.get("status") in live), memory=sits.sits,
                stats=stats(deals), labels=TYPE_LABEL)


def stats(deals: DealBook) -> dict:
    """What the bids in memory did: how many were raised, failed or called off, and the premiums paid."""
    ds = [d for d in deals.deals.values() if d.get("kind") == "tender_offer"]
    if not ds:
        return {}
    closed = [d for d in ds if d.get("status") in ("closed", "ended")]
    prem = [d["price"] / d["ref_price"] - 1 for d in ds if d.get("price") and d.get("ref_price")]
    return dict(since=min(d["first_date"] for d in ds), bids=len(ds), closed=len(closed),
                raised=sum(1 for d in ds if d.get("bumps")), failed=sum(1 for d in ds if d.get("success") is False),
                withdrawn=sum(1 for d in ds if d.get("status") == "withdrawn"),
                hostile=sum(1 for d in ds if d.get("board_view") == "oppose"),
                partial=sum(1 for d in ds if d.get("partial")),
                median_premium=round(statistics.median(prem), 3) if prem else None)
