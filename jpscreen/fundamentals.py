"""Turn XBRL facts (EDINET 有価証券報告書 / 半期報告書, TDnet 決算短信) into the numbers the screen uses.

Includes the two notes that make Japanese net-nets special:
  - 賃貸等不動産 (rental real estate) fair value, parsed from the note's HTML table;
  - 大株主の状況 (major shareholders), for parent / founder / activist / cross-holder detection.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from lxml import html as lhtml

from .util import nfkc, parse_jp_number
from .xbrl import CURRENT_DURATION, CURRENT_INSTANT, FactSet, split_context


@dataclass
class Fundamentals:
    code: str = ""
    source: str = ""               # 'edinet:<docID>' / 'tdnet:<id>' / 'reference'
    period_end: Optional[str] = None
    consolidated: Optional[bool] = None
    cash: Optional[float] = None
    receivables: Optional[float] = None
    securities: Optional[float] = None
    investment_securities: Optional[float] = None
    current_assets: Optional[float] = None
    inventories: Optional[float] = None
    total_assets: Optional[float] = None
    liabilities: Optional[float] = None
    net_assets: Optional[float] = None
    equity: Optional[float] = None          # attributable to owners (net assets - NCI - warrants)
    nci: Optional[float] = None
    land: Optional[float] = None
    interest_bearing_debt: Optional[float] = None
    revenue: Optional[float] = None
    op_income: Optional[float] = None
    ordinary_income: Optional[float] = None
    net_income: Optional[float] = None
    net_income_history: list = field(default_factory=list)   # newest first
    op_income_history: list = field(default_factory=list)
    fcf_history: list = field(default_factory=list)          # newest first: OCF - capex (or OCF + investing CF)
    dividends_paid: Optional[float] = None                  # cash dividends paid last FY (CF statement), yen
    shares_issued: Optional[float] = None
    treasury_shares: Optional[float] = None
    rental_fair_value: Optional[float] = None
    rental_book_value: Optional[float] = None
    rental_note: str = ""                   # 'table' / 'text' / 'immaterial' / 'absent'
    major_holders: list = field(default_factory=list)       # [{name, pct}]
    officers_shares: Optional[float] = None
    ceo_age: Optional[int] = None
    going_concern: bool = False
    fiscal_year_end: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def merge_newer_bs(self, other: "Fundamentals") -> None:
        """Take the balance sheet of a more recent interim/quarterly report, keep annual-only notes."""
        for k in ("period_end", "cash", "receivables", "securities", "investment_securities", "current_assets",
                  "inventories", "total_assets", "liabilities", "net_assets", "equity", "nci", "land",
                  "interest_bearing_debt"):
            v = getattr(other, k)
            if v is not None:
                setattr(self, k, v)
        if other.shares_issued:
            self.shares_issued = other.shares_issued
        if other.treasury_shares is not None:
            self.treasury_shares = other.treasury_shares
        self.source = f"{self.source}+{other.source}"


# ---------------------------------------------------------------- balance-sheet element sets

CASH = ["CashAndDeposits", "CashAndCashEquivalents", "CashAndDepositsAssets", "CashAndCashEquivalentsIFRS"]
SECURITIES = ["ShortTermInvestmentSecurities", "Securities", "SecuritiesCA", "OtherFinancialAssetsCAIFRS"]
INV_SECURITIES = ["InvestmentSecurities", "OtherFinancialAssetsNCAIFRS"]
RECEIVABLE_COMBINED = re.compile(r"^(NotesAndAccountsReceivableTrade|NotesAndAccountsReceivableTradeAndContractAssets|"
                                 r"AccountsReceivableTradeAndContractAssets|TradeAndOtherReceivablesCAIFRS|"
                                 r"NotesReceivableAccountsReceivableFromCompletedConstructionContracts\w*)$")
RECEIVABLE_PARTS = re.compile(r"^(NotesReceivableTrade|AccountsReceivableTrade|ContractAssets|"
                              r"ElectronicallyRecordedMonetaryClaimsOperatingCA|AccountsReceivableFromCompletedConstructionContracts\w*|"
                              r"AccountsReceivableTradeAndContractAssetsCNS|NotesReceivableTradeCNS)$")
ELECTRONIC = re.compile(r"^ElectronicallyRecordedMonetaryClaimsOperatingCA$")
INVENTORY = re.compile(r"^(Inventories|MerchandiseAndFinishedGoods|Merchandise|FinishedGoods|WorkInProcess|"
                       r"RawMaterialsAndSupplies|CostsOnUncompletedConstructionContracts\w*|RealEstateForSale\w*|"
                       r"InventoriesIFRS)$")
DEBT = re.compile(r"^(ShortTermLoansPayable|CurrentPortionOfLongTermLoansPayable|LongTermLoansPayable|BondsPayable|"
                  r"CurrentPortionOfBonds|CommercialPapersLiabilities|ShortTermBorrowings\w*|LongTermBorrowings\w*|"
                  r"BondsAndBorrowings\w*)$")


def _is_cons(ctx: str) -> bool:
    return "NonConsolidatedMember" not in ctx


def _instant_pred(consolidated: bool, period: Optional[str] = None):
    def pred(ctx: str) -> bool:
        base, members = split_context(ctx)
        if not CURRENT_INSTANT.match(base):
            return False
        if period and base != period:
            return False
        other = [m for m in members if m not in ("NonConsolidatedMember", "ConsolidatedMember", "ResultMember")]
        if other:
            return False
        return _is_cons(ctx) if consolidated else ("NonConsolidatedMember" in ctx)
    return pred


def _duration_pred(consolidated: bool, base_re=CURRENT_DURATION):
    def pred(ctx: str) -> bool:
        base, members = split_context(ctx)
        if not base_re.match(base):
            return False
        other = [m for m in members if m not in ("NonConsolidatedMember", "ConsolidatedMember", "ResultMember")]
        if other:
            return False
        return _is_cons(ctx) if consolidated else ("NonConsolidatedMember" in ctx)
    return pred


def _first(fs: FactSet, names: list[str], pred) -> Optional[float]:
    for n in names:
        v = fs.num(n, pred)
        if v is not None:
            return v
    return None


def _sum_matching(fs: FactSet, rx: re.Pattern, pred) -> Optional[float]:
    total, seen = 0.0, set()
    found = False
    for f in fs.facts:
        if f.value is None or f.local in seen or not rx.match(f.local) or not pred(f.context):
            continue
        seen.add(f.local)
        total += f.value
        found = True
    return total if found else None


def balance_sheet(fs: FactSet, code: str = "", source: str = "") -> Fundamentals:
    f = Fundamentals(code=code, source=source)
    # consolidated if any consolidated BS total exists
    cons = fs.num("Assets", _instant_pred(True)) is not None or fs.num("AssetsIFRS", _instant_pred(True)) is not None
    f.consolidated = cons
    pred = _instant_pred(cons)
    # pick the latest current instant present (an interim report has InterimInstant)
    f.cash = _first(fs, CASH, pred)
    f.securities = _first(fs, SECURITIES, pred)
    f.investment_securities = _first(fs, INV_SECURITIES, pred)
    comb = _sum_matching(fs, RECEIVABLE_COMBINED, pred)
    if comb is not None:
        f.receivables = comb + (_sum_matching(fs, ELECTRONIC, pred) or 0)
    else:
        f.receivables = _sum_matching(fs, RECEIVABLE_PARTS, pred)
    allowance = fs.num("AllowanceForDoubtfulAccountsCA", pred)
    if f.receivables is not None and allowance is not None:
        f.receivables += allowance if allowance < 0 else -allowance
    f.current_assets = _first(fs, ["CurrentAssets", "CurrentAssetsIFRS"], pred)
    f.inventories = _sum_matching(fs, INVENTORY, pred)
    f.total_assets = _first(fs, ["Assets", "AssetsIFRS"], pred)
    f.liabilities = _first(fs, ["Liabilities", "LiabilitiesIFRS"], pred)
    f.net_assets = _first(fs, ["NetAssets", "EquityIFRS"], pred)
    f.nci = _first(fs, ["NonControllingInterests", "NonControllingInterestsIFRS"], pred) or 0.0
    warrants = _first(fs, ["SubscriptionRightsToShares"], pred) or 0.0
    owners = _first(fs, ["EquityAttributableToOwnersOfParentIFRS"], pred)
    if owners is not None:
        f.equity = owners
    elif f.net_assets is not None:
        f.equity = f.net_assets - f.nci - warrants
    f.land = _first(fs, ["Land", "LandIFRS"], pred)
    f.interest_bearing_debt = _sum_matching(fs, DEBT, pred)
    # period end: the date part is not in the context id; record which context family we used
    for x in fs.facts:
        if x.local in ("CurrentAssets", "Assets", "CurrentAssetsIFRS", "AssetsIFRS") and pred(x.context):
            f.period_end = split_context(x.context)[0]
            break
    return f


def income(fs: FactSet, f: Fundamentals) -> None:
    for cons in (True, False):
        pred = _duration_pred(cons)
        rev = _first(fs, ["NetSales", "OperatingRevenue1", "OperatingRevenue2", "Revenue", "RevenueIFRS",
                          "NetSalesOfCompletedConstructionContractsCNS", "NetSalesSummaryOfBusinessResults"], pred)
        op = _first(fs, ["OperatingIncome", "OperatingProfitLossIFRS", "OperatingIncomeLoss"], pred)
        if op is None:
            loss = fs.num("OperatingLoss", pred)
            op = -abs(loss) if loss is not None else None
        ni = _first(fs, ["ProfitLossAttributableToOwnersOfParent", "ProfitLossAttributableToOwnersOfParentIFRS",
                         "ProfitAttributableToOwnersOfParent", "NetIncome", "ProfitLoss", "NetIncomeLoss"], pred)
        if rev is not None or op is not None or ni is not None:
            f.revenue, f.op_income, f.net_income = rev, op, ni
            f.ordinary_income = _first(fs, ["OrdinaryIncome", "OrdinaryIncomeLoss"], pred)
            break
    # five-year history from 主要な経営指標等の推移
    hist_ni, hist_op = [], []
    for i, base in enumerate(["CurrentYearDuration", "Prior1YearDuration", "Prior2YearDuration", "Prior3YearDuration",
                              "Prior4YearDuration"]):
        def p(ctx, base=base, cons=f.consolidated):
            b, m = split_context(ctx)
            return b == base and ((not m) if cons else (m == ["NonConsolidatedMember"]))
        ni = _first(fs, ["ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults",
                         "NetIncomeLossSummaryOfBusinessResults",
                         "ProfitLossAttributableToOwnersOfParentIFRSSummaryOfBusinessResults"], p)
        if ni is None and f.consolidated:
            ni = _first(fs, ["NetIncomeLossSummaryOfBusinessResults"],
                        lambda c, base=base: split_context(c)[0] == base and "NonConsolidatedMember" in c)
        hist_ni.append(ni)
        hist_op.append(_first(fs, ["OrdinaryIncomeLossSummaryOfBusinessResults"], p))
    if any(v is not None for v in hist_ni):
        f.net_income_history = hist_ni
        if f.net_income is None:
            f.net_income = hist_ni[0]
    if any(v is not None for v in hist_op):
        f.op_income_history = hist_op


def cash_flows(fs: FactSet, f: Fundamentals) -> None:
    """FCF per year, newest first: OCF − capex from the statements (2 years), OCF + investing CF from the summary."""
    cons = f.consolidated if f.consolidated is not None else True
    out = []
    for base in ("CurrentYearDuration", "Prior1YearDuration"):
        def p(ctx, base=base):
            b_, m = split_context(ctx)
            return b_ == base and ((not m) if cons else (m == ["NonConsolidatedMember"]))
        ocf = _first(fs, ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesIFRS"], p)
        capex = None
        for n in ("PurchaseOfPropertyPlantAndEquipment", "PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssets",
                  "PurchaseOfPropertyPlantAndEquipmentInvCF", "PurchaseOfPropertyPlantAndEquipmentIFRS"):
            v = fs.num(n, p)
            if v is not None:
                capex = (capex or 0) + abs(v)
        intang = fs.num("PurchaseOfIntangibleAssets", p) or fs.num("PurchaseOfIntangibleAssetsInvCF", p)
        if capex is not None and intang is not None:
            capex += abs(intang)
        if ocf is not None and capex is not None:
            out.append(ocf - capex)
        if base == "CurrentYearDuration":
            div = _first(fs, ["CashDividendsPaidFinCF", "CashDividendsPaid", "DividendsPaidFinCF", "DividendsPaidIFRS"], p)
            if div is not None:
                f.dividends_paid = abs(div)
    if len(out) < 3:
        summ = []
        for base in ("CurrentYearDuration", "Prior1YearDuration", "Prior2YearDuration", "Prior3YearDuration"):
            def p(ctx, base=base):
                b_, m = split_context(ctx)
                return b_ == base and ((not m) if cons else (m == ["NonConsolidatedMember"]))
            o = next((x.value for x in fs.matching(r"OperatingActivities\w*SummaryOfBusinessResults") if x.value is not None and p(x.context)), None)
            i = next((x.value for x in fs.matching(r"InvestingActivities\w*SummaryOfBusinessResults") if x.value is not None and p(x.context)), None)
            summ.append(o + i if o is not None and i is not None else None)
        for v in summ[len(out):]:
            if v is not None:
                out.append(v)
    f.fcf_history = out[:5]


def shares(fs: FactSet, f: Fundamentals) -> None:
    issued = None
    for x in fs.matching(r"(TotalNumberOfIssuedShares|NumberOfIssuedSharesAsOfFiscalYearEnd|"
                         r"NumberOfIssuedAndOutstandingSharesAtTheEndOfFiscalYearIncludingTreasuryStock|"
                         r"NumberOfIssuedSharesAsOfFilingDate)"):
        if x.value and (CURRENT_INSTANT.match(split_context(x.context)[0]) or x.context.startswith("FilingDate")):
            issued = x.value
            break
    treasury = None
    for x in fs.matching(r"(TotalNumberOfSharesHeldTreasurySharesEtc|NumberOfTreasuryStockAtTheEndOfFiscalYear|"
                         r"NumberOfSharesHeldInOwnNameTreasurySharesEtc)"):
        if x.value is not None and CURRENT_INSTANT.match(split_context(x.context)[0]):
            treasury = x.value
            break
    f.shares_issued, f.treasury_shares = issued, treasury


# ---------------------------------------------------------------- notes

UNIT_RE = re.compile(r"単位\s*[:：]?\s*(百万円|千円|円)")
UNIT_MULT = {"百万円": 1e6, "千円": 1e3, "円": 1.0}
FV_ROW = re.compile(r"時価|公正価値")
NOT_FV = re.compile(r"簿価|増減|期首|算定|方法|鑑定|差額")


def _tables(html: str) -> tuple[list[list[list[str]]], str]:
    try:
        root = lhtml.fromstring(f"<div>{html}</div>")
    except Exception:
        return [], nfkc(re.sub(r"<[^>]+>", " ", html))
    tables = []
    for t in root.iter("table"):
        rows = []
        for tr in t.iter("tr"):
            cells = [nfkc(" ".join(td.itertext())).strip() for td in tr if td.tag in ("td", "th")]
            if any(cells):
                rows.append(cells)
        tables.append(rows)
    return tables, nfkc(" ".join(root.itertext()))


def _last_number(cells: list[str]) -> Optional[float]:
    for c in reversed(cells):
        if re.search(r"\d", c) and not re.search(r"\d{4}年|\d+月|\d+日|第\d+期", c):
            v = parse_jp_number(c)
            if v is not None:
                return v
    return None


YEAR_HEAD = re.compile(r"(前|当)(連結)?(会計|事業)?年度(?:\s|　|&nbsp;|<[^>]+>)*[（(]\s*(?:自|\d{4}年)")
NUM_CELL = re.compile(r"[△▲\-−]?\s*[\d,]+(\.\d+)?\s*(百万円|千円|円)?")
BOOK_ROW = re.compile(r"期末残高|期末帳簿価額|帳簿価額|貸借対照表計上額")
SUM_ROW = re.compile(r"^(合計|計|総計)$")
CLASS_MARK = re.compile(r"[（(][1-9１-９][）)]|賃貸等不動産として使用される部分を含む不動産|それ以外の不動産")


def _current_year(html: str) -> str:
    """Notes that print last year's table and this year's one after the other: keep this year's part."""
    spans = [m.span() for m in re.finditer(r"<table\b.*?</table>", html, re.S | re.I)]
    heads = [m for m in YEAR_HEAD.finditer(html) if not any(a <= m.start() < b for a, b in spans)]
    cur = [m for m in heads if m.group(1) == "当"]
    return html[cur[-1].start():] if cur else html


