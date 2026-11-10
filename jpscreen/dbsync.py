"""Moving state between the dashboard's database and a fresh container.

A scheduled refresh starts with an empty disk, so everything the next run needs lives in the page's database:
  cache/fund_NN      the fundamentals store, in chunks (EDINET annual/interim data for every listed company)
  watchlist/*        value lists (tags for the radar), frontrun/latest, mine/latest
  portfolio/*        last refresh (carries English titles and "new" markers), positions/* (added on the page)
The refresh downloads those collections as JSON files (ArtifactData list with out_dir), runs `jpscreen daily`, and
writes back only what changed, in pinned batches built by `plan_batches`.
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import Optional

from .store import Store

BUCKETS = 48
CHUNK_BYTES = 230_000
BATCH_BYTES = 900_000
BATCH_DOCS = 50
SLIM_DROP = {"officers_shares", "interest_bearing_debt", "inventories"}


def _read(p: Path) -> dict:
    d = json.loads(p.read_text())
    return d["data"] if isinstance(d.get("data"), dict) and "version" in d else d


def collection_files(db_dir: Optional[str | Path], collection: str) -> list[Path]:
    if not db_dir:
        return []
    d = Path(db_dir) / collection
    return sorted(d.glob("*.json")) if d.exists() else []


def restore_store(store: Store, db_dir: Optional[str | Path]) -> int:
    """Load cache/fund_* chunks into the local store (local entries win when they are newer)."""
    recs = store._load_fund()
    n = 0
    for p in collection_files(db_dir, "cache"):
        if not p.stem.startswith("fund_"):
            continue
        for code, rec in (_read(p).get("recs") or {}).items():
            cur = recs.get(code)
            newer = lambda r: max((r.get(k) or {}).get("filed", "") for k in ("annual", "latest"))  # noqa: E731
            if cur is None or newer(rec) > newer(cur):
                recs[code] = rec
                n += 1
    return n


def store_chunks(store: Store, buckets: int = BUCKETS) -> dict[str, dict]:
    """The fundamentals store as documents under the database's 256 KiB cap.

    Each company always lands in the same document (a hash of its code), so a day's new filings rewrite only the
    documents that hold those companies; the bucket count doubles if one ever outgrows the cap.
    """
    recs = store._load_fund()
    chunks: dict[str, dict] = {}
    sizes: dict[str, int] = {}
    for code in sorted(recs):
        rec = {}
        ann = recs[code].get("annual")
        for slot, v in recs[code].items():
            if not v or (slot == "latest" and ann and v.get("source") == ann.get("source")):
                continue          # the annual report is also the latest: store it once
            rec[slot] = _slim(v, keep_holders=slot == "annual" or not ann)
        key = f"fund_{zlib.crc32(code.encode()) % buckets:02d}"
        chunks.setdefault(key, dict(recs={}))["recs"][code] = rec
        sizes[key] = sizes.get(key, 0) + len(json.dumps(rec, ensure_ascii=False, separators=(",", ":")).encode())
    if sizes and max(sizes.values()) > CHUNK_BYTES:
        return store_chunks(store, buckets * 2)
    return dict(sorted(chunks.items()))


def _slim(v: dict, keep_holders: bool) -> dict:
    out = {}
    for k, x in v.items():
        if k in SLIM_DROP or x in (None, [], ""):
            continue
        if k == "major_holders":
            if not keep_holders:
                continue
            x = [{"name": (h.get("name") or "")[:40], "pct": round(h.get("pct") or 0, 2)} for h in x[:8]]
        elif isinstance(x, float):
            x = round(x) if abs(x) >= 1000 else round(x, 4)
        elif isinstance(x, list):
            x = [round(y) if isinstance(y, float) and abs(y) >= 1000 else y for y in x]
        out[k] = x
    return out


def load_value_tags(db_dir: Optional[str | Path]) -> tuple[dict, dict]:
    """Value-list tags and rows from downloaded watchlist documents (when the local state has none)."""
    tags: dict[str, list[str]] = {}
    rows: dict[str, dict] = {}
    for p in collection_files(db_dir, "watchlist"):
        if p.stem == "meta":
            continue
        doc = _read(p)
        for r in doc.get("rows") or []:
            tags.setdefault(r["code"], []).append(p.stem)
            rows.setdefault(r["code"], r)
    return tags, rows


def restore_value(store: Store, db_dir: Optional[str | Path]) -> Optional[dict]:
    """The weekly value screen rebuilt from the page's database, for a container that starts empty: every screened
    company from the screen/p* documents, the lists from watchlist/*, and what the slim rows leave out (profit,
    source, top holder) from the restored fundamentals. Without it a weekday run has no screen to re-price and no
    companies to run the no-brainer rules on."""
    from .value import top_corporate_holder
    rows: dict[str, dict] = {}
    for p in collection_files(db_dir, "screen"):
        for r in _read(p).get("rows") or []:
            rows[r["code"]] = dict(r)
    if not rows:
        return None
    lists, tags = {}, {}
    meta = None
    for p in collection_files(db_dir, "watchlist"):
        doc = _read(p)
        if p.stem == "meta":
            meta = doc
            continue
        lists[p.stem] = doc
        for r in doc.get("rows") or []:
            tags.setdefault(r["code"], []).append(p.stem)
            if r["code"] in rows:
                for k, v in r.items():
                    rows[r["code"]].setdefault(k, v)
    for code, r in rows.items():
        if r.get("src") and not r.get("source"):
            r["source"] = r["src"]
        if r.get("ni") is not None and r.get("source"):
            continue
        f = store.fundamentals(code)
        if f is None:
            continue
        r.setdefault("ni", f.net_income)
        r.setdefault("op", f.op_income)
        r.setdefault("source", f.source or "restored")
        if "top_holder" not in r:
            r["top_holder"], pct = top_corporate_holder(f)
            r.setdefault("top_holder_pct", pct)
    lists["_tags"] = tags
    return dict(meta=meta or {}, lists=lists, rows_all=list(rows.values()))


def plan_batches(out_db: str | Path, db_dir: Optional[str | Path], versions: dict[str, int],
                 deletes: Optional[list[str]] = None) -> list[list[dict]]:
    """Writes for every exported document that differs from the downloaded copy, pinned to its version."""
    writes = []
    for p in sorted(Path(out_db).glob("*.json")):
        coll, doc_id = p.stem.split("__", 1)
        new = json.loads(p.read_text())
        old_p = Path(db_dir) / coll / f"{doc_id}.json" if db_dir else None
        if old_p and old_p.exists():
            try:
                if _read(old_p) == new:
                    continue          # unchanged: no write
            except ValueError:
                pass
        w = dict(op="set", collection=coll, doc_id=doc_id, file_path=str(p.resolve()))
        v = versions.get(f"{coll}/{doc_id}")
        if v:
            w["if_version"] = int(v)
        writes.append((w, p.stat().st_size))
    for key in deletes or []:
        coll, doc_id = key.split("/", 1)
        v = versions.get(key)
        if v:
            writes.append((dict(op="delete", collection=coll, doc_id=doc_id, if_version=int(v)), 0))
    batches, cur, size = [], [], 0
    for w, b in writes:
        if cur and (len(cur) >= BATCH_DOCS or size + b > BATCH_BYTES):
            batches.append(cur)
            cur, size = [], 0
        cur.append(w)
        size += b
    if cur:
        batches.append(cur)
    return batches
