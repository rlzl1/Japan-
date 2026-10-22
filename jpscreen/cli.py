"""Command line.

  python -m jpscreen radar   [--from YYYY-MM-DD --to YYYY-MM-DD]   daily filing radar (TDnet)
  python -m jpscreen value                                         weekly value screen + front-run + coverage
  python -m jpscreen edinet  [--months 15] [--limit N]             refresh fundamentals from EDINET (needs key)
  python -m jpscreen all     [--days 1]                            edinet (if key) + value + radar + export
  python -m jpscreen export                                        Excel workbook + dashboard data from state/

Environment: EDINET_API_KEY (free, https://api.edinet-fsa.go.jp/) unlocks whole-market fundamentals,
the rental real-estate notes and 5% holding reports. Without it the radar still runs on TDnet and the
value screen runs on the reference list, your watchlist and every earnings release harvested so far.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

from .edinet import Edinet
from .export import export_all
from .frontrun import load_activists
from .http import Http
from .pipeline import coverage, edinet_holdings, frontrun_screen, load_mine, refresh_edinet, value_screen
from .prices import PriceBook
from .radar import Context, scan
from .store import Store
from .tdnet import TDnet
from .universe import Universe
from .util import JST, today_jst

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """KEY=VALUE lines from a local, gitignored .env (the EDINET key never goes in the repo)."""
    import os
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def build_context(args) -> tuple[Context, Edinet]:
    http = Http(cache_dir=args.cache, offline=args.offline)
    universe = Universe.load(http)
    store = Store(args.state)
    ctx = Context(tdnet=TDnet(http), universe=universe, prices=PriceBook(http, Path(args.state) / "prices.json"),
                  store=store, activists=load_activists(DATA / "activists.csv"), mine=load_mine(args.watchlist))
    value = store.read_json("value.json", {})
    ctx.value_tags = (value.get("lists") or {}).get("_tags", {})
    ctx.value_rows = {r["code"]: r for r in (value.get("rows_all") or [])}
    return ctx, Edinet(http)


def default_window() -> tuple[dt.date, dt.date]:
    t = today_jst()
    start = t - dt.timedelta(days=2) if t.weekday() == 0 else t
    return start, t


def cmd_radar(args, ctx, edinet):
    start, end = default_window()
    if args.days:
        start = end - dt.timedelta(days=args.days - 1)
    if args.date_from:
        start = dt.date.fromisoformat(args.date_from)
    if args.date_to:
        end = dt.date.fromisoformat(args.date_to)
    res = scan(ctx, start, end)
    holds = edinet_holdings(ctx, edinet, start, end)
    if holds:
        ctx.store.write_json("holdings_latest.json", holds)
    for run in res["runs"]:
        ctx.store.write_json(f"runs/{run['date']}.json", run)
    return res


def cmd_value(args, ctx, edinet):
    ref = args.reference if args.reference and Path(args.reference).exists() else None
    v = value_screen(ctx, ref, price_all=not args.no_price_all)
    holds = []
    if edinet.enabled:
        end = today_jst()
        holds = edinet_holdings(ctx, edinet, end - dt.timedelta(days=365), end)
    fr = frontrun_screen(ctx, v["rows"], v["lists"], holds)
    cov = coverage(ctx, v["rows"], v["lists"], ref, fr)
    ctx.store.write_json("value.json", dict(meta=v["meta"], lists=v["lists"],
                                            rows_all=[r.to_dict() for r in v["rows"].values()]))
    ctx.store.write_json("frontrun.json", dict(date=v["meta"]["date"], rows=fr))
    ctx.store.write_json("coverage.json", dict(date=v["meta"]["date"], rows=cov))
    missed = [c for c in cov if not c["captured"]]
    print(f"coverage: {len(cov) - len(missed)}/{len(cov)} reference + watchlist names surfaced")
    for c in missed:
        print(f"  not on a list: {c['code']} {c['name']}: {c['reason'] or 'not on any list'}")
    return v


def cmd_port(args, ctx, edinet):
    import json as _json
    from .portfolio import build, load_holdings, save, universe_doc
    holdings = load_holdings(args.tracker_dir, args.positions_dir)
    if not holdings:
        print("portfolio: no holdings found (pass --tracker-dir and/or --positions-dir)")
        return None
    runs = [_json.loads(p.read_text()) for p in sorted((Path(args.state) / "runs").glob("*.json"))]
    fr = (ctx.store.read_json("frontrun.json") or {}).get("rows") or []
    res = build(ctx, holdings, runs, fr, prev_dir=args.prev_dir, edinet=edinet if edinet.enabled else None)
    save(res, Path(args.state) / "portfolio")
    ctx.store.write_json("universe.json", universe_doc(ctx))
    ctx.prices.save()
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["radar", "value", "edinet", "all", "export", "port", "port-apply", "daily", "batches"])
    ap.add_argument("--db-dir", help="the dashboard database downloaded as JSON files (one folder per collection)")
    ap.add_argument("--versions", help="JSON {\"collection/doc\": version} of the downloaded documents")
    ap.add_argument("--weekly", action="store_true", help="daily: also rerun the whole-market value screen")
    ap.add_argument("--tracker-dir", help="Tokyo Filings Tracker `stocks` documents (JSON files)")
    ap.add_argument("--positions-dir", help="radar page `positions` documents (JSON files)")
    ap.add_argument("--prev-dir", help="previous `portfolio` documents from the page (carries translations and 'new')")
    ap.add_argument("--translations", help="JSON {item id: English title} to merge into the portfolio documents")
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--days", type=int, default=0, help="radar window in calendar days ending today")
    ap.add_argument("--months", type=int, default=15)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--state", default=str(ROOT / "state"))
    ap.add_argument("--cache", default=str(ROOT / ".cache" / "http"))
    ap.add_argument("--watchlist", default=str(DATA / "watchlist.csv"))
    ap.add_argument("--reference", default=str(DATA / "reference_netnet_2026-10-09.csv"))
    ap.add_argument("--no-price-all", action="store_true", help="price only reference + watchlist names")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--codes", default="", help="comma-separated codes to put first (edinet) or to limit to (--only-codes)")
    ap.add_argument("--only-codes", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    ctx, edinet = build_context(args)
    print(f"universe: {len(ctx.universe)} listed companies; EDINET key: {'yes' if edinet.enabled else 'no'}")
    if args.cmd in ("edinet", "all"):
        prio = list(load_mine(args.watchlist)) + [c for c in (args.codes or "").split(",") if c]
        refresh_edinet(ctx, edinet, months=args.months, limit=args.limit, priority=prio,
                       codes=set(args.codes.split(",")) if args.only_codes and args.codes else None,
                       skip_fresh_days=1)
    if args.cmd in ("value", "all"):
        cmd_value(args, ctx, edinet)
        ctx, edinet = build_context(args)   # pick up the fresh value tags for the radar
    if args.cmd in ("radar", "all"):
        cmd_radar(args, ctx, edinet)
    if args.cmd == "port":
        cmd_port(args, ctx, edinet)
    if args.cmd == "port-apply":
        import json as _json
        from .portfolio import apply_translations, load_saved, save
        out = Path(args.state) / "portfolio"
        res = load_saved(out)
        n = apply_translations(res, _json.loads(Path(args.translations).read_text()) if args.translations else {})
        save(res, out)
        print(f"applied {n} translations; {len(res['to_translate'])} titles still untranslated")
    if args.cmd == "daily":
        cmd_daily(args, ctx, edinet)
    if args.cmd in ("export", "all", "radar", "value", "port", "port-apply", "daily"):
        paths = export_all(ctx.store)
        for p in paths:
            print("wrote", p)
    if args.cmd in ("batches", "daily", "port-apply"):
        cmd_batches(args, ctx)
    return 0


def cmd_daily(args, ctx, edinet):
    """One refresh, as the scheduled task runs it: restore state from the page's database, read new EDINET filings,
    rerun the value screen on Mondays (or --weekly), scan TDnet, rebuild the portfolio."""
    import json as _json
    from .dbsync import collection_files, load_value_tags, restore_store
    db = args.db_dir
    n = restore_store(ctx.store, db)
    ctx.store.save()
    print(f"restored {n} companies' fundamentals from the page's database")
    if not ctx.value_tags:
        ctx.value_tags, ctx.value_rows = load_value_tags(db)
    today = today_jst()
    last = None
    for p in collection_files(db, "config"):
        if p.stem == "refresh":
            d = _json.loads(p.read_text())
            last = (d.get("data") or d).get("lastRunAt")
    since = dt.date.fromisoformat(last[:10]) - dt.timedelta(days=1) if last else today - dt.timedelta(days=7)
    if edinet.enabled:
        refresh_edinet(ctx, edinet, since=since, skip_fresh_days=1)
    else:
        print("EDINET_API_KEY not set: fundamentals not refreshed this run")
    weekly = args.weekly or today.weekday() == 0 or not ctx.value_tags
    if weekly:
        cmd_value(args, ctx, edinet)
        value = ctx.store.read_json("value.json", {})
        ctx.value_tags = (value.get("lists") or {}).get("_tags", {})
        ctx.value_rows = {r["code"]: r for r in (value.get("rows_all") or [])}
    args.date_from = args.date_from or max(since + dt.timedelta(days=1), today - dt.timedelta(days=6)).isoformat()
    cmd_radar(args, ctx, edinet)
    for p in collection_files(db, "frontrun"):
        if p.stem == "latest" and not ctx.store.read_json("frontrun.json"):
            d = _json.loads(p.read_text())
            ctx.store.write_json("frontrun.json", d.get("data") or d)
    if args.prev_dir is None and db:
        args.prev_dir = str(Path(db) / "portfolio")
    if args.positions_dir is None and db:
        args.positions_dir = str(Path(db) / "positions")
    res = cmd_port(args, ctx, edinet)
    if res:
        s = res["summary"]
        new = sum(h["new"] for h in s["holdings"])
        print(f"SUMMARY: {len(s['holdings'])} holdings, {new} new filings/stories; "
              f"{len(res['to_translate'])} titles to translate" + ("; weekly value screen rerun" if weekly else ""))


def cmd_batches(args, ctx):
    """state/out/batches.json: pinned ArtifactData batch writes for every exported document that changed."""
    import json as _json
    from .dbsync import collection_files, plan_batches
    versions = _json.loads(Path(args.versions).read_text()) if args.versions and Path(args.versions).exists() else {}
    out_db = Path(args.state) / "out" / "db"
    deletes = []
    if args.db_dir:
        exported = {p.stem for p in out_db.glob("*.json")}
        for p in collection_files(args.db_dir, "portfolio"):
            if f"portfolio__{p.stem}" not in exported:
                deletes.append(f"portfolio/{p.stem}")      # a holding that was removed
    batches = plan_batches(out_db, args.db_dir, versions, deletes)
    path = Path(args.state) / "out" / "batches.json"
    path.write_text(_json.dumps(batches, ensure_ascii=False, indent=1))
    print(f"wrote {path}: {sum(len(b) for b in batches)} writes in {len(batches)} batches")


if __name__ == "__main__":
    sys.exit(main())