def _rows(table_html: str) -> list[list[str]]:
    tables, _ = _tables(table_html)
    return tables[0] if tables else []


def _cell_number(c: str) -> Optional[float]:
    """A cell that holds just an amount ("1,171", "△71", "1,243百万円", "7,683 〃", "※2 9,801"), else None."""
    t = re.sub(r"(百万円|千円|円|〃|″|”|\"|\s)+$", "", c.strip())
    t = re.sub(r"^[※＊*]\s*\d?\s+", "", t)
    return parse_jp_number(t) if NUM_CELL.fullmatch(t) and re.search(r"\d", t) else None


def _split_row(r: list[str]) -> tuple[str, list[float]]:
    label, nums = [], []
    for c in r:
        v = _cell_number(c)
        if v is not None:
            nums.append(v)
        elif c.strip() not in ("", "－", "-", "―"):
            label.append(c)
    return " ".join(label), nums


def _table_values(rows: list[list[str]]) -> tuple[Optional[float], Optional[float]]:
    """(fair value, book value) of one table, in the table's unit, for the latest period."""
    split = [_split_row(r) for r in rows]
    # vertical: one row per measure (期末残高, 期末時価), one column per year (the last is the latest)
    fv = book = None
    for label, nums in split:
        if not nums or re.search(r"前(連結)?(会計|事業)?年度|前期末(?!残高)", label):
            continue
        if FV_ROW.search(label) and not NOT_FV.search(label):
            fv = (fv or 0) + nums[-1]
        elif BOOK_ROW.search(label) and not re.search(r"期首|前期末|期中|増減", label):
            book = (book or 0) + nums[-1]
    if fv is not None:
        return fv, book
    # horizontal: header cells name the measures (… 期末残高 | 時価), one row per class of property
    head = " ".join(lb for lb, nums in split if not nums)
    if not FV_ROW.search(head):
        return None, None
    data = [(lb, nums) for lb, nums in split if len(nums) >= 2 and not FV_ROW.search(lb)]
    if any(re.search(r"期首|期中|増減|期末残高", lb) for lb, _ in data):
        return None, None          # a measures-as-rows table whose value row could not be read
    total = [d for d in data if SUM_ROW.match(d[0].strip())]
    data = total[-1:] or data
    if not data:
        return None, None
    return sum(n[-1] for _, n in data), sum(n[-2] for _, n in data)


