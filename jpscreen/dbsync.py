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
from pathlib import Path
from typing import Optional

from .store import Store

CHUNK_BYTES = 200_000
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


def store_chunks(store: Store) -> dict[str, dict]:
    """The fundamentals store as documents under the database's 256 KiB cap."""
    recs = store._load_fund()
    chunks, cur, size, idx = {}, {}, 0, 0
    for code in sorted(recs):
        rec = {k: {kk: vv for kk, vv in (v or {}).items() if kk not in SLIM_DROP and vv not in (None, [], "")}
               for k, v in recs[code].items() if v}
        b = len(json.dumps(rec, ensure_ascii=False, separators=(",", ":")).encode())
        if cur and size + b > CHUNK_BYTES:
            chunks[f"fund_{idx:02d}"] = dict(recs=cur)
            cur, size, idx = {}, 0, idx + 1
        cur[code] = rec
        size += b
    if cur:
        chunks[f"fund_{idx:02d}"] = dict(recs=cur)
    return chunks


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
