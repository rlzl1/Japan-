"""The investable universe: every listed Japanese company, with market segment and EDINET code.

Sources (no key needed):
  - JPX 'data_j.xlsx' (TSE Prime/Standard/Growth, sector, size)
  - EDINET code list (all listed filers incl. Nagoya/Fukuoka/Sapporo-only listings, fiscal year end)
"""
from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .http import Http
from .util import norm_code, nfkc

log = logging.getLogger(__name__)

JPX_LIST = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
EDINET_CODES = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"

MARKET_SHORT = {
    "プライム（内国株式）": "東P",
    "スタンダード（内国株式）": "東S",
    "グロース（内国株式）": "東G",
}
EXCLUDED_SEGMENTS = ("ETF", "REIT", "PRO Market", "出資証券", "外国株式")
FINANCIAL_SECTORS = ("銀行業", "証券、商品先物取引業", "保険業", "その他金融業")


@dataclass
class Company:
    code: str
    name: str
    market: str = "地方"          # 東P / 東S / 東G / 地方 (regional-only listing)
    sector: str = ""
    size: str = ""
    edinet_code: str = ""
    fiscal_year_end: str = ""
    consolidated: Optional[bool] = None
    name_en: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def financial(self) -> bool:
        return self.sector in FINANCIAL_SECTORS

    @property
    def yahoo_symbols(self) -> list[str]:
        if self.market.startswith("東"):
            return [f"{self.code}.T"]
        return [f"{self.code}.N", f"{self.code}.F", f"{self.code}.S", f"{self.code}.T"]


class Universe:
    def __init__(self, companies: dict[str, Company]):
        self.companies = companies
        self.by_edinet = {c.edinet_code: c for c in companies.values() if c.edinet_code}

    def __contains__(self, code: str) -> bool:
        return norm_code(code) in self.companies

    def get(self, code: str) -> Optional[Company]:
        return self.companies.get(norm_code(code))

    def __len__(self) -> int:
        return len(self.companies)

    def __iter__(self):
        return iter(self.companies.values())

    @classmethod
    def load(cls, http: Http, ttl: float = 7 * 86400) -> "Universe":
        companies: dict[str, Company] = {}
        try:
            data = http.get(JPX_LIST, ttl=ttl)
            df = pd.read_excel(io.BytesIO(data), dtype=str)
            for _, r in df.iterrows():
                seg = str(r.get("市場・商品区分") or "")
                if any(x in seg for x in EXCLUDED_SEGMENTS) or seg not in MARKET_SHORT:
                    continue
                code = norm_code(r["コード"])
                companies[code] = Company(code=code, name=nfkc(r["銘柄名"]).strip(),
                                          market=MARKET_SHORT[seg], sector=str(r.get("33業種区分") or ""),
                                          size=str(r.get("規模区分") or ""))
        except Exception as e:  # keep going on the EDINET list alone
            log.warning("JPX list unavailable: %s", e)

        try:
            data = http.get(EDINET_CODES, ttl=ttl)
            zf = zipfile.ZipFile(io.BytesIO(data))
            name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            ec = pd.read_csv(io.BytesIO(zf.read(name)), encoding="cp932", skiprows=1, dtype=str)
            ec.columns = [nfkc(c) for c in ec.columns]
            for _, r in ec.iterrows():
                if str(r.get("上場区分")) != "上場":
                    continue
                code = norm_code(r.get("証券コード"))
                if not code:
                    continue
                c = companies.get(code)
                if c is None:
                    # listed only on a regional exchange (or JPX list missing): keep as 地方
                    filer_type = str(r.get("提出者種別") or "")
                    if "内国" not in filer_type or "投資法人" in str(r.get("提出者名") or ""):
                        continue
                    c = Company(code=code, name=nfkc(r.get("提出者名")).replace("株式会社", "").strip(),
                                market="地方", sector=str(r.get("提出者業種") or ""))
                    companies[code] = c
                cell = lambda k: "" if pd.isna(r.get(k)) else str(r.get(k)).strip()  # noqa: E731  (blank cells are NaN)
                c.edinet_code = cell("EDINETコード")
                c.fiscal_year_end = cell("決算日")
                c.consolidated = cell("連結の有無") == "有"
                c.name_en = cell("提出者名(英字)")
        except Exception as e:
            log.warning("EDINET code list unavailable: %s", e)
        return cls(companies)