def parse_rental_note(html: str) -> dict:
    """Fair value and book value of 賃貸等不動産 (or IFRS 投資不動産) from the note's HTML, in yen.

    Handles one table or one per class of property ((1) partly used by the company, (2) the rest), measures as rows
    or as columns, footnote markers in labels, and notes that repeat the table for the previous year."""
    _, text = _tables(html)
    m = UNIT_RE.search(text)
    unit = m.group(1) if m else ("千円" if "千円" in text and "百万円" not in text else "百万円")
    if re.search(r"重要性が(乏しい|ない)|記載を省略|注記を省略|該当事項はありません|該当事項なし", text) and not FV_ROW.search(
            " ".join(" ".join(r) for t in _tables(html)[0] for r in t)):
        return {"fair_value": 0.0, "book_value": 0.0, "method": "immaterial", "unit": unit}
    part = _current_year(html)
    found, prev_end = [], 0
    for tm in re.finditer(r"<table\b.*?</table>", part, re.S | re.I):
        rows = _rows(tm.group(0))
        fv, book = _table_values(rows)
        if fv is None:
            continue
        before = nfkc(re.sub(r"<[^>]+>", " ", part[prev_end:tm.start()]))    # text since the previous value table
        prev_end = tm.end()
        um = UNIT_RE.findall(before) or UNIT_RE.findall(" ".join(" ".join(r) for r in rows))
        t_unit = um[-1] if um else ("千円" if re.search(r"[（(]千円[）)]", " ".join(" ".join(r) for r in rows)) else unit)
        mult = UNIT_MULT[t_unit]
        found.append(dict(fv=fv * mult, book=book * mult if book is not None else None, cls=bool(CLASS_MARK.search(before))))
    if found:
        use = found if len(found) > 1 and sum(f["cls"] for f in found) >= len(found) - 1 else found[-1:]
        books = [f["book"] for f in use]
        return {"fair_value": sum(f["fv"] for f in use),
                "book_value": sum(books) if all(b is not None for b in books) else None,
                "method": "table" if len(use) == 1 else f"tables:{len(use)}", "unit": unit}
    m = re.search(r"(?:期末|当連結会計年度末|当事業年度末)?(?:の)?時価(?:は|:)?\s*([\d,]+)\s*(百万円|千円)", text)
    if m:
        return {"fair_value": parse_jp_number(m.group(1)) * UNIT_MULT[m.group(2)], "book_value": None,
                "method": "text", "unit": m.group(2)}
    return {"fair_value": None, "book_value": None, "method": "unparsed", "unit": unit}


