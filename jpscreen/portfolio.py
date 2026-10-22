"""The portfolio tab: every holding rated on the radar's system, with all its filings and news.

Holdings come from two places, merged:
  - the Tokyo Filings Tracker (its `stocks` collection: the companies you follow there, with English titles and
    summaries of their filings), and
  - positions added on the radar page itself (its `positions` collection: code, held/watching, shares, average cost).
Each holding gets: a price/valuation snapshot (EDINET fundamentals when available), the RE net-net index, its radar
events (180 days), standing catalysts (two years of disclosures, holders, yield, listing pressure, pre-deal score),
a rating (edge + value − risk), and a feed of filings (TDnet, EDINET) and news (Kabutan, Google News).
Stored as one `portfolio/summary` document plus one `portfolio/<code>` document per company.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Optional

from .classify import CAT_LABEL
from .frontrun import history_signals, score
from .news import filing_cat, google_news, is_noise, kabutan_news, short_name, title_en
from .radar import Context
from .rating import days_between, holding_rating, standing_catalysts
from .util import JST, nfkc, norm_code, today_jst
from .value import build_row

log = logging.getLogger(__name__)

ITEM_DAYS = 730          # the feed keeps two years of history
KEEP_ITEMS = 80          # per company
FEED_ITEMS = 150         # newest items across the portfolio in the summary
EVENT_DAYS = 180


def _load_dir(d: Optional[str | Path]) -> list[dict]:
    out = []
    if not d or not Path(d).exists():
        return out
    for p in sorted(Path(d).glob("*.json")):
        try:
            x = json.loads(p.read_text())
        except ValueError:
            continue
        if not isinstance(x, dict):
            continue                 # e.g. the translation queue kept next to the portfolio documents
        x = x["data"] if isinstance(x.get("data"), dict) and "version" in x else x
        x.setdefault("_id", p.stem)
        out.append(x)
    return out


def load_holdings(tracker_dir: Optional[str], positions_dir: Optional[str]) -> list[dict]:
    """Merge the tracker's companies with the radar's own positions; positions carry shares and cost."""
    hold: dict[str, dict] = {}
    for t in _load_dir(tracker_dir):
        code = norm_code(t.get("code") or t["_id"])
        if not re.match(r"^\d[0-9A-Z]{3}$", code):
            continue
        hold[code] = dict(code=code, group="held", en=t.get("nameEn") or "", name=t.get("nameJa") or "",
                          tracker=dict(headline=t.get("headline"), next_event=t.get("nextEvent"),
                                       items=t.get("items") or [], snap=t.get("snap") or {}, sector=t.get("sector"),
                                       basis=t.get("basis"), fy=t.get("fy")), source="tracker")
    for p in _load_dir(positions_dir):
        code = norm_code(p.get("code") or p["_id"])
        if not re.match(r"^\d[0-9A-Z]{3}$", code) or p.get("removed"):
            continue
        h = hold.setdefault(code, dict(code=code, group="held", en="", name="", source="radar"))
        h["group"] = "held" if (p.get("group") or "held") == "held" else "watch"
        for k in ("shares", "cost", "note", "added"):
            if p.get(k) not in (None, ""):
                h[k] = p[k]
        h["source"] = "both" if h.get("source") == "tracker" else "radar"
    return list(hold.values())


def _tracker_match(items: list[dict], tr_items: list[dict]) -> None:
    """Reuse the tracker's English titles and summaries for the same filing (same day, same Japanese title)."""
    by = {}
    for t in tr_items:
        ja = nfkc(t.get("titleJa") or "")
        if ja:
            by[(t.get("date"), re.sub(r"\s", "", ja)[:30])] = t
    for it in items:
        t = by.get((it.get("date"), re.sub(r"\s", "", it.get("ja") or "")[:30]))
        if t:
            it["en"] = it.get("en") or t.get("titleEn") or ""
            if t.get("summary") and not it.get("sum"):
                it["sum"] = t["summary"][:300]


