"""Weekly value screen, EDINET refresh, front-run radar and the watchlist coverage check."""
from __future__ import annotations

import csv
import datetime as dt
import logging
from pathlib import Path
from typing import Optional

from .edinet import ANNUAL, AMENDED_ANNUAL, LARGE_HOLDING, LARGE_HOLDING_AMEND, Edinet, holding_from_facts
from .frontrun import history_signals, score
from .fundamentals import from_facts
from .radar import Context
from .util import JST, norm_code, today_jst
from .value import CRITERIA, ValueRow, build_lists, build_row, load_reference, reference_row

log = logging.getLogger(__name__)


def load_mine(path: str | Path) -> dict:
    out = {}
    p = Path(path)
    if not p.exists():
        return out
    for r in csv.DictReader(open(p, encoding="utf-8")):
        code = norm_code(r.get("code"))
        if code:
            out[code] = dict(name=r.get("name") or "", source=r.get("source") or "", note=r.get("note") or "")
    return out


# ---------------------------------------------------------------- EDINET refresh (needs a key)

def refresh_edinet(ctx: Context, edinet: Edinet, months: int = 15, limit: Optional[int] = None,
                   codes: Optional[set] = None, log_=print) -> int:
    if not edinet.enabled:
        log_("EDINET_API_KEY not set: skipping the EDINET refresh (value screen uses stored/harvested data)")
        return 0
    end = today_jst()
    start = end - dt.timedelta(days=int(months * 30.5))
    latest = edinet.latest_reports(start, end)
    log_(f"EDINET: latest reports for {len(latest)} filers")
    n = 0
    for comp in ctx.universe:
        if codes and comp.code not in codes:
            continue
        rep = latest.get(comp.edinet_code)
        if not rep:
            continue
        for kind in ("annual", "interim"):
            fl = rep.get(kind)
            if not fl:
                continue
            if kind == "interim" and rep.get("annual") and fl.submitted < rep["annual"].submitted:
                continue
            fs = edinet.facts(fl.doc_id)
            if not fs:
                continue
            f = from_facts(fs, comp.code, f"edinet:{fl.doc_id}", annual=(kind == "annual"))
            ctx.store.put_fundamentals(comp.code, f, fl.submitted.date(), annual=(kind == "annual"))
        n += 1
        if n % 100 == 0:
            ctx.store.save()
            log_(f"  {n} companies refreshed")
        if limit and n >= limit:
            break
    ctx.store.save()
    return n


def edinet_holdings(ctx: Context, edinet: Edinet, start: dt.date, end: dt.date, log_=print) -> list[dict]:
    """5% / change reports in the window, filed by known activists or for a management-influence purpose."""
    if not edinet.enabled:
        return []
    from .frontrun import match_activist
    out = []
    for f in edinet.documents_range(start, end, {LARGE_HOLDING, LARGE_HOLDING_AMEND}):
        comp = ctx.universe.by_edinet.get(f.issuer_edinet_code or f.subject_edinet_code or "")
        if not comp:
            continue
        act = match_activist(f.filer, ctx.activists)
        if not act and comp.code not in ctx.mine:
            continue
        info = {}
        fs = edinet.facts(f.doc_id) if f.xbrl else None
        if fs:
            info = holding_from_facts(fs)
        out.append(dict(code=comp.code, company=comp.name, date=f.submitted.date().isoformat(), filer=f.filer,
                        activist=act[0] if act else None, kind=act[1] if act else None, description=f.description,
                        pct=info.get("pct"), pct_before=info.get("pct_before"), purpose=info.get("purpose"), url=f.url))
    log_(f"EDINET large-holding reports of interest: {len(out)}")
    return out


# ---------------------------------------------------------------- value screen