def rental_property(fs: FactSet, f: Fundamentals) -> None:
    blocks = [b for b in fs.text_blocks()
              if re.search(r"RealEstateForLease|RentalPropert|InvestmentAndRentalPropert|InvestmentProperty", b.local)
              or ("賃貸等不動産" in (b.text or "")[:400] and "TextBlock" in b.local)]
    blocks = [b for b in blocks if "NonConsolidated" not in b.local] or blocks
    if not blocks:
        f.rental_note = "absent"
        return
    best = None
    for b in blocks:
        r = parse_rental_note(b.text or "")
        if r["fair_value"] is not None and (best is None or r["method"].startswith("table")):
            best = r
    if best:
        f.rental_fair_value, f.rental_book_value, f.rental_note = best["fair_value"], best["book_value"], best["method"]
    else:
        f.rental_note = "unparsed"


TRUST_BANKS = re.compile(r"日本マスタートラスト|日本カストディ|資産管理サービス|日本トラスティ|信託口|CUSTODY|"
                         r"STATE STREET|ステート・?ストリート|BNY|MELLON|メロン|JP ?MORGAN|ジェーピー ?モルガン|"
                         r"GOLDMAN|ゴールドマン|MORGAN STANLEY|モルガン・?スタンレー|NOMURA|野村證券|SSBTC|NORTHERN TRUST|"
                         r"ノーザン・?トラスト|CITIBANK|シティバンク|HSBC|UBS|BARCLAYS|バークレイズ|BNP|CREDIT SUISSE|"
                         r"GOVERNMENT OF NORWAY|ノルウェー政府|INTERACTIVE BROKERS|楽天証券|SBI証券|松井証券|auカブコム|マネックス証券",
                         re.I)
