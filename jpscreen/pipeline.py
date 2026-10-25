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
from .value import CRITERIA, ValueRow, build_lists, build_row, load_reference, reference_row, fit_scores

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
                   codes: Optional[set] = None, priority: Optional[list] = None, workers: int = 5,
                   skip_fresh_days: int = 0, since: Optional[dt.date] = None, log_=print) -> int:
    """Annual + latest interim report for each listed company, parsed into the fundamentals store.

    Packages are downloaded and parsed in parallel (not cached on disk); `priority` codes go first.
    """
    if not edinet.enabled:
        log_("EDINET_API_KEY not set: skipping the EDINET refresh (value screen uses stored/harvested data)")
        return 0
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .http import Http
    end = today_jst()
    start = since or (end - dt.timedelta(days=int(months * 30.5)))
    latest = edinet.latest_reports(start, end)
    log_(f"EDINET: latest reports for {len(latest)} filers")
    order = list(priority or []) + [c.code for c in ctx.universe]
    prio_set = set(priority or [])
    seen, jobs = set(), []
    for code in order:
        if code in seen:
            continue
        seen.add(code)
        comp = ctx.universe.get(code)
        if comp is None or (codes and code not in codes):
            continue
        rep = latest.get(comp.edinet_code)
        if not rep:
            continue
        for kind in ("annual", "interim"):
            fl = rep.get(kind)
            if not fl or (kind == "interim" and rep.get("annual") and fl.submitted < rep["annual"].submitted):
                continue
            if skip_fresh_days and ctx.store.has_source(code, f"edinet:{fl.doc_id}"):
                continue
            jobs.append((code, kind, fl))
        if limit and len({j[0] for j in jobs}) >= limit:
            break
    # annual reports for everyone first (notes, holders, history), fresher interim balance sheets after
    jobs.sort(key=lambda j: (j[1] != "annual", order.index(j[0]) if j[0] in prio_set else len(order)))
    log_(f"EDINET: {len(jobs)} reports to read for {len({j[0] for j in jobs})} companies")
    h0 = edinet.http

    def work(job):
        code, kind, fl = job
        ed = Edinet(Http(cache_dir=h0.cache_dir, min_interval=0.5), key=edinet.key)
        fs = ed.facts(fl.doc_id)
        if not fs:
            return job, None
        try:
            return job, from_facts(fs, code, f"edinet:{fl.doc_id}", annual=(kind == "annual"))
        except Exception as e:  # one malformed report must not stop the run
            log.info("parse %s: %s", fl.doc_id, e)
            return job, None

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, j) for j in jobs]
        for fut in as_completed(futs):
            (code, kind, fl), f = fut.result()
            if f is not None:
                ctx.store.put_fundamentals(code, f, fl.submitted.date(), annual=(kind == "annual"))
            done += 1
            if done % 200 == 0:
                ctx.store.save()
                log_(f"  {done}/{len(jobs)} reports read")
    ctx.store.save()
    return done


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
                 extra_codes: Optional[set] = None, log_=print) -> dict:
    """Two passes: every company on bulk prices (no trading history), then full quotes for the candidates."""
    rows: dict[str, ValueRow] = {}
    stored = set(ctx.store.fundamentals_codes())
    ref_rows = {norm_code(r["code"]): r for r in (load_reference(reference) if reference else [])}
    wanted = set(stored) | set(ref_rows) | set(ctx.mine) | set(extra_codes or ())
    financial = {c.code for c in ctx.universe if c.financial}
    n_bulk = ctx.prices.load_bulk(ctx.tdnet.http) if price_all else 0
    log_(f"value screen over {len(wanted)} companies ({len(stored)} with stored fundamentals, "
         f"{len(ref_rows)} reference rows, {len(ctx.mine)} on your watchlist; {n_bulk} bulk prices)")

    def row_for(code):
        comp = ctx.universe.get(code)
        if comp is None:
            return None
        f = ctx.store.fundamentals(code)
        has_notes = bool(f and f.rental_note)
        q = ctx.prices.quotes.get(code)
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
        return r

    for code in wanted:
        r = row_for(code)
        if r is not None:
            rows[code] = r
    # candidates: anything near a list threshold, plus the names that must always be tracked
    near = {c for c, r in rows.items() if (r.re_index is not None and r.re_index < 1.2) or (r.ncav_mcap or 0) >= 0.8
            or (r.am_mcap or 0) >= 0.5 or ((r.pe or 99) <= 7 and (r.pb or 9) <= 0.7) or (r.treasury_pct or 0) >= 10
            or ((r.top_holder_pct or 0) >= 50 and (r.pb or 9) <= 1.1)}
    must = set(ref_rows) | set(ctx.mine) | set(extra_codes or ())
    cand = (near | must) - financial
    log_(f"  full quotes (with trading history) for {len(cand)} candidates")
    ctx.prices.prefetch({c: ctx.universe.get(c).yahoo_symbols for c in cand if ctx.universe.get(c)})
    if price_all:
        # the company's forecast EPS and dividend (forward P/E, yield) for everyone else: pages parsed, not kept
        rest = {c: ctx.universe.get(c).yahoo_symbols for c in (set(rows) - cand - financial) if ctx.universe.get(c)}
        log_(f"  quote pages (forecast EPS, dividend) for {len(rest)} more companies")
        ctx.prices.prefetch(rest, store=False)
    for code in set(rows):
        r = row_for(code)
        if r is not None:
            rows[code] = r
    ctx.prices.save()
    n_fit = fit_scores(rows.values(), financial)
    log_(f"  FIT scored for {n_fit} companies")
    lists = build_lists(rows.values(), crit, financial)
    meta = dict(date=dt.datetime.now(JST).date().isoformat(), price_date=ctx.prices.asof, universe=len(ctx.universe),
                screened=len(rows), with_fundamentals=len(stored), reference=Path(reference).name if reference else None,
                edinet=sum(1 for r in rows.values() if (r.source or "").startswith("edinet")))
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
