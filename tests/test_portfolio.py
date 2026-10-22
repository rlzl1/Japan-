import json

from jpscreen.dbsync import plan_batches
from jpscreen.news import kabutan_news, title_en
from jpscreen.portfolio import _position, load_holdings
from jpscreen.prices import parse_ranking_page


def test_holdings_merge_tracker_and_positions(tmp_path):
    (tmp_path / "t").mkdir(); (tmp_path / "p").mkdir()
    (tmp_path / "t" / "4624.json").write_text(json.dumps({"code": "4624", "nameEn": "Isamu Paint", "nameJa": "イサム塗料", "items": []}))
    (tmp_path / "p" / "4624.json").write_text(json.dumps({"code": "4624", "shares": 1000, "cost": 3500}))
    (tmp_path / "p" / "7203.json").write_text(json.dumps({"code": "7203", "group": "watch"}))
    H = {h["code"]: h for h in load_holdings(str(tmp_path / "t"), str(tmp_path / "p"))}
    assert H["4624"]["source"] == "both" and H["4624"]["shares"] == 1000
    assert H["7203"]["group"] == "watch" and H["7203"]["source"] == "radar"


def test_position_math():
    pos = _position(dict(shares=1000, cost=3500), dict(price=3940, chg_1d=0.02))
    assert pos["value"] == 3_940_000 and pos["pnl"] == 440_000 and abs(pos["pnl_pct"] - 0.1257) < 1e-3


def test_title_en():
    assert title_en("2027年3月期 第1四半期決算短信〔日本基準〕（連結）") == "Q1 earnings report"
    assert title_en("業績予想および配当予想の修正に関するお知らせ") == "Earnings and dividend forecast revision"
    assert title_en("当社滋賀工場における落雷の影響に関するお知らせ") == ""


KABU = '''<li class="py-2 border-b border-slate-200"><a target="_blank" href="https://tdnet-pdf.kabutan.jp/20260805/140120260805509217.pdf">
<div>業績予想および配当予想に関するお知らせ</div><span class="news_category-disclosure rounded-sm py-px px-1">開示</span>
<time class="text-slate-500" datetime="2026-08-05 13:00:00 +0900">8月5日</time></a></li>
<li class="py-2 border-b border-slate-200"><a target="_self" href="/stocks/4624/news/k202608050018/"><div>イサム、今期経常は15％増</div>
<span class="accounting_type_closing rounded-sm py-px px-1">決算</span><time datetime="2026-08-05 13:00:00 +0900">x</time></a></li>'''


class FakeHttp:
    def get_text(self, *a, **k):
        return KABU


def test_kabutan_parse():
    items = kabutan_news(FakeHttp(), "4624")
    assert items[0]["kind"] == "filing" and items[0]["id"] == "t140120260805509217"
    assert items[1]["kind"] == "news" and items[1]["cat"] == "results" and items[1]["url"].startswith("https://s.kabutan.jp/")


def test_ranking_page():
    html = ('{"rank":"1","stockCode":"4624","marketName":"東証STD","stockName":"イサム塗料(株)","detailLink":"x","bbsUrl":"y",'
            '"savePrice":"3,940","date":"15:30","rankingResult":{"a":null,"totalPriceObj":{"updateDateTime":"2026/10/22 17:40",'
            '"sharesIssued":"2,000,000","totalPrice":"7,880","shareUnit":"100"}}}')
    r = parse_ranking_page(html)[0]
    assert r["code"] == "4624" and r["price"] == 3940 and r["mcap"] == 7.88e9


def test_plan_batches_skips_unchanged_and_pins(tmp_path):
    out = tmp_path / "out"; db = tmp_path / "db"
    out.mkdir(); (db / "portfolio").mkdir(parents=True)
    (out / "portfolio__summary.json").write_text(json.dumps({"a": 2}))
    (out / "portfolio__4624.json").write_text(json.dumps({"b": 1}))
    (db / "portfolio" / "4624.json").write_text(json.dumps({"b": 1}))
    (db / "portfolio" / "summary.json").write_text(json.dumps({"a": 1}))
    b = plan_batches(out, db, {"portfolio/summary": 7})
    assert len(b) == 1 and len(b[0]) == 1 and b[0][0]["if_version"] == 7 and b[0][0]["doc_id"] == "summary"
