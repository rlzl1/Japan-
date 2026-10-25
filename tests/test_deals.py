import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from jpscreen.deals import DealBook, _hint, bidder_from_title, parse_result


@dataclass
class D:
    id: str
    code: str
    title: str
    date: dt.date
    pdf_url: str = "https://x/a.pdf"
    time: dt.datetime = field(default=None)
    ymd: str = ""

    def __post_init__(self):
        self.time = self.time or dt.datetime.combine(self.date, dt.time(15, 30))
        self.ymd = self.date.strftime("%Y%m%d")


class Store:
    def __init__(self):
        self.parsed_docs, self.files = {}, {}

    def parsed(self, i):
        return self.parsed_docs.get(i)

    def put_parsed(self, i, c):
        self.parsed_docs[i] = c

    def read_json(self, rel, default=None):
        return self.files.get(rel, default)

    def write_json(self, rel, obj):
        self.files[rel] = obj

    def fundamentals(self, code):
        return None


class Comp:
    def __init__(self, name):
        self.name = name


class Ctx:
    def __init__(self, texts):
        self.store = Store()
        self.universe = {"3297": Comp("東武住販"), "4626": Comp("太陽ホールディングス")}
        self.texts = texts
        self.tdnet = self
        self.prices = type("P", (), {"quotes": {}})()
        self.value_rows = {}

    def pdf_text(self, d):
        return self.texts.get(d.id)


class Names:
    def find(self, title, exclude=""):
        return "3297" if "東武住販" in title else None


ANNOUNCE = ("公開買付者株式会社カチタス 買付け等の価格 普通株式1株につき、金2,000円 買付け等の期間 2026年10月14日から2026年12月3日まで "
            "買付予定数の上限 ― 買付予定数の下限 1,000,000株 公開買付けの目的 完全子会社化 当社取締役会は賛同の意見を表明し応募を推奨")
RESULT_TXT = "応募株券等の総数 2,345,600株 買付予定数の下限以上 買付け等を行います 所有割合 93.2%"


def test_hint():
    assert _hint("株式会社Xによる当社株券等に対する公開買付けの結果に関するお知らせ") == "closed"
    assert _hint("(開示事項の経過)KJ005株式会社による当社株式に対する公開買付け実施に向けた進捗状況のお知らせ") == "announced"
    assert _hint("買付条件等の変更に関するお知らせ") == "changed"
    assert _hint("(一部変更)株式会社Xによる当社株式に対する公開買付けに関する賛同の意見表明のお知らせ") == "changed"
    assert _hint("株式会社Xによる当社株券等に対する公開買付けに関する賛同の意見表明及び応募推奨のお知らせ") == "open"


def test_parse_result():
    r = parse_result(RESULT_TXT)
    assert r["tendered"] == 2345600 and r["success"] is True and r["owned_after_pct"] == 93.2


def test_bidder_from_title():
    assert bidder_from_title("(一部変更)SBINM合同会社による当社株式に対する公開買付けに関する賛同の意見表明のお知らせ") == "SBINM合同会社"
    assert bidder_from_title("当社親会社であるヒューリック株式会社による当社株券等に対する公開買付け") == "ヒューリック株式会社"


def test_deal_life_cycle_carries_terms_to_follow_ups():
    ctx = Ctx({"a1": ANNOUNCE, "a3": RESULT_TXT})
    book = DealBook(ctx.store)
    items = [
        D("a1", "3297", "株式会社カチタスによる当社株券に対する公開買付けに関する賛同の意見表明及び応募推奨のお知らせ", dt.date(2026, 10, 13)),
        D("a0", "8919", "株式会社東武住販(証券コード:3297)の株式に対する公開買付けの開始に関するお知らせ", dt.date(2026, 10, 13)),
        D("a2", "3297", "(一部変更)株式会社カチタスによる当社株券に対する公開買付けに関する賛同の意見表明のお知らせ", dt.date(2026, 10, 20)),
    ]
    assert book.update(ctx, items, Names()) == 3
    deal = book.latest("3297", "tender_offer")
    assert deal["price"] == 2000 and deal["end"] == "2026-12-03" and deal["status"] == "open"
    assert deal["board_view"] == "support" and deal["delisting"] and deal["bidder"] == "株式会社カチタス"
    assert len(deal["docs"]) == 3 and deal["changed"]
    # an amendment with no price in it still gets the terms
    assert book.terms_for("3297", "tender_offer")["price"] == 2000
    # the result closes it
    book.update(ctx, [D("a3", "3297", "株式会社カチタスによる当社株券等に対する公開買付けの結果に関するお知らせ", dt.date(2026, 12, 4))], Names())
    deal = book.latest("3297", "tender_offer")
    assert deal["status"] == "closed" and deal["success"] is True and deal["tendered"] == 2345600
    # a bid for a company we do not list is skipped; so is an affiliate-stake sale
    n = book.update(ctx, [D("b1", "4626", "株式会社Zによる株式会社Q(証券コード:9999)の株券等に対する公開買付けの開始に関するお知らせ", dt.date(2026, 10, 1)),
                          D("b2", "4626", "持分法適用会社株式に対する公開買付けに係る応募契約の締結に関するお知らせ", dt.date(2026, 10, 1))], Names())
    assert n == 0
    book.save("2026-12-05")
    assert "deals" in ctx.store.files["deals.json"]


def test_mark_rows_flags():
    ctx = Ctx({"a1": ANNOUNCE})
    book = DealBook(ctx.store)
    book.update(ctx, [D("a1", "3297", "株式会社カチタスによる当社株券に対する公開買付けに関する賛同の意見表明及び応募推奨のお知らせ", dt.date(2026, 10, 13))], Names())
    from jpscreen.prices import Quote
    ctx.prices.quotes["3297"] = Quote(code="3297", price=2030.0, price_date="2026-10-20", history=[("2026-10-13", 1500.0, 100.0), ("2026-10-10", 1480.0, 100.0)])
    rows = book.mark(ctx, "2026-10-20")
    r = rows[0]
    assert r["spread"] < 0 and "trading above the bid" in r["flags"]
    assert r["premium"] and abs(r["premium"] - (2000 / 1480 - 1)) < 1e-3      # the close before the first filing
    assert r["days_left"] == 44 and r["bump_score"] >= 1