def value_screen(ctx: Context, reference: Optional[str], crit: dict = CRITERIA, price_all: bool = True,
                 log_=print) -> dict:
    rows: dict[str, ValueRow] = {}
    stored = set(ctx.store.fundamentals_codes())
    ref_rows = {norm_code(r["code"]): r for r in (load_reference(reference) if reference else [])}
    wanted = set(stored) | set(ref_rows) | set(ctx.mine)
    financial = {c.code for c in ctx.universe if c.financial}
    log_(f"value screen over {len(wanted)} companies ({len(stored)} with stored fundamentals, "
         f"{len(ref_rows)} reference rows, {len(ctx.mine)} on your watchlist)")
    ctx.prices.prefetch({c: ctx.universe.get(c).yahoo_symbols for c in wanted
                         if ctx.universe.get(c) and (price_all or c in ref_rows or c in ctx.mine)})
    for i, code in enumerate(sorted(wanted)):
        comp = ctx.universe.get(code)
        if comp is None:
            continue
        f = ctx.store.fundamentals(code)
        has_notes = bool(f and f.rental_note)
        need_price = price_all or code in ref_rows or code in ctx.mine
        q = ctx.prices.get(code, comp.yahoo_symbols, refresh=False) if need_price else None
        if code in ref_rows and not has_notes:
            r = reference_row(ref_rows[code], q)
            r.sector = comp.sector
            r.market = ref_rows[code]["market"] or comp.market
        else:
            r = build_row(comp, f, q, crit)
            if code in ref_rows:
                r.flags["ref_rank"] = int(ref_rows[code]["rank"])
                r.flags["ref_index"] = float(ref_rows[code]["index"])
        if q and r.pb is None and q.pbr:
            r.pb = q.pbr
        if q and r.pe is None and q.per:
            r.pe = q.per
        rows[code] = r
        if (i + 1) % 200 == 0:
            ctx.prices.save()
            log_(f"  priced {i + 1}")
    ctx.prices.save()
    lists = build_lists(rows.values(), crit, financial)
    meta = dict(date=dt.datetime.now(JST).date().isoformat(), price_date=ctx.prices.asof, universe=len(ctx.universe),
                screened=len(rows), with_fundamentals=len(stored), reference=Path(reference).name if reference else None,
                edinet=any((r.source or "").startswith("edinet") for r in rows.values()))
    return dict(rows=rows, lists=lists, meta=meta)


def frontrun_screen(ctx: Context, rows: dict, lists: dict, holdings: list[dict], log_=print) -> list[dict]:
    """Pre-deal probabilities for everything on a value list plus the user's watchlist."""
    candidates = set(lists.get("_tags", {})) | set(ctx.mine)
    today = today_jst()
    since = today - dt.timedelta(days=3 * 365)
    by_code_hold = {}
    for hdoc in holdings:
        by_code_hold.setdefault(hdoc["code"], []).append(hdoc)
    ctx.tdnet.prefetch_companies([c for c in candidates if c in rows])
    out = []
    for code in sorted(candidates):
        r = rows.get(code)
        if r is None:
            continue
        try:
            hist = ctx.tdnet.list_company(code, limit=300)
        except Exception as e:
            log_(f"  history {code}: {e}")
            hist = []
        events = history_signals(hist, since)
        for hd in by_code_hold.get(code, []):
            events.append(dict(date=hd["date"], kind="holding", flags={"activist": hd.get("activist")},
                               title=f"{hd['filer']} {hd.get('description') or ''} {hd.get('pct') or ''}%", url=hd["url"]))
        f = ctx.store.fundamentals(code)
        fr = score(r, f, events, ctx.activists, today)
        d = fr.to_dict()
        d["lists"] = r.lists
        d["mine"] = code in ctx.mine
        d["sector"] = r.sector
        out.append(d)
    out.sort(key=lambda d: (-d["prob"], d["re_index"] if d["re_index"] is not None else 9))
    log_(f"front-run radar: {len(out)} companies, {sum(1 for d in out if d['stars'] >= 4)} at 4-5 stars")
    return out


def coverage(ctx: Context, rows: dict, lists: dict, reference: Optional[str], frontrun: list[dict]) -> list[dict]:
    """Does the screen surface every reference name and every name on the user's watchlist? Says why not."""
    ref = {norm_code(r["code"]): r for r in (load_reference(reference) if reference else [])}
    fr = {d["code"]: d for d in frontrun}
    out = []
    for code in sorted(set(ref) | set(ctx.mine), key=lambda c: (int(ref[c]["rank"]) if c in ref else 999, c)):
        r = rows.get(code)
        comp = ctx.universe.get(code)
        tags = lists.get("_tags", {}).get(code, [])
        rank = None
        if r is not None and "re_netnet" in tags:
            rank = next((i + 1 for i, x in enumerate(lists["re_netnet"]["rows"]) if x["code"] == code), None)
        reason = None
        if comp is None:
            reason = "not in the listed universe (delisted or code changed?)"
        elif r is None:
            reason = "no data"
        elif r.mcap is None:
            reason = "no price / market cap"
        elif r.re_index is None:
            reason = "no balance-sheet data (needs EDINET key or an earnings release)"
        elif r.re_index >= 1:
            reason = f"RE net-net index now {r.re_index:.2f} (≥ 1)"
        out.append(dict(code=code, name=(comp.name if comp else (ref.get(code) or {}).get("name") or ctx.mine.get(code, {}).get("name")),
                        ref_rank=int(ref[code]["rank"]) if code in ref else None,
                        ref_index=float(ref[code]["index"]) if code in ref else None,
                        re_index=round(r.re_index, 3) if r and r.re_index is not None else None,
                        rank=rank, lists=tags, mine=code in ctx.mine,
                        frontrun_stars=fr.get(code, {}).get("stars"), frontrun_prob=fr.get(code, {}).get("prob"),
                        captured=reason is None and bool(tags), reason=reason))
    return out