def build(ctx: Context, holdings: list[dict], runs: list[dict], frontrun_rows: list[dict],
          prev_dir: Optional[str] = None, edinet=None, log_=print) -> dict:
    now = dt.datetime.now(JST).isoformat(timespec="minutes")
    today = today_jst().isoformat()
    prev = {d.get("code") or d["_id"]: d for d in _load_dir(prev_dir)}
    prev_summary = prev.pop("summary", None) or {}
    prev_hold = {h["code"]: h for h in prev_summary.get("holdings") or []}
    fr_by = {r["code"]: r for r in frontrun_rows}
    # a full year of EDINET filings the first time a holding is seen, then only the last few days
    days = 10 if all(h["code"] in prev for h in holdings) else 365
    edinet_by = _edinet_filings(ctx, edinet, {h["code"] for h in holdings}, days) if edinet is not None else {}
    ctx.prices.prefetch({h["code"]: (ctx.universe.get(h["code"]).yahoo_symbols if ctx.universe.get(h["code"]) else
                                     [h["code"] + ".T"]) for h in holdings})
    docs, to_translate, summary_rows, feed = {}, [], [], []
    for h in holdings:
        code = h["code"]
        comp = ctx.universe.get(code)
        name = (comp.name if comp else "") or h.get("name") or code
        h["name"] = h.get("name") or name
        q = ctx.prices.get(code, comp.yahoo_symbols if comp else None, refresh=False)
        f = ctx.store.fundamentals(code)
        row = build_row(comp, f, q) if comp else None
        if row and (row.re_index is None) and code in ctx.value_rows:
            ref = ctx.value_rows[code]
            row.re_index, row.nca, row.real_estate = ref.get("re_index"), ref.get("nca"), ref.get("real_estate")
        # ---- snapshot
        snap = _snapshot(q, f, row)
        # ---- feed items: carry over what was seen before
        old = {it["id"]: it for it in (prev.get(code) or {}).get("items") or []}
        items = {k: it for k, it in old.items() if not is_noise(it)}
        fresh = []
        for it in kabutan_news(ctx.tdnet.http, code) + _tdnet_items(ctx, code) + edinet_by.get(code, []) + \
                google_news(ctx.tdnet.http, short_name(name)):
            if it["id"] in items:
                continue
            if it["kind"] == "filing":
                it["en"] = it.get("en") or title_en(it["ja"])
                it["cat"] = filing_cat(it["ja"]) if it.get("cat") in (None, "filing") else it["cat"]
            it["first"] = now
            items[it["id"]] = it
            fresh.append(it["id"])
        _dedupe_same_filing(items)
        fresh_set = set(fresh)
        _tracker_match(list(items.values()), (h.get("tracker") or {}).get("items") or [])
        cutoff = (today_jst() - dt.timedelta(days=ITEM_DAYS)).isoformat()
        keep = sorted((it for it in items.values() if (it.get("date") or "") >= cutoff),
                      key=lambda x: (x.get("date") or "", x["id"]), reverse=True)[:KEEP_ITEMS]
        if not old:
            for it in keep:
                it["seeded"] = True      # the first refresh of a holding is history, not news
        for it in keep:
            if not it.get("en") and it["kind"] == "filing":
                it["en"] = title_en(it["ja"])          # routine titles, also for items carried over
            if not it.get("en"):
                to_translate.append(dict(code=code, id=it["id"], ja=it["ja"], kind=it["kind"]))
        # ---- radar events on this company
        events = []
        for run in runs:
            for hit in run.get("hits") or []:
                if hit.get("code") != code:
                    continue
                d = str(hit.get("date") or "")
                ev = dict(id=hit["id"], date=f"{d[:4]}-{d[4:6]}-{d[6:8]}", cat=hit.get("cat"),
                          label=CAT_LABEL.get(hit.get("cat"), hit.get("cat")), headline=(hit.get("headline") or "")[:200],
                          stars=hit.get("stars"), url=hit.get("url"))
                if hit.get("group") == "warning" or hit.get("signal") == "warning":
                    ev["warn"] = {"w_distress": "distress", "w_dilution": "dilution"}.get(hit.get("cat"), "other")
                else:
                    ev["edge"] = (hit.get("rating") or {}).get("edge")
                events.append(ev)
        events = sorted({e["id"]: e for e in events if days_between(e["date"], today) <= EVENT_DAYS}.values(),
                        key=lambda e: e["date"], reverse=True)
        # ---- standing catalysts and pre-deal score
        hist = []
        try:
            hist = history_signals(ctx.tdnet.list_company(code, limit=300), today_jst() - dt.timedelta(days=730))
        except Exception as e:  # history is optional
            log.info("history %s: %s", code, e)
        fr = fr_by.get(code)
        if fr is None and row is not None:
            fr = score(row, f, hist, ctx.activists, today_jst()).to_dict()
        holders = (f.major_holders if f else []) or []
        standing = standing_catalysts(hist, snap, fr, holders, ctx.activists, today)
        designation = next((e["headline"] for e in events if e.get("warn") == "distress"
                            and re.search(r"監理|特別注意|整理", e.get("headline") or "")), None)
        stars, rating = holding_rating(snap, events, standing, today, designation)
        # ---- position
        pos = _position(h, snap)
        hrow = dict(code=code, name=name, en=h.get("en") or (short_en(comp.name_en) if comp else ""), market=comp.market if comp else "",
                    sector=comp.sector if comp else (h.get("tracker") or {}).get("sector"), group=h["group"],
                    source=h.get("source"), stars=stars, rating=rating, snap=snap, **pos,
                    new=sum(1 for x in keep if x["id"] in fresh_set and not x.get("seeded")),
                    last_item=keep[0]["date"] if keep else None, note=h.get("note"), added=h.get("added"),
                    headline=(h.get("tracker") or {}).get("headline"), next_event=(h.get("tracker") or {}).get("next_event"),
                    prev_stars=(prev_hold.get(code) or {}).get("stars"),
                    lists=(row.lists if row else []) or ctx.value_tags.get(code, []))
        summary_rows.append(hrow)
        docs[code] = dict(code=code, name=name, items=keep, events=events, catalysts=[dict(p=p, why=w) for p, w in standing],
                          frontrun=dict(prob=(fr or {}).get("prob"), stars=(fr or {}).get("stars"),
                                        reasons=(fr or {}).get("reasons") or []) if fr else None,
                          holders=[dict(name=x.get("name"), pct=round(x.get("pct") or 0, 2)) for x in holders[:8]],
                          tracker_snap=_slim_tracker((h.get("tracker") or {}).get("snap")))
        for it in keep[:25]:
            feed.append(dict(it, code=code))
    total_value = sum(r.get("value") or 0 for r in summary_rows if r["group"] == "held")
    for r in summary_rows:
        if r.get("value") and total_value:
            r["weight"] = round(r["value"] / total_value, 4)
    feed.sort(key=lambda x: (x.get("date") or "", x["id"]), reverse=True)
    totals = dict(value=total_value or None,
                  cost=sum((r.get("cost_value") or 0) for r in summary_rows if r["group"] == "held") or None)
    if totals["value"] and totals["cost"]:
        totals["pnl"] = totals["value"] - totals["cost"]
        totals["pnl_pct"] = round(totals["pnl"] / totals["cost"], 4)
    summary = dict(date=today, generated_at=now, price_date=ctx.prices.asof, holdings=summary_rows,
                   feed=feed[:FEED_ITEMS], totals=totals)
    log_(f"portfolio: {len(summary_rows)} holdings, {sum(r['new'] for r in summary_rows)} new items, "
         f"{len(to_translate)} titles to translate")
    return dict(summary=summary, docs=docs, to_translate=to_translate)


