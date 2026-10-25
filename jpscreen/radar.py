"""The daily filing radar: every TDnet disclosure in the window, classified, sized, filtered and rated.

  disclosures -> classify (classify.py) -> read the PDF (docparse.py) -> price (prices.py)
              -> fundamentals (store / EDINET / earnings XBRL) -> evaluate + rate (rate.py) -> hits

Earnings releases (決算短信) in the window are harvested for their XBRL balance sheets on the way, so the
fundamentals store fills up even without an EDINET key.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from .classify import CAT_LABEL, CATALYST_LABEL, Tag, classify
from .docparse import parse_catalyst
from .deals import DealBook, bidder_from_title
from .fundamentals import from_facts
from .prices import PriceBook
from .rate import LIMITS, evaluate, fin_line, headline
from .store import Store
from .tdnet import TDnet, Disclosure
from .universe import Universe
from .util import JST, nfkc, norm_code

log = logging.getLogger(__name__)

READ_DOCS = {"tender_offer", "self_tender", "squeeze_out", "rumor", "strategic", "control_change", "major_holder", "holding",
             "proxy_fight", "control_dispute", "merger", "share_swap", "split", "reverse_merger", "liquidation",
             "listing", "buyback", "cancellation", "dividend", "value_up", "guidance", "asset_sale", "contract",
             "capital_return"}
NAME_ONLY_WARNINGS = {"distress", "withdrawn", "double_listing", "dividend_cut"}
DEAL_KINDS = {"tender_offer", "self_tender", "squeeze_out", "share_swap", "merger"}


@dataclass
class Context:
    tdnet: TDnet
    universe: Universe
    prices: PriceBook
    store: Store
    activists: list = field(default_factory=list)
    mine: dict = field(default_factory=dict)          # user's watchlist: code -> note
    value_tags: dict = field(default_factory=dict)    # code -> [list ids] from the value screen
    value_rows: dict = field(default_factory=dict)    # code -> value row dict (re_index etc.)
    limits: dict = field(default_factory=lambda: dict(LIMITS))
    max_docs: int = 800
    max_kessan: int = 800



class NameIndex:
    def __init__(self, universe: Universe):
        self.names = []
        for c in universe:
            nm = re.sub(r"株式会社|\(株\)|㈱|ホールディングス$", "", c.name).strip()
            if len(nm) >= 3:
                self.names.append((nm, c.code))
        self.names.sort(key=lambda x: -len(x[0]))

    def find(self, title: str, exclude: str = "") -> Optional[str]:
        t = nfkc(title)
        for nm, code in self.names:
            if code != exclude and nm in t:
                return code
        return None


def harvest_kessan(ctx: Context, items: list[Disclosure], log_=print) -> int:
    """Balance sheets (and full-year P&L) from the earnings releases in the window."""
    ctx.tdnet.prefetch([d.xbrl_url for d in items if "決算短信" in d.title and d.xbrl_url
                        and d.code in ctx.universe][:ctx.max_kessan])
    n = 0
    for d in items:
        if n >= ctx.max_kessan:
            break
        if "決算短信" not in d.title or not d.xbrl_url or d.code not in ctx.universe:
            continue
        if "REIT" in d.title or "訂正" in d.title:
            continue
        fs = ctx.tdnet.xbrl_facts(d)
        if not fs or len(fs) == 0:
            continue
        f = from_facts(fs, d.code, f"tdnet:{d.id}", annual=False)
        annual = not re.search(r"四半期|中間|第[1-3１-３]", d.title)
        if f.total_assets is None and f.liabilities is None:
            continue
        ctx.store.put_fundamentals(d.code, f, d.date, annual=False, pl_annual=annual)
        n += 1
    log_(f"harvested {n} earnings-release balance sheets")
    return n


def scan(ctx: Context, start: dt.date, end: dt.date, log_=print) -> dict:
    items = ctx.tdnet.list_range(start, end)
    log_(f"{len(items)} TDnet disclosures {start}..{end}")
    harvest_kessan(ctx, items, log_)
    names = NameIndex(ctx.universe)
    book = DealBook(ctx.store)
    if not book.deals:          # first run: the last weeks' deals, so follow-up filings carry their terms
        book.update(ctx, ctx.tdnet.list_range(start - dt.timedelta(days=45), start - dt.timedelta(days=1)), names,
                    max_reads=150, log_=log_)
    book.update(ctx, items, names, log_=log_)

    tagged: list[tuple[Disclosure, Tag, str]] = []
    for d in items:
        tag = classify(d.title, d.code)
        if not tag:
            continue
        subject = tag.target_code or d.code
        if tag.role == "acquirer" and not tag.target_code and tag.kind in ("tender_offer", "share_swap", "merger"):
            other = names.find(d.title, exclude=d.code)
            if not other:
                if tag.kind == "tender_offer":
                    continue        # a bid for an unlisted company: not a situation in the bidder's stock
            else:
                subject = other
        if subject not in ctx.universe:
            continue            # ETFs, REITs, PRO Market, foreign listings
        tagged.append((d, tag, norm_code(subject)))

    # target-side documents first (they carry the board's view), then de-duplicate per company/kind/day
    tagged.sort(key=lambda x: (x[0].time.date(), x[1].role != "target", x[0].time), reverse=False)
    seen, uniq = set(), []
    for d, tag, subject in tagged:
        key = (subject, tag.kind, tag.label, d.date if tag.kind in ("holding", "control_change", "major_holder") else None)
        if tag.signal == "catalyst" and tag.cat in ("sale_process",):
            key = (subject, tag.cat, d.date)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((d, tag, subject))
    log_(f"{len(uniq)} disclosures of interest")

    designations = {}
    for d, tag, subject in uniq:
        if tag.signal == "warning" and re.search(r"監理|整理|特別注意", tag.label):
            designations[subject] = tag.label

    # fetch documents and quotes in parallel first; the loop below then reads them from the cache
    cats = [(d, tag, subject) for d, tag, subject in uniq if tag.signal == "catalyst"]
    ctx.tdnet.prefetch([d.pdf_url for d, tag, _ in cats if tag.kind in READ_DOCS and ctx.store.parsed(d.id) is None])
    ctx.prices.prefetch({s: ctx.universe.get(s).yahoo_symbols for _, _, s in cats})

    hits_by_day: dict[str, list[dict]] = {}
    docs_read = 0
    deals: dict = {}
    last_deal_doc = {}
    for d, tag, subject in uniq:
        if tag.signal == "catalyst" and tag.kind in DEAL_KINDS:
            last_deal_doc[(subject, tag.kind)] = d   # uniq is in time order, so the last one wins
    asof = dt.datetime.now(JST).date().isoformat()
    for d, tag, subject in uniq:
        comp = ctx.universe.get(subject)
        h = dict(id=d.id, date=d.ymd, time=d.time.strftime("%H:%M"), company=comp.name, code=subject,
                 market=comp.market, sector=comp.sector, title=d.title, url=d.pdf_url, signal=tag.signal,
                 kind=tag.kind, role=tag.role, discloser=d.code, mine=subject in ctx.mine)
        if tag.signal == "warning":
            show = tag.kind in NAME_ONLY_WARNINGS or subject in ctx.mine or subject in ctx.value_tags
            if not show:
                continue
            h.update(group="warning", cat=tag.cat, kind_label=tag.label, headline=tag.label, stars=None, passes=True,
                     why=[], watch=ctx.value_tags.get(subject, []), score=0)
            hits_by_day.setdefault(d.ymd, []).append(h)
            continue
        cat = ctx.store.parsed(d.id)
        if cat is None and tag.kind in READ_DOCS and docs_read < ctx.max_docs:
            text = ctx.tdnet.pdf_text(d)
            docs_read += 1
            cat = parse_catalyst(tag.kind, text or "")
            cat["doc_read"] = bool(text)
            ctx.store.put_parsed(d.id, cat)
        cat = dict(cat or {"kind": tag.kind})
        if tag.kind in DEAL_KINDS:
            # one row per deal: the latest document, carrying terms read from earlier ones
            deal = deals.setdefault((subject, tag.kind), {})
            deal.update({k: v for k, v in cat.items() if v not in (None, "", [], False)})
            cat = dict(deal)
            mem = book.terms_for(subject, tag.kind)
            if cat.get("price"):        # this filing states its own price: a later raise is the deal's, not this filing's
                mem.pop("old_price", None), mem.pop("bumps", None)
            for k, v in mem.items():
                if cat.get(k) in (None, "", False):
                    cat[k] = v          # terms from the deal's earlier filings (an amendment repeats none)
            later = last_deal_doc.get((subject, tag.kind))
            if later is not None and later is not d:
                continue
        if tag.kind in ("control_change", "major_holder", "holding") and re.search(r"持株会", cat.get("holder") or ""):
            continue                # employee / supplier share plans crossing 10%: noise
        if tag.kind == "major_holder" and cat.get("direction") == "down" and subject not in ctx.mine and subject not in ctx.value_tags:
            continue                # a holder falling below 10%: only on watched names
        # forecast cuts are warnings, not "guidance raised"
        if tag.kind == "guidance" and cat.get("direction") != "up":
            if cat.get("direction") == "down" and (subject in ctx.mine or subject in ctx.value_tags):
                h.update(group="warning", cat="w_other", signal="warning", kind_label="Forecast cut",
                         headline="Forecast cut" + (f": op. profit {cat['op_chg']:+.0f}%" if cat.get("op_chg") is not None else ""),
                         stars=None, passes=True, why=[], watch=ctx.value_tags.get(subject, []), score=0)
                hits_by_day.setdefault(d.ymd, []).append(h)
            continue
        if tag.kind in ("rumor", "strategic") and cat.get("denied"):
            continue                # denials are dropped, as in the Korean radar
        if tag.kind == "dividend" and cat.get("raise_pct") is None and not (tag.flags.get("special") or tag.flags.get("initiation")
                                                                             or cat.get("special") or cat.get("initiation")):
            if subject not in ctx.mine and subject not in ctx.value_tags:
                continue            # routine dividends: only show raises, specials, first dividends or watched names
        q = ctx.prices.get(subject, comp.yahoo_symbols, refresh=False)
        f = ctx.store.fundamentals(subject)
        extra = dict(ctx.value_rows.get(subject) or {})
        counter = [c for c in (cat.get("codes") or []) if c != subject]
        if counter and tag.kind in ("share_swap", "merger"):
            cq = ctx.prices.get(counter[0], (ctx.universe.get(counter[0]) or comp).yahoo_symbols)
            extra["counter_price"] = cq.price if cq else None
        h["bidder"] = bidder_from_title(d.title) if tag.kind in ("tender_offer", "squeeze_out") else None
        h["target_listed"] = tag.role == "acquirer" and subject != d.code
        h["designation"] = designations.get(subject)
        h["asof"] = asof
        evaluate(h, tag, cat, q, f, ctx.value_tags.get(subject, []), ctx.limits, ctx.activists,
                 extra={"re_index": extra.get("re_index"), "am_mcap": extra.get("am_mcap"), "fit": extra.get("fit"),
                        "counter_price": extra.get("counter_price")})
        h.update(group=tag.group, cat=tag.cat, kind_label=CATALYST_LABEL.get(tag.kind, tag.kind),
                 headline=headline(h, tag), fin=fin_line(h, f), end=cat.get("end"), flags=tag.flags,
                 doc_read=cat.get("doc_read", False))
        hits_by_day.setdefault(d.ymd, []).append(h)
    ctx.store.write_json("deals_latest.json", book.doc(ctx, asof, ctx.activists))
    book.save(asof)
    ctx.store.save()
    ctx.prices.save()
    now = dt.datetime.now(JST).isoformat(timespec="minutes")
    days = sorted({d.ymd for d in items} | set(hits_by_day))
    runs = []
    for day in days:
        hits = hits_by_day.get(day, [])
        runs.append(dict(date=day, window=[day], scanned=sum(1 for d in items if d.ymd == day), hits=[_slim(h) for h in hits],
                         generated_at=now, notes=_notes(hits, ctx)))
    log_(f"{sum(len(r['hits']) for r in runs)} hits over {len(runs)} days; {docs_read} documents read")
    return dict(runs=runs, scanned=len(items), docs_read=docs_read)


KEEP = ("id", "date", "time", "company", "code", "market", "sector", "title", "url", "signal", "group", "cat", "kind",
        "kind_label", "role", "headline", "passes", "fail_reasons", "warnings", "score", "stars", "why", "watch", "mine",
        "mcap", "price", "price_date", "adv", "pe", "pb", "re_index", "asset_margin_vs_mcap", "ncav_vs_mcap",
        "treasury_pct", "premium_pct", "appraisal_premium_pct", "return_yield", "buyback_vs_mcap", "div_yield",
        "gain_vs_mcap", "pro_forma_vs_mcap", "contract_pct", "contract_vs_mcap", "fin", "end", "bidder",
        "debt_equity", "profitable_years", "years_known", "doc_read", "designation", "rating", "fcf3_yield")


def _slim(h: dict) -> dict:
    out = {}
    for k in KEEP:
        v = h.get(k)
        if v is None or v == [] or v == "":
            continue
        if isinstance(v, float):
            v = round(v, 4) if abs(v) < 1000 else round(v)
        out[k] = v
    return out


def _notes(hits: list[dict], ctx: Context) -> list[str]:
    notes = []
    missing = [h["company"] for h in hits if h.get("signal") == "catalyst" and h.get("mcap") is None]
    if missing:
        notes.append("no price for " + ", ".join(missing[:6]))
    unread = sum(1 for h in hits if h.get("signal") == "catalyst" and not h.get("doc_read"))
    if unread:
        notes.append(f"{unread} documents not read (TDnet keeps PDFs ~31 days, or per-run cap)")
    if ctx.prices.asof:
        notes.append(f"prices: Yahoo! Finance Japan, latest close {ctx.prices.asof}")
    return notes
