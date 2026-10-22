"""Exports: an Excel workbook (one tab per view) and the documents the dashboard reads (state/out/db/*.json).

Dashboard collections (mirroring the Korea Filing Radar):
  runs/<YYYYMMDD>      one doc per day: {date, window, scanned, hits[], notes, generated_at}
  watchlist/<list>     {count, rows[]} for re_netnet, net_net, cash_rich, deep_value, treasury, parent_sub
  watchlist/meta       {date, price_date, universe, screened, ...}
  frontrun/latest      {date, rows[]}
  mine/latest          {date, rows[]}  coverage of the reference ranking + the user's own watchlist
"""
from __future__ import annotations

import json
from pathlib import Path

from .classify import CAT_LABEL
from .store import Store

LIST_TITLES = {"re_netnet": "RE net-net (cheapness)", "net_net": "Net-nets", "cash_rich": "Cash-rich",
               "deep_value": "Deep value", "treasury": "Treasury piles", "parent_sub": "Listed subsidiaries"}


def db_docs(store: Store) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    runs_dir = store.root / "runs"
    for p in sorted(runs_dir.glob("*.json"))[-60:]:
        run = json.loads(p.read_text())
        docs[f"runs/{run['date']}"] = run
    value = store.read_json("value.json", {})
    lists = value.get("lists") or {}
    for k in LIST_TITLES:
        if k in lists:
            docs[f"watchlist/{k}"] = lists[k]
    if value.get("meta"):
        docs["watchlist/meta"] = value["meta"]
    fr = store.read_json("frontrun.json")
    if fr:
        docs["frontrun/latest"] = dict(date=fr["date"], rows=fr["rows"][:250])
    cov = store.read_json("coverage.json")
    if cov:
        docs["mine/latest"] = cov
    pdir = store.root / "portfolio"
    if (pdir / "summary.json").exists():
        for p in pdir.glob("*.json"):
            if p.stem in ("to_translate", "translations"):
                continue
            docs[f"portfolio/{p.stem}"] = json.loads(p.read_text())
    uni = store.read_json("universe.json")
    if uni:
        docs["universe/names"] = uni
    if store.fund_path.exists():
        from .dbsync import store_chunks
        for k, v in store_chunks(store).items():
            docs[f"cache/{k}"] = v
    return docs


MAX_DOC = 250_000  # the dashboard database caps a document at 256 KiB


def fit(doc: dict) -> dict:
    """Trim the longest list in a document until it fits the database's per-document cap."""
    def size(d):
        return len(json.dumps(d, ensure_ascii=False, separators=(",", ":"), default=str).encode())
    key = "hits" if "hits" in doc else "rows" if isinstance(doc.get("rows"), list) else "items" if "items" in doc else \
        "feed" if "feed" in doc else None
    if key is None:
        return doc
    doc = dict(doc)
    if size(doc) > MAX_DOC and key == "hits":
        doc["hits"] = [{k: v for k, v in h.items() if k not in ("fin", "title")} for h in doc["hits"]]
    while size(doc) > MAX_DOC and doc[key]:
        doc[key] = doc[key][:int(len(doc[key]) * 0.9)]
        doc["truncated"] = True
    return doc


def write_db_docs(store: Store) -> Path:
    out = store.root / "out" / "db"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    for key, doc in db_docs(store).items():
        doc = fit(doc)
        p = out / (key.replace("/", "__") + ".json")
        p.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":"), default=str))
    return out


def write_xlsx(store: Store) -> Path:
    import pandas as pd
    path = store.root / "out" / "japan_radar.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = store.read_json("value.json", {})
    lists = value.get("lists") or {}
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        # 1. cheapness: the reference-style ranking
        rows = (lists.get("re_netnet") or {}).get("rows") or []
        if rows:
            df = pd.DataFrame([{
                "順位 Rank": i + 1, "コード Code": r["code"], "銘柄名 Name": r["name"], "市場 Mkt": r["market"],
                "指数 Index": r.get("re_index"), "時価総額 Mcap (億円)": _oku(r.get("mcap")),
                "正味流動資産 NCA (億円)": _oku(r.get("nca")), "不動産・土地 RE (億円)": _oku(r.get("real_estate")),
                "PBR": r.get("pb"), "PER": r.get("pe"), "ADV (百万円)": _m(r.get("adv")),
                "Lists": ", ".join(r.get("lists") or []), "Source": r.get("source"),
            } for i, r in enumerate(rows)])
            df.to_excel(xw, sheet_name="Cheapness (RE net-net)", index=False)
        # 2. radar hits
        hits = []
        for p in sorted((store.root / "runs").glob("*.json")):
            run = json.loads(p.read_text())
            hits.extend(run.get("hits") or [])
        if hits:
            df = pd.DataFrame([{
                "Date": h.get("date"), "Stars": h.get("stars"), "Code": h.get("code"), "Name": h.get("company"),
                "Mkt": h.get("market"), "Category": CAT_LABEL.get(h.get("cat"), h.get("cat")),
                "Headline": h.get("headline"), "Why": "; ".join(h.get("why") or []), "Spread %": h.get("premium_pct"),
                "Mcap (億円)": _oku(h.get("mcap")), "RE index": h.get("re_index"), "P/B": h.get("pb"),
                "Passes": h.get("passes"), "Filtered": ", ".join(h.get("fail_reasons") or []),
                "Title (JP)": h.get("title"), "Filing": h.get("url"),
            } for h in sorted(hits, key=lambda x: (x.get("date") or "", x.get("stars") or 0), reverse=True)])
            df.to_excel(xw, sheet_name="Radar", index=False)
        # 3. front-run
        fr = (store.read_json("frontrun.json") or {}).get("rows") or []
        if fr:
            df = pd.DataFrame([{
                "Stars": r["stars"], "Prob %": round(r["prob"] * 100, 1), "Code": r["code"], "Name": r["name"],
                "Mkt": r["market"], "RE index": r.get("re_index"), "P/B": r.get("pb"), "Mcap (億円)": _oku(r.get("mcap")),
                "Signals": "; ".join(r.get("reasons") or []), "Lists": ", ".join(r.get("lists") or []),
            } for r in fr])
            df.to_excel(xw, sheet_name="Front-run", index=False)
        # 4. other value lists
        for k, title in LIST_TITLES.items():
            if k == "re_netnet" or not (lists.get(k) or {}).get("rows"):
                continue
            pd.DataFrame(lists[k]["rows"]).to_excel(xw, sheet_name=title[:31], index=False)
        # 5. coverage of the reference ranking + watchlist
        cov = (store.read_json("coverage.json") or {}).get("rows") or []
        if cov:
            pd.DataFrame(cov).to_excel(xw, sheet_name="Watchlist coverage", index=False)
    return path


def _oku(v):
    return None if v is None else round(v / 1e8, 1)


def _m(v):
    return None if v is None else round(v / 1e6, 1)


def export_all(store: Store) -> list[Path]:
    return [write_db_docs(store), write_xlsx(store)]