def _snapshot(q, f, row) -> dict:
    s = {}
    if q:
        s.update(price=q.price, price_date=q.price_date, adv=q.adv, per=q.per, pbr=q.pbr, high=q.year_high, low=q.year_low)
        if q.history and len(q.history) >= 2 and q.history[1][1]:
            s["chg_1d"] = round(q.history[0][1] / q.history[1][1] - 1, 4) if q.price_date == q.history[0][0] else None
        if q.dps and q.price:
            s["div_yield"] = round(q.dps / q.price, 4)
            s["dps"] = q.dps
        s["next_results"] = q.next_results
    if row:
        s.update(mcap=row.mcap, re_index=row.re_index, nca=row.nca, real_estate=row.real_estate, net_mcap=row.am_mcap,
                 ncav_mcap=row.ncav_mcap, pe=row.pe or (q.per if q else None), pb=row.pb or (q.pbr if q else None),
                 treasury=row.treasury_pct, top_holder=row.top_holder, top_holder_pct=row.top_holder_pct)
    if f:
        hist = [v for v in (f.net_income_history or []) if v is not None]
        s.update(op=f.op_income, ni=f.net_income, rev=f.revenue, py=sum(1 for v in hist if v > 0), yk=len(hist),
                 de=(f.liabilities / f.equity) if f.liabilities is not None and f.equity and f.equity > 0 else None,
                 rental_fv=f.rental_fair_value, land=f.land, source=f.source)
        fc = [x for x in (f.fcf_history or [])[:3] if x is not None]
        if fc and row and row.mcap:
            s["fcf3_yield"] = sum(fc) / len(fc) / row.mcap
        if f.dividends_paid and row and row.mcap and "div_yield" not in s:
            s["div_yield"] = round(f.dividends_paid / row.mcap, 4)
    elif q and q.eps is not None and q.shares_issued:
        s["op"] = s["ni"] = q.eps * q.shares_issued
    return {k: (round(v, 4) if isinstance(v, float) and abs(v) < 1000 else round(v) if isinstance(v, float) else v)
            for k, v in s.items() if v is not None}


