"""Replay a few companies' filings through the radar and measure what followed.

For each code: its TDnet disclosures over the window (the yanoshin mirror keeps the history), classified and read
like the daily radar (PDFs TDnet has deleted come from Kabutan's permanent mirror), rated with the price of the day,
and followed by the stock's return against the TOPIX ETF (1306) one week, one month and three months later.

Caveat: fundamentals are today's (the store keeps the latest reports only), so value points carry some hindsight;
edge and risk use only the filing and the prices of the time.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from .classify import CATALYST_LABEL, classify
from .docparse import parse_catalyst
from .prices import Quote, YJ_HISTORY, parse_history_page
from .radar import READ_DOCS, Context
from .rate import evaluate, headline
from .tdnet import Disclosure
from .util import today_jst

BENCH = "1306.T"          # NEXT FUNDS TOPIX ETF: the index's own history page does not parse
HORIZONS = (("1w", 5), ("1m", 21), ("3m", 63))


def price_history(http, symbol: str, start: dt.date, end: dt.date, max_pages: int = 20) -> list[tuple[str, float, float]]:
    """Daily (date, close, volume), oldest first, from Yahoo! Finance Japan's paged history."""
    rows = {}
    for page in range(1, max_pages + 1):
        html = http.get_text(YJ_HISTORY.format(sym=symbol), params={
            "from": start.strftime("%Y%m%d"), "to": end.strftime("%Y%m%d"), "timeFrame": "d", "page": page},
            ttl=12 * 3600, allow_404=True)
        got = parse_history_page(html or "", adjusted=True)
        if not got:
            break
        new = [r for r in got if r[0] not in rows]
        for r in got:
            rows[r[0]] = r
        if not new or len(got) < 20:
            break
    return sorted(rows.values())


def kabutan_pdf(d: Disclosure) -> Optional[str]:
    m = re.search(r"/(\d{18})\.pdf", d.pdf_url or "")
    return f"https://tdnet-pdf.kabutan.jp/{d.ymd}/{m.group(1)}.pdf" if m else None


def _after(hist: list, day: str, n: int) -> Optional[tuple]:
    """The close n sessions after the first session after `day` (filings come after the 15:00 close or mid-session)."""
    idx = next((i for i, r in enumerate(hist) if r[0] > day), None)
    if idx is None or idx + n >= len(hist):
        return None
    return hist[idx], hist[idx + n]


def run(ctx: Context, codes: list[str], months: int = 12, log_=print) -> dict:
    end = today_jst()
    start = end - dt.timedelta(days=int(months * 30.5))
    http = ctx.tdnet.http
    bench = price_history(http, BENCH, start - dt.timedelta(days=10), end)
    out = {}
    for code in codes:
        comp = ctx.universe.get(code)
        sym = (comp.yahoo_symbols if comp else [code + ".T"])[0]
        hist = price_history(http, sym, start - dt.timedelta(days=10), end)
        f = ctx.store.fundamentals(code)
        events = []
        for d in ctx.tdnet.list_company(code, limit=300):
            day = d.date.isoformat()
            if not (start <= d.date <= end):
                continue
            tag = classify(d.title, d.code)
            if not tag or (tag.target_code and tag.target_code != code):
                continue
            ev = dict(date=day, title=d.title, signal=tag.signal, kind=tag.kind, cat=tag.cat,
                      label=CATALYST_LABEL.get(tag.kind, tag.label if tag.signal == "warning" else tag.kind))
            if tag.signal == "catalyst":
                cat = ctx.store.parsed(d.id)
                if cat is None and tag.kind in READ_DOCS:
                    text = ctx.tdnet.pdf_text(d)
                    if not text and kabutan_pdf(d):
                        d2 = Disclosure(**{**d.__dict__, "pdf_url": kabutan_pdf(d)})
                        text = ctx.tdnet.pdf_text(d2)
                    cat = parse_catalyst(tag.kind, text or "")
                    cat["doc_read"] = bool(text)
                    ctx.store.put_parsed(d.id, cat)
                cat = dict(cat or {"kind": tag.kind})
                if tag.kind == "guidance" and cat.get("direction") == "down":
                    ev.update(signal="warning", label="Forecast cut")
                elif tag.kind == "guidance" and cat.get("direction") != "up":
                    continue
                else:
                    prior = [r for r in hist if r[0] <= day]
                    q = None
                    if prior:
                        last20 = prior[-20:]
                        q = Quote(code=code, symbol=sym, price=prior[-1][1], price_date=prior[-1][0], history=last20,
                                  adv=sum(c * v for _, c, v in last20) / len(last20),
                                  shares_issued=None)
                    h = dict(id=d.id, date=d.ymd, code=code, company=comp.name if comp else code, title=d.title,
                             signal=tag.signal, kind=tag.kind, role=tag.role, mine=True)
                    evaluate(h, tag, cat, q, f, ctx.value_tags.get(code, []), ctx.limits, ctx.activists,
                             extra={"re_index": (ctx.value_rows.get(code) or {}).get("re_index")})
                    ev.update(stars=h.get("stars"), headline=headline(h, tag), rating=h.get("rating"),
                              doc_read=cat.get("doc_read", False), passes=h.get("passes"))
            # what followed: the stock and the TOPIX ETF from the next session's close
            for name, n in HORIZONS:
                s, t = _after(hist, day, n), _after(bench, day, n)
                if s and t:
                    rs, rt = s[1][1] / s[0][1] - 1, t[1][1] / t[0][1] - 1
                    ev["r_" + name], ev["x_" + name] = round(rs, 4), round(rs - rt, 4)
            s0 = _after(hist, day, 0)
            if s0 and hist:
                ev["r_now"] = round(hist[-1][1] / s0[0][1] - 1, 4)
            events.append(ev)
        events.sort(key=lambda e: e["date"])
        first, last = (hist[0], hist[-1]) if hist else (None, None)
        bfirst = next((r for r in bench if first and r[0] >= first[0]), None)
        out[code] = dict(code=code, name=comp.name if comp else code, events=events,
                         period=dict(start=first[0] if first else None, end=last[0] if last else None,
                                     ret=round(last[1] / first[1] - 1, 4) if first else None,
                                     bench=round(bench[-1][1] / bfirst[1] - 1, 4) if bfirst and bench else None))
        log_(f"{code}: {len(events)} events, {sum(1 for e in events if e.get('stars'))} rated")
    ctx.store.save()
    return out
