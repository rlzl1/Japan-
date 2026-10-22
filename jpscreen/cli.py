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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["radar", "value", "edinet", "all", "export"])
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
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    ctx, edinet = build_context(args)
    print(f"universe: {len(ctx.universe)} listed companies; EDINET key: {'yes' if edinet.enabled else 'no'}")
    if args.cmd in ("edinet", "all"):
        refresh_edinet(ctx, edinet, months=args.months, limit=args.limit)
    if args.cmd in ("value", "all"):
        cmd_value(args, ctx, edinet)
        ctx, edinet = build_context(args)   # pick up the fresh value tags for the radar
    if args.cmd in ("radar", "all"):
        cmd_radar(args, ctx, edinet)
    if args.cmd in ("export", "all", "radar", "value"):
        paths = export_all(ctx.store)
        for p in paths:
            print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
