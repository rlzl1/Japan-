"""EDINET API v2 client: filing lists, XBRL packages, large-shareholding reports.

Needs a free subscription key (https://api.edinet-fsa.go.jp/ → アカウント登録) in EDINET_API_KEY.
Daily lists for past dates are cached forever; packages are immutable and cached too.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass
from typing import Iterable, Optional

from .http import Http, HttpError
from .util import nfkc, norm_code
from .xbrl import FactSet, parse_zip

log = logging.getLogger(__name__)

API = "https://api.edinet-fsa.go.jp/api/v2"
VIEW = "https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{doc}"

ANNUAL, AMENDED_ANNUAL, QUARTERLY, SEMIANNUAL = "120", "130", "140", "160"
EXTRAORDINARY, TOB_FILING, TOB_REPORT, OPINION = "180", "240", "270", "290"
LARGE_HOLDING, LARGE_HOLDING_AMEND = "350", "360"


@dataclass
class Filing:
    doc_id: str
    edinet_code: str
    sec_code: str
    filer: str
    doc_type: str
    description: str
    submitted: dt.datetime
    period_end: Optional[str]
    issuer_edinet_code: Optional[str]
    subject_edinet_code: Optional[str]
    xbrl: bool

    @property
    def url(self) -> str:
        return VIEW.format(doc=self.doc_id)


class Edinet:
    def __init__(self, http: Http, key: Optional[str] = None):
        self.http = http
        self.key = key or os.environ.get("EDINET_API_KEY")

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def _params(self, **kw) -> dict:
        kw["Subscription-Key"] = self.key
        return kw

    def documents(self, day: dt.date) -> list[Filing]:
        if not self.key:
            return []
        recent = (dt.date.today() - day).days <= 3
        params = self._params(date=day.isoformat(), type=2)
        js = self.http.get_json(f"{API}/documents.json", params=params, ttl=1800 if recent else None)
        if not js or str(js.get("metadata", {}).get("status")) != "200":
            self.http.invalidate(f"{API}/documents.json", params)  # never cache an error body
            if js and str(js.get("StatusCode")) == "401":
                raise PermissionError("EDINET rejected the API key (401)")
            return []
        out = []
        for r in js.get("results") or []:
            if r.get("withdrawalStatus") not in (None, "0") or r.get("disclosureStatus") not in (None, "0"):
                continue
            try:
                ts = dt.datetime.strptime(r["submitDateTime"], "%Y-%m-%d %H:%M")
            except (KeyError, TypeError, ValueError):
                ts = dt.datetime.combine(day, dt.time())
            out.append(Filing(
                doc_id=r["docID"], edinet_code=r.get("edinetCode") or "", sec_code=norm_code(r.get("secCode")),
                filer=nfkc(r.get("filerName") or ""), doc_type=str(r.get("docTypeCode") or ""),
                description=nfkc(r.get("docDescription") or ""), submitted=ts, period_end=r.get("periodEnd"),
                issuer_edinet_code=r.get("issuerEdinetCode"), subject_edinet_code=r.get("subjectEdinetCode"),
                xbrl=r.get("xbrlFlag") == "1"))
        return out

    def documents_range(self, start: dt.date, end: dt.date, types: Optional[set[str]] = None) -> Iterable[Filing]:
        d = end
        while d >= start:
            try:
                for f in self.documents(d):
                    if types is None or f.doc_type in types:
                        yield f
            except HttpError as e:
                log.warning("EDINET list %s: %s", d, e)
            d -= dt.timedelta(days=1)

    def package(self, doc_id: str) -> Optional[bytes]:
        try:
            data = self.http.get(f"{API}/documents/{doc_id}", params=self._params(type=1))
        except HttpError as e:
            log.info("EDINET package %s: %s", doc_id, e)
            return None
        return data if data and data.startswith(b"PK") else None

    def facts(self, doc_id: str) -> Optional[FactSet]:
        data = self.package(doc_id)
        if not data:
            return None
        try:
            return parse_zip(data, prefer="instance")
        except Exception as e:
            log.info("EDINET parse %s: %s", doc_id, e)
            return None

    def latest_reports(self, start: dt.date, end: dt.date) -> dict[str, dict[str, Filing]]:
        """{edinet_code: {'annual': Filing, 'interim': Filing}} - newest of each kind in the window."""
        out: dict[str, dict[str, Filing]] = {}
        for f in self.documents_range(start, end, {ANNUAL, AMENDED_ANNUAL, SEMIANNUAL, QUARTERLY}):
            if not f.xbrl or not f.edinet_code:
                continue
            kind = "annual" if f.doc_type in (ANNUAL, AMENDED_ANNUAL) else "interim"
            cur = out.setdefault(f.edinet_code, {}).get(kind)
            if cur is None or f.submitted > cur.submitted:
                out[f.edinet_code][kind] = f
        return out


def holding_from_facts(fs: FactSet) -> dict:
    """Stake and purpose from a 大量保有報告書 / 変更報告書 instance (jplvh taxonomy)."""
    out: dict = {}
    for x in fs.facts:
        if out.get("pct") is None and "HoldingRatioOfShareCertificatesEtc" in x.local and x.value is not None:
            if "Prior" not in x.context and "Joint" not in x.context:
                out["pct"] = x.value * 100 if x.value <= 1 else x.value
        elif "PurposeOfHolding" in x.local and x.text and not out.get("purpose"):
            out["purpose"] = nfkc(x.text)[:80]
        elif x.local in ("NameOfIssuer", "IssuerName") and x.text and not out.get("issuer"):
            out["issuer"] = nfkc(x.text)
        elif "SecurityCodeOfIssuer" in x.local and x.text and not out.get("issuer_code"):
            out["issuer_code"] = norm_code(x.text)
    for x in fs.facts:
        if "HoldingRatioOfShareCertificatesEtcPerLastReport" in x.local and x.value is not None:
            out["pct_before"] = x.value * 100 if x.value <= 1 else x.value
            break
    return out
