from jpscreen.classify import Tag
from jpscreen.frontrun import score
from jpscreen.fundamentals import Fundamentals
from jpscreen.prices import Quote
from jpscreen.rate import evaluate
from jpscreen.value import ValueRow
import datetime as dt


def _quote(price=1000, adv=5e7):
    return Quote(code="1234", price=price, shares_issued=10_000_000, adv=adv, eps=50)


def test_going_private_bid_with_spread_is_five_stars():
    h = {}
    tag = Tag("tender_offer", "catalyst", role="target")
    evaluate(h, tag, {"price": 1150, "delisting": True, "partial": False, "board_view": "support"}, _quote(), None, ["re_netnet"])
    assert round(h["premium_pct"]) == 15
    assert h["stars"] == 5


def test_no_spread_partial_tender_is_low():
    h = {}
    tag = Tag("tender_offer", "catalyst", role="target")
    evaluate(h, tag, {"price": 990, "partial": True}, _quote(), None, [])
    assert h["stars"] <= 2


def test_illiquid_buyback_is_penalised_and_failing_company_capped():
    f = Fundamentals(op_income=-1e8, net_income=-2e8, liabilities=1e9, equity=2e9, shares_issued=10_000_000)
    h = {}
    evaluate(h, Tag("buyback", "catalyst"), {"amount": 1e9}, _quote(adv=1e6), f, [])
    assert not h["passes"] and h["stars"] <= 2
    assert any("illiquid" in w for w in h["why"])


def test_frontrun_listed_subsidiary():
    row = ValueRow(code="1234", name="X", market="東S", re_index=0.45, pb=0.4, mcap=5e9, adv=2e6, am_mcap=0.8)
    f = Fundamentals(major_holders=[{"name": "親会社株式会社", "pct": 62.0}, {"name": "日本マスタートラスト信託銀行株式会社（信託口）", "pct": 5.0}])
    fr = score(row, f, [], [], dt.date(2026, 10, 21))
    assert "listed_subsidiary" in fr.signals and fr.stars >= 3