FINANCIAL_HOLDER = re.compile(r"銀行|信用金庫|生命保険|損害保険|海上火災|火災海上|保険|証券|信託")
SELF_HELD = re.compile(r"従業員持株会|持株会|役員持株会|取引先持株会|自己株式|財団")


def major_holders(fs: FactSet, f: Fundamentals) -> None:
    rows: dict[str, dict] = {}
    for x in fs.facts:
        if "MajorShareholders" not in x.context and "MajorShareholder" not in x.local:
            continue
        key = x.context
        if x.local.startswith("NameMajorShareholders") and x.text:
            rows.setdefault(key, {})["name"] = nfkc(re.sub(r"<[^>]+>", "", x.text)).strip()
        elif "ShareholdingRatio" in x.local and x.value is not None:
            rows.setdefault(key, {})["pct"] = x.value
        elif x.local.startswith("NumberOfSharesHeld") and x.value is not None and "MajorShareholders" in x.context:
            rows.setdefault(key, {})["shares"] = x.value
    out = [r for r in rows.values() if r.get("name") and r.get("pct") is not None]
    if out:
        if max(r["pct"] for r in out) <= 1.0:
            for r in out:
                r["pct"] *= 100
        out = [r for r in out if "計" != r["name"] and not r["name"].startswith("計")]
    else:
        tb = next((b for b in fs.text_blocks() if "MajorShareholders" in b.local), None)
        if tb:
            tables, _ = _tables(tb.text or "")
            for t in tables:
                for r in t:
                    if len(r) < 3 or not re.search(r"\d", r[-1]):
                        continue
                    pct = parse_jp_number(r[-1])
                    name = r[0]
                    if pct is None or pct > 100 or not name or re.search(r"^(計|氏名|名称)", name):
                        continue
                    out.append({"name": name, "pct": pct})
    f.major_holders = sorted(out, key=lambda r: -r["pct"])[:12]


