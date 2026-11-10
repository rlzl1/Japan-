import datetime as dt

from jpscreen.workouts import SituationBook, board, grade_cash, parse_return
from jpscreen.deals import DealBook
from jpscreen.prices import Quote
from tests.test_deals import D, Ctx, Names, ANNOUNCE, RESULT_TXT, Comp


def test_grade_cash():
    base = dict(type="tender", status="open", price=2000.0, spread=0.02, board_view="support")
    assert grade_cash(base)[0] == "A"
    assert grade_cash(dict(base, partial=True))[0] == "B"
    assert grade_cash(dict(base, status="announced"))[0] == "B"
    assert grade_cash(dict(base, spread=0.003))[0] == "B"
    assert grade_cash(dict(base, board_view="oppose"))[0] == "C"
    assert grade_cash(dict(base, spread=0.44))[0] == "C"
    assert grade_cash(dict(base, spread=-0.15))[0] == "C"
    assert grade_cash(dict(base, price=None, spread=None))[0] == "C"
    g, plus, con = grade_cash(dict(base, type="squeeze"))
    assert g == "A" and any("vote" in x for x in plus)


def test_parse_return():
    r = parse_return("資本剰余金を原資とする配当 1株当たり配当金 120円00銭 基準日 2026年12月31日 効力発生日 2027年3月30日")
    assert r["per_share"] == 120 and r["record_date"] == "2026-12-31" and r["pay_date"] == "2027-03-30"


SWAP = ("当社の普通株式1株に対して、親会社の普通株式0.85株を割当て交付いたします。 株式会社親会社(証券コード:9001)を株式交換完全親会社とし、当社を株式交換完全子会社とする "
        "株式交換の効力発生日 2027年1月1日 当社株式は上場廃止となる予定")


class Names2(Names):
    def find(self, title, exclude=""):
        return "9001" if "親会社" in title and exclude != "9001" else super().find(title, exclude)


def test_parse_ratio_and_party_matching():
    from jpscreen.workouts import parse_ratio, match_party
    r = parse_ratio("ワールドの普通株式1株に対して共同持株会社の普通株式1株を、TSIHDの普通株式1株に対して共同持株会社の普通株式0.8株を割り当て交付いたします。"
                    "効力発生日)共同持株会社株式新規上場日2027年3月1日(月)(予定)")
    assert [(x["frm"], x["n"]) for x in r["legs"]] == [("ワールド", 1), ("TSIHD", 0.8)] and r["effective"] == "2027-03-01"
    r = parse_ratio("月島ホールディングスの普通株式1株に対して三菱化工機の普通株式0.88株を割当て交付いたします。効力発生日は2027年7月1日を予定")
    assert r["legs"][0] == dict(frm="月島ホールディングス", to="三菱化工機", n=0.88) and r["effective"] == "2027-07-01"
    parties = {"3612": "ワールド", "3608": "TSIホールディングス"}
    assert match_party("TSIHD", parties) == "3608" and match_party("ワールド", parties) == "3612" and match_party("共同持株会社", parties) is None


def test_board_ratio_after_tender_and_liquidation():
    ctx = Ctx({"s1": SWAP, "a1": ANNOUNCE, "a3": RESULT_TXT, "l1": "解散の理由:事業継続が困難 残余財産の分配 1株当たり 350円 上場廃止"})
    ctx.universe.update({"9001": Comp("親会社"), "5555": Comp("清算会社")})
    ctx.activists = []
    q = ctx.prices.quotes
    q["3297"] = Quote(code="3297", price=1960.0, price_date="2026-12-10")
    q["4626"] = Quote(code="4626", price=800.0, price_date="2026-12-10")
    q["9001"] = Quote(code="9001", price=1000.0, price_date="2026-12-10")
    q["5555"] = Quote(code="5555", price=300.0, price_date="2026-12-10")
    deals, sits = DealBook(ctx.store), SituationBook(ctx.store)
    deals.update(ctx, [D("a1", "3297", "株式会社カチタスによる当社株券に対する公開買付けに関する賛同の意見表明及び応募推奨のお知らせ", dt.date(2026, 10, 13)),
                       D("a3", "3297", "株式会社カチタスによる当社株券等に対する公開買付けの結果に関するお知らせ", dt.date(2026, 12, 4))], Names())
    n = sits.update(ctx, [D("s1", "4626", "株式会社親会社による当社の完全子会社化に関する株式交換契約締結のお知らせ", dt.date(2026, 11, 20)),
                          D("l1", "5555", "当社の解散及び清算に関するお知らせ", dt.date(2026, 11, 25))], Names2())
    assert n == 2
    b = board(ctx, deals, sits, "2026-12-10")
    rows = {r["type"]: r for r in b["rows"]}
    ratio = rows["ratio"]
    assert ratio["code"] == "4626" and ratio["counter"] == "9001" and ratio["price"] == 850.0
    assert abs(ratio["spread"] - 0.0625) < 1e-4 and ratio["days"] == 22 and ratio["grade"] == "B"
    after = rows["after"]
    assert after["code"] == "3297" and abs(after["spread"] - (2000 / 1960 - 1)) < 1e-3 and after["grade"] in ("A", "B")
    liq = rows["liquidation"]
    assert liq["price"] == 350 and abs(liq["spread"] - (350 / 300 - 1)) < 1e-3
    assert b["stats"]["bids"] == 1 and b["memory"] and b["live"] >= 3
    # a withdrawal closes the situation
    sits.update(ctx, [D("s2", "4626", "株式交換契約の解約及び経営統合の中止に関するお知らせ", dt.date(2026, 12, 11))], Names2())
    assert sits.live("4626") is None