def _position(h: dict, snap: dict) -> dict:
    out = {}
    try:
        sh = float(h["shares"]) if h.get("shares") not in (None, "") else None
        cost = float(h["cost"]) if h.get("cost") not in (None, "") else None
    except (TypeError, ValueError):
        sh = cost = None
    px = snap.get("price")
    if sh:
        out["shares"] = sh
        if px:
            out["value"] = round(sh * px)
            if snap.get("chg_1d") is not None:
                out["day_pnl"] = round(sh * px * snap["chg_1d"] / (1 + snap["chg_1d"]))
    if cost:
        out["cost"] = cost
        if sh:
            out["cost_value"] = round(sh * cost)
        if px:
            out["pnl_pct"] = round(px / cost - 1, 4)
            if sh:
                out["pnl"] = round((px - cost) * sh)
    return out


def _slim_tracker(sn: Optional[dict]) -> Optional[dict]:
    if not sn:
        return None
    keep = {}
    for k in ("latest", "forecast"):
        if sn.get(k):
            keep[k] = sn[k]
    return keep or None


def _tdnet_items(ctx: Context, code: str) -> list[dict]:
    """TDnet titles from the company's own history (fills gaps when Kabutan's list is short)."""
    try:
        hist = ctx.tdnet.list_company(code, limit=60)
    except Exception:
        return []
    out = []
    for d in hist[:60]:
        m = re.search(r"/(\d{18})\.pdf", d.pdf_url or "")
        url = d.pdf_url
        if m:   # Kabutan keeps TDnet PDFs permanently; TDnet itself deletes them after ~31 days
            url = f"https://tdnet-pdf.kabutan.jp/{d.date.strftime('%Y%m%d')}/{m.group(1)}.pdf"
        out.append(dict(id=("t" + m.group(1)) if m else "y" + d.id, date=d.date.isoformat(), kind="filing", ja=d.title[:200],
                        cat="filing", url=url, src="TDnet"))
    return out


def _dedupe_same_filing(items: dict) -> None:
    """Kabutan and yanoshin list the same TDnet PDF under different ids in rare cases: keep one per (date, title)."""
    seen = {}
    for k, it in sorted(items.items(), key=lambda kv: kv[0]):
        key = (it.get("date"), it.get("kind"), re.sub(r"\s", "", it.get("ja") or "")[:40])
        if key in seen:
            keep = items[seen[key]]
            for f in ("en", "sum"):
                if it.get(f) and not keep.get(f):
                    keep[f] = it[f]
            del items[k]
        else:
            seen[key] = k


