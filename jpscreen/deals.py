"""Open deals, carried from day to day.

Tender offers, self-tenders and squeeze-outs announced on TDnet, with every later filing on the same deal folded in
(changed terms, extensions, progress notes, results), the live spread to the bid, and the signs that a bid could be
raised. The radar reads a follow-up filing's terms from here when the document itself repeats none, so an amended
opinion statement or a progress note still shows the price and the spread.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterable, Optional

from .classify import SELF, codes_in
from .docparse import parse_tender, tight, to_num
from .frontrun import match_activist
from .prices import YJ_HISTORY, parse_history_page
from .tdnet import Disclosure
from .util import nfkc, norm_code

TOB = re.compile(r"公開買付")
SQUEEZE = re.compile(r"株式等売渡請求|売渡請求|株式併合.*(上場廃止|臨時株主総会|整理銘柄)")
RESULT = re.compile(r"結果")
PLANNED = re.compile(r"開始予定|予定に関する|進捗|経過|前提条件")
CHANGE = re.compile(r"買付条件等の変更|期間(の)?延長|買付価格の(変更|引き上げ|引上げ)|(一部)?変更")
WITHDRAWN = re.compile(r"撤回|中止")
OPINION = re.compile(r"意見表明|賛同|応募(の)?推奨|中立|反対")
OWN = re.compile(r"当社(株式|株券|普通株式|の株式|の株券|株)|当社に対する|当社を")
NOT_A_DEAL = re.compile(r"ETF|投資法人|REIT|インフラファンド|応募契約|応募(の)?(予定|方針)|売却(予定|方針)|特別利益|特別損失|"
                        r"持分法適用会社|子会社株式|関連会社株式|の株式に対する公開買付けへの応募")
KEEP_CLOSED_DAYS = 45       # closed deals stay on the page this long
MEMORY_DAYS = 180           # and in the memory this long

TERM_FIELDS = ("price", "old_price", "bumps", "start", "end", "partial", "upper_limit", "lower_limit", "pct_of_shares",
               "delisting", "mbo", "board_view", "bidder", "premium_stated")


def bidder_from_title(title: str) -> Optional[str]:
    t = nfkc(title)
    t = re.sub(r"^[（(](変更|一部変更|訂正|開示事項の経過)[)）]", "", t).lstrip("「")
    m = re.search(r"([^「」『』()（）、]{2,60}?)による", t)
    if not m:
        return None
    b = re.sub(r"^(当社(の)?(親会社|筆頭株主|主要株主|その他の関係会社|代表取締役[^で]*)である)", "", m.group(1)).strip()
    return b[:60] or None


def kabutan_pdf(d) -> Optional[str]:
    """Kabutan keeps every TDnet PDF; TDnet itself deletes them after about a month."""
    m = re.search(r"/(\d{18})\.pdf", d.pdf_url or "")
    return f"https://tdnet-pdf.kabutan.jp/{d.ymd}/{m.group(1)}.pdf" if m else None


def doc_text(ctx, d) -> Optional[str]:
    text = ctx.tdnet.pdf_text(d)
    if not text and kabutan_pdf(d):
        text = ctx.tdnet.pdf_text(Disclosure(**{**d.__dict__, "pdf_url": kabutan_pdf(d)}))
    return text


def close_before(http, symbol: str, day: str) -> Optional[tuple[str, float]]:
    """The last close strictly before `day` (the undisturbed price of a deal announced that day)."""
    d0 = dt.date.fromisoformat(day)
    try:
        html = http.get_text(YJ_HISTORY.format(sym=symbol), params={"from": (d0 - dt.timedelta(days=21)).strftime("%Y%m%d"),
                             "to": (d0 - dt.timedelta(days=1)).strftime("%Y%m%d"), "timeFrame": "d"}, ttl=None, allow_404=True)
    except Exception:
        return None
    rows = parse_history_page(html or "")
    return (rows[0][0], rows[0][1]) if rows else None


def parse_result(text: str) -> dict:
    """The outcome in a 結果 filing: shares tendered and whether the bid went through."""
    t = tight(text)
    out: dict = {}
    m = re.search(r"応募株券等の(?:総)?数[^0-9]{0,40}([\d,]+)株", t)
    out["tendered"] = to_num(m.group(1)) if m else None
    if re.search(r"下限に満たな|不成立|成立しませんでした|買付け等を行いません|買付け等を行わない", t[:20000]):
        out["success"] = False
    elif re.search(r"成立|買付け等を行います|全部の買付け|応募株券等の全部", t[:20000]):
        out["success"] = True
    m = re.search(r"所有割合[^0-9]{0,30}([\d.]+)%", t[-15000:])
    out["owned_after_pct"] = to_num(m.group(1)) if m else None
    return out


def _hint(t: str) -> str:
    """What kind of filing this is for the deal's life: announced, open, changed, closed, withdrawn."""
    if RESULT.search(t):
        return "closed"
    if WITHDRAWN.search(t):
        return "withdrawn"
    if PLANNED.search(t) and not re.search(r"開始に関する|開始のお知らせ", t) and not OPINION.search(t):
        return "announced"
    if CHANGE.search(t) and not re.search(r"開始に関する|開始のお知らせ", t):
        return "changed"
    return "open"