def officers(fs: FactSet, f: Fundamentals) -> None:
    by_ctx: dict[str, dict] = {}
    for x in fs.facts:
        if "InformationAboutDirectorsAndCorporateAuditors" not in x.local and "InformationAboutOfficers" not in x.local:
            continue
        d = by_ctx.setdefault(x.context, {})
        if x.local.startswith("NumberOfSharesHeld") and x.value is not None:
            d["shares"] = x.value
        elif x.local.startswith("DateOfBirth") and x.text:
            m = re.search(r"(\d{4})", nfkc(x.text))
            d["born"] = int(m.group(1)) if m else None
        elif x.local.startswith("OfficialTitleOrPosition") and x.text:
            d["title"] = nfkc(re.sub(r"<[^>]+>", "", x.text))
    if not by_ctx:
        return
    f.officers_shares = sum(d.get("shares") or 0 for d in by_ctx.values())
    ceo = next((d for d in by_ctx.values() if "代表取締役" in (d.get("title") or "") and d.get("born")), None)
    if ceo and f.period_end is not None:
        import datetime as _dt
        f.ceo_age = _dt.date.today().year - ceo["born"]


def going_concern(fs: FactSet, f: Fundamentals) -> None:
    f.going_concern = any("GoingConcern" in x.local and (x.text or "").strip() for x in fs.facts)


def from_facts(fs: FactSet, code: str = "", source: str = "", annual: bool = True) -> Fundamentals:
    f = balance_sheet(fs, code, source)
    income(fs, f)
    cash_flows(fs, f)
    shares(fs, f)
    if annual:
        rental_property(fs, f)
        major_holders(fs, f)
        officers(fs, f)
        going_concern(fs, f)
    return f
