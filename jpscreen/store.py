"""Local state that survives between runs: fundamentals per company, parsed TDnet documents, run outputs.

TDnet deletes documents after ~31 days and EDINET packages are large, so whatever was read once is kept.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

from .fundamentals import Fundamentals


def _fund(d: dict) -> Fundamentals:
    return Fundamentals(**{k: v for k, v in d.items() if k in Fundamentals.__dataclass_fields__})


class Store:
    def __init__(self, root: str | Path = "state"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._fund: Optional[dict] = None
        self._docs: Optional[dict] = None

    # ---------------------------------------------------------------- fundamentals
    @property
    def fund_path(self) -> Path:
        return self.root / "fundamentals.json"

    def _load_fund(self) -> dict:
        if self._fund is None:
            self._fund = json.loads(self.fund_path.read_text()) if self.fund_path.exists() else {}
        return self._fund

    def fundamentals(self, code: str) -> Optional[Fundamentals]:
        rec = self._load_fund().get(code)
        if not rec:
            return None
        base = rec.get("annual") or rec.get("latest")
        if not base:
            return None
        f = _fund(base)
        latest = rec.get("latest")
        if rec.get("annual") and latest and latest.get("filed", "") > rec["annual"].get("filed", ""):
            f.merge_newer_bs(_fund(latest))
            if latest.get("pl_annual"):
                for k in ("revenue", "op_income", "ordinary_income", "net_income"):
                    if latest.get(k) is not None:
                        setattr(f, k, latest[k])
        elif not rec.get("annual") and not (latest or {}).get("pl_annual"):
            f.revenue = f.op_income = f.ordinary_income = f.net_income = None  # interim P&L is not a fiscal year
        return f

    def put_fundamentals(self, code: str, f: Fundamentals, filed: dt.date | str, annual: bool,
                         pl_annual: bool = False) -> None:
        """annual=True for an EDINET annual report (keeps the notes); earnings releases go to 'latest'."""
        recs = self._load_fund()
        rec = recs.setdefault(code, {})
        d = f.to_dict()
        d["filed"] = filed.isoformat() if isinstance(filed, dt.date) else str(filed)
        d["pl_annual"] = pl_annual or annual
        slot = "annual" if annual else "latest"
        cur = rec.get(slot)
        if cur is None or d["filed"] >= cur.get("filed", ""):
            rec[slot] = d
        if annual and (rec.get("latest") is None or d["filed"] >= rec["latest"].get("filed", "")):
            rec["latest"] = d

    def fundamentals_codes(self) -> list[str]:
        return list(self._load_fund().keys())

    def save(self) -> None:
        if self._fund is not None:
            self.fund_path.write_text(json.dumps(self._fund, ensure_ascii=False))
        if self._docs is not None:
            self.docs_path.write_text(json.dumps(self._docs, ensure_ascii=False))

    # ---------------------------------------------------------------- parsed documents
    @property
    def docs_path(self) -> Path:
        return self.root / "parsed_docs.json"

    def _load_docs(self) -> dict:
        if self._docs is None:
            self._docs = json.loads(self.docs_path.read_text()) if self.docs_path.exists() else {}
        return self._docs

    def parsed(self, doc_id: str) -> Optional[dict]:
        return self._load_docs().get(doc_id)

    def put_parsed(self, doc_id: str, parsed: dict) -> None:
        self._load_docs()[doc_id] = parsed

    # ---------------------------------------------------------------- outputs
    def write_json(self, rel: str, obj) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str))
        return p

    def read_json(self, rel: str, default=None):
        p = self.root / rel
        return json.loads(p.read_text()) if p.exists() else default