class DealBook:
    def __init__(self, store):
        self.store = store
        data = store.read_json("deals.json", {}) or {}
        self.deals: dict[str, dict] = data.get("deals") or {}
        self.updated = data.get("updated")

    def save(self, today: str) -> None:
        cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=MEMORY_DAYS)).isoformat()
        self.deals = {k: d for k, d in self.deals.items() if (d.get("last_date") or d.get("first_date") or today) >= cutoff}
        self.store.write_json("deals.json", dict(deals=self.deals, updated=today))

    def load_memory(self, deals: dict) -> None:
        """Deals restored from the dashboard's database (a fresh container starts empty)."""
        for k, d in (deals or {}).items():
            cur = self.deals.get(k)
            if cur is None or (d.get("last_date") or "") > (cur.get("last_date") or ""):
                self.deals[k] = d

    def latest(self, code: str, kind: str, include_closed: bool = True) -> Optional[dict]:
        cands = [d for d in self.deals.values() if d["code"] == code and d["kind"] == kind
                 and (include_closed or d.get("status") not in ("closed", "withdrawn", "ended"))]
        return max(cands, key=lambda d: d.get("last_date") or "") if cands else None

    def terms_for(self, code: str, kind: str) -> dict:
        d = self.latest(code, kind)
        return {k: d[k] for k in TERM_FIELDS if d and d.get(k) not in (None, "", [], False)} if d else {}

    # ---------------------------------------------------------------- updating from filings
    def update(self, ctx, items: Iterable, names, max_reads: int = 40, log_=print) -> int:
        reads = n = 0
        for d in sorted(items, key=lambda x: x.time):
            t = nfkc(d.title)
            if not (TOB.search(t) or SQUEEZE.search(t)) or NOT_A_DEAL.search(t):
                continue
            if "訂正" in t and "買付条件" not in t:
                continue
            kind = "self_tender" if SELF.search(t) else "squeeze_out" if (SQUEEZE.search(t) and not TOB.search(t)) else "tender_offer"
            code = norm_code(d.code)
            coded = codes_in(t)
            if kind != "tender_offer" or OWN.search(t) or code in coded:
                target = code       # the target posts the bidder's announcement under its own code
            else:
                other = [c for c in coded if c != code]
                target = other[0] if other else names.find(t, exclude=code)
                if not target:
                    continue        # a bid for an unlisted company
            target = norm_code(target)
            if target not in ctx.universe:
                continue
            hint = _hint(t)
            deal = self.latest(target, kind, include_closed=False)
            if deal is None and hint in ("closed", "withdrawn"):
                deal = self.latest(target, kind)        # a result for a deal already closed: fold into it
                if deal and deal.get("last_date") and (d.date - dt.date.fromisoformat(deal["last_date"])).days > 90:
                    deal = None
            if deal is None:
                key = f"{target}:{kind}:{d.ymd}"
                comp = ctx.universe.get(target)
                deal = self.deals.setdefault(key, dict(key=key, code=target, kind=kind, name=comp.name if comp else target,
                                                         first_date=d.date.isoformat(), bumps=0, extensions=0, docs=[],
                                                         status=None))
            need = deal.get("price") is None or deal.get("end") is None or hint in ("changed", "closed")
            cat = ctx.store.parsed(d.id)
            if (cat is None or (need and not cat.get("doc_read"))) and reads < max_reads:
                text = doc_text(ctx, d)
                reads += 1
                cat = parse_tender(text or "") if text else {}
                if hint == "closed" and text:
                    cat.update(parse_result(text))
                cat["doc_read"] = bool(text)
                cat["kind"] = kind
                ctx.store.put_parsed(d.id, cat)
            self._merge(deal, cat or {}, d, t, hint, target)
            n += 1
        if reads:
            log_(f"deals: {reads} documents read")
        return n

    def _merge(self, deal: dict, cat: dict, d, title: str, hint: str, target: str) -> None:
        day = d.date.isoformat()
        cat = dict(cat)
        # a period must sit around the filing date; anything else is some other date range in the document
        for k, lo, hi in (("start", -120, 120), ("end", -60, 150)):
            if cat.get(k):
                try:
                    off = (dt.date.fromisoformat(cat[k]) - d.date).days
                except ValueError:
                    off = None
                if off is None or not (lo <= off <= hi):
                    cat[k] = None
        if cat.get("start") and cat.get("end") and cat["end"] < cat["start"]:
            cat["start"] = cat["end"] = None
        price = cat.get("price")
        if price:
            if deal.get("price") and price > deal["price"]:
                deal["old_price"], deal["bumps"] = deal["price"], (deal.get("bumps") or 0) + 1
            elif cat.get("old_price") and cat.get("bumps") and not deal.get("price"):
                deal["old_price"], deal["bumps"] = cat["old_price"], cat["bumps"]
            deal["price"] = price
        end = cat.get("end")
        if end:
            if deal.get("end") and end > deal["end"] and hint == "changed":
                deal["extensions"] = (deal.get("extensions") or 0) + 1
            if not deal.get("end") or end > deal["end"] or hint in ("changed", "open"):
                deal["end"] = end
        if deal["kind"] == "squeeze_out":       # the company itself proposes it: no bid limits, no board opinion to read
            for k in ("partial", "upper_limit", "lower_limit", "pct_of_shares", "board_view"):
                cat[k] = None
        for k in ("start", "partial", "upper_limit", "lower_limit", "pct_of_shares", "delisting", "mbo", "premium_stated"):
            if cat.get(k) not in (None, "", False) and deal.get(k) in (None, "", False):
                deal[k] = cat[k]
        if cat.get("board_view") and (norm_code(d.code) == target or not deal.get("board_view")):
            deal["board_view"] = cat["board_view"]
        if not deal.get("bidder") and deal["kind"] != "self_tender":
            deal["bidder"] = bidder_from_title(title)       # the title names the bidder cleanly
        b = cat.get("bidder")
        if b and not deal.get("bidder") and len(b) <= 40 and re.search(r"株式会社|合同会社|LLC|L\.P\.|Limited|Ltd|Inc", b) \
                and not re.search(r"代表|会社名|氏名|所在地", b):
            deal["bidder"] = b
        if deal["kind"] == "self_tender":
            deal["bidder"] = deal["name"]
        if re.search(r"親会社|支配株主", title) or re.search(r"親会社", cat.get("purpose") or ""):
            deal["parent"] = True
        if cat.get("mbo") or re.search(r"MBO|マネジメント・?バイアウト", title):
            deal["mbo"] = True
        if hint == "closed":
            deal["status"] = "closed"
            for k in ("tendered", "success", "owned_after_pct"):
                if cat.get(k) is not None:
                    deal[k] = cat[k]
        elif hint == "withdrawn":
            deal["status"] = "withdrawn"
        elif hint == "announced":
            if deal.get("status") in (None, "announced"):
                deal["status"] = "announced"
        elif deal.get("status") in (None, "announced"):
            deal["status"] = "open"
        if hint == "changed" and deal.get("status") == "open":
            deal["changed"] = True
        deal["last_date"], deal["last_title"], deal["last_url"] = day, title[:120], d.pdf_url
        if d.id not in [x.get("id") for x in deal["docs"]]:
            deal["docs"] = (deal["docs"] + [dict(id=d.id, date=day, title=title[:90], url=d.pdf_url)])[-12:]

    # ---------------------------------------------------------------- live view
    def mark(self, ctx, today: str, activists=None) -> list[dict]:
        """Spread, days left, offer multiples and bump signals for every deal; closes bids whose period has passed."""
        from .value import market_cap, net_current_assets, shares_now
        t0 = dt.date.fromisoformat(today)
        rows = []
        for deal in self.deals.values():
            code = deal["code"]
            q = ctx.prices.quotes.get(code)
            f = ctx.store.fundamentals(code)
            if deal.get("status") not in ("closed", "withdrawn") and deal.get("end"):
                if (t0 - dt.date.fromisoformat(deal["end"])).days > 5:
                    deal["status"] = "ended"       # the period has passed and no result filing was seen
            if deal.get("ref_price") is None and deal.get("status") in ("open", "announced", None):
                before = [r for r in ((q.history if q else None) or []) if r[0] < deal["first_date"]]
                if before:
                    deal["ref_price"], deal["ref_date"] = before[0][1], before[0][0]
                else:
                    comp = ctx.universe.get(code)
                    sym = (comp.yahoo_symbols if comp else [code + ".T"])[0]
                    got = close_before(ctx.tdnet.http, sym, deal["first_date"])
                    if got:
                        deal["ref_date"], deal["ref_price"] = got
            live = dict(deal)
            live.pop("docs", None)
            price = deal.get("price")
            if q and q.price:
                live["last"], live["last_date"] = q.price, q.price_date
                if price:
                    live["spread"] = round(price / q.price - 1, 4)
            if price and deal.get("ref_price"):
                live["premium"] = round(price / deal["ref_price"] - 1, 4)
            if deal.get("end"):
                live["days_left"] = (dt.date.fromisoformat(deal["end"]) - t0).days
            if price and f:
                issued, treasury = shares_now(f, q)
                out = (issued or 0) - (treasury or 0)
                if out > 0:
                    offer_mcap = price * out
                    live["offer_mcap"] = round(offer_mcap)
                    if f.equity:
                        live["offer_pb"] = round(offer_mcap / f.equity, 2)
                    nca = net_current_assets(f)
                    if nca is not None:
                        live["offer_nca"] = round(nca / offer_mcap, 2)        # net current assets per yen of the bid
                    if f.liabilities is not None:
                        am = (f.cash or 0) + (f.securities or 0) + (f.investment_securities or 0) - f.liabilities
                        live["offer_net_cash"] = round(am / offer_mcap, 2)
                if activists and f.major_holders:
                    acts = [match_activist(h.get("name") or "", activists) for h in f.major_holders]
                    acts = [a[0] for a in acts if a]
                    if acts:
                        live["activists"] = acts[:3]
                th = f.major_holders[0] if f.major_holders else None
                if th and deal.get("bidder") and not deal.get("parent"):
                    b = re.sub(r"株式会社|合同会社|ホールディングス", "", deal["bidder"])
                    if len(b) >= 3 and b in (th.get("name") or ""):
                        live["parent"] = True
            flags = []
            if live.get("mbo"):
                flags.append("MBO")
            if live.get("parent"):
                flags.append("parent buy-out")
            if live.get("partial"):
                flags.append("partial")
            if deal.get("bumps"):
                flags.append(f"raised ×{deal['bumps']}")
            if deal.get("extensions"):
                flags.append(f"extended ×{deal['extensions']}")
            if live.get("spread") is not None and live["spread"] < -0.005 and deal.get("status") in ("open", "announced"):
                flags.append("trading above the bid")
            if live.get("premium") is not None and live["premium"] < 0.30:
                flags.append("low premium")
            if (live.get("offer_pb") or 9) < 0.8 or (live.get("offer_net_cash") or 0) >= 0.7 or (live.get("offer_nca") or 0) >= 0.9:
                flags.append("cheap bid")
            if live.get("activists"):
                flags.append("activist on register")
            if deal.get("board_view") in ("neutral", "oppose", "reserve"):
                flags.append(f"board {deal['board_view']}")
            bump = sum(1 for x in flags if x in ("trading above the bid", "low premium", "cheap bid", "activist on register"))
            bump += 0.5 * sum(1 for x in flags if x in ("MBO", "parent buy-out") or x.startswith("raised") or x.startswith("board"))
            live["flags"], live["bump_score"] = flags, bump
            rows.append(live)
        cutoff = (t0 - dt.timedelta(days=KEEP_CLOSED_DAYS)).isoformat()
        rows = [r for r in rows if r.get("status") in ("open", "announced") or (r.get("last_date") or "") >= cutoff]
        order = {"open": 0, "announced": 1, "ended": 2, "closed": 3, "withdrawn": 4}
        rows.sort(key=lambda r: (order.get(r.get("status"), 5), -(r.get("bump_score") or 0), -(r.get("spread") or -9)))
        return rows

    def doc(self, ctx, today: str, activists=None) -> dict:
        rows = self.mark(ctx, today, activists)
        return dict(date=today, rows=rows, memory=self.deals,
                    open=sum(1 for r in rows if r.get("status") in ("open", "announced")))