def _edinet_filings(ctx: Context, edinet, codes: set, days: int = 365) -> dict[str, list[dict]]:
    """EDINET filings by or about each holding in the last 365 days (annual/half-year reports, 5% holder reports...)."""
    if not getattr(edinet, "enabled", False):
        return {}
    by_ed = {ctx.universe.get(c).edinet_code: c for c in codes if ctx.universe.get(c) and ctx.universe.get(c).edinet_code}
    out: dict[str, list[dict]] = {}
    end = today_jst()
    for f in edinet.documents_range(end - dt.timedelta(days=days), end):
        code = by_ed.get(f.edinet_code) or by_ed.get(f.issuer_edinet_code or "") or by_ed.get(f.subject_edinet_code or "")
        if not code:
            continue
        about_other = f.edinet_code not in by_ed and code is not None
        ja = f.description + (f" ({f.filer})" if about_other else "")
        out.setdefault(code, []).append(dict(id="e" + f.doc_id, date=f.submitted.date().isoformat(), kind="filing",
                                             ja=ja[:200], cat=filing_cat(f.description), url=f.url,
                                             src="EDINET" + (f" · {f.filer[:24]}" if about_other else "")))
    return out


def save(result: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        if old.name not in ("translations.json",):
            old.unlink()
    (out / "summary.json").write_text(json.dumps(result["summary"], ensure_ascii=False))
    for code, doc in result["docs"].items():
        (out / f"{code}.json").write_text(json.dumps(doc, ensure_ascii=False))
    (out / "to_translate.json").write_text(json.dumps(result["to_translate"], ensure_ascii=False, indent=0))


def load_saved(out: Path) -> dict:
    summary = json.loads((out / "summary.json").read_text())
    docs = {p.stem: json.loads(p.read_text()) for p in out.glob("*.json")
            if p.stem not in ("summary", "to_translate", "translations")}
    tt = json.loads((out / "to_translate.json").read_text()) if (out / "to_translate.json").exists() else []
    return dict(summary=summary, docs=docs, to_translate=tt)


def apply_translations(result: dict, translations: dict[str, str]) -> int:
    """Put English titles (from the refresh's translation step) into the docs, the feed and the summary."""
    n = 0
    for doc in result["docs"].values():
        for it in doc["items"]:
            en = translations.get(it["id"])
            if en and not it.get("en"):
                it["en"] = en[:220]
                n += 1
    for it in result["summary"]["feed"]:
        en = translations.get(it["id"])
        if en and not it.get("en"):
            it["en"] = en[:220]
    result["to_translate"] = [t for t in result["to_translate"] if t["id"] not in translations]
    return n


def short_en(name: str) -> str:
    """'TOYOTA MOTOR CORPORATION' -> 'Toyota Motor'; 'Nagase & Co., Ltd.' -> 'Nagase'; 'THE ZENITAKA CORP.' -> 'Zenitaka'."""
    n = re.sub(r"\s+", " ", name or "").strip()
    n = re.sub(r"[,.]?\s*(&\s*)?(CO\.?,?\s*LTD\.?|CORPORATION|CORP\.?|INCORPORATED|INC\.?|LIMITED|LTD\.?|K\.K\.|"
               r"KABUSHIKI KAISHA|COMPANY,? LIMITED|COMPANY|CO\.)$", "", n, flags=re.I).strip(" ,.&")
    n = re.sub(r"^THE\s+", "", n, flags=re.I)
    if n.isupper() or n.islower():
        small = {"AND": "and", "OF": "of", "FOR": "for", "THE": "the", "IN": "in"}
        n = " ".join(small.get(w, w if re.fullmatch(r"[A-Z]{1,3}|[A-Z]&[A-Z]", w) else w.capitalize())
                     for w in n.split(" "))
    return n[:32]


def universe_en_doc(ctx: Context) -> dict:
    """code -> short English name (EDINET's register), so the page can show any ticker in English."""
    return dict(date=today_jst().isoformat(), rows={c.code: short_en(c.name_en) for c in ctx.universe if c.name_en})


def universe_doc(ctx: Context) -> dict:
    """code -> [name, market, last price] for every listed company, so a new ticker resolves on the page at once."""
    rows = {}
    for c in ctx.universe:
        q = ctx.prices.quotes.get(c.code)
        rows[c.code] = [c.name[:24], c.market, round(q.price) if q and q.price else None]
    return dict(date=today_jst().isoformat(), rows=rows)
