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


def test_store_chunks_stable_buckets_and_round_trip(tmp_path):
    from jpscreen.dbsync import restore_store, store_chunks
    from jpscreen.store import Store
    s = Store(tmp_path / "a")
    recs = s._load_fund()
    for i in range(300):
        recs[f"{1300 + i}"] = {"annual": {"source": f"edinet:S{i}", "filed": "2026-06-27", "cash": 1.5e9 + i,
                                          "major_holders": [{"name": "x" * 60, "pct": 12.3456}] * 10}}
    recs["1300"]["latest"] = dict(recs["1300"]["annual"])           # latest == annual: stored once
    a = store_chunks(s)
    assert all(len(v["recs"]) for v in a.values()) and "latest" not in a[next(k for k, v in a.items() if "1300" in v["recs"])]["recs"]["1300"]
    recs["1450"]["annual"]["cash"] = 9e9                              # one company changes: one document changes
    b = store_chunks(s)
    assert sum(a[k] != b[k] for k in a) == 1 and set(a) == set(b)
    db = tmp_path / "db" / "cache"; db.mkdir(parents=True)
    for k, v in b.items():
        (db / f"{k}.json").write_text(json.dumps(v))
    s2 = Store(tmp_path / "b")
    assert restore_store(s2, tmp_path / "db") == 300 and store_chunks(s2) == b
    h = s2._load_fund()["1301"]["annual"]["major_holders"]
    assert len(h) == 8 and len(h[0]["name"]) == 40 and h[0]["pct"] == 12.35


def test_news_noise():
    from jpscreen.news import is_noise
    noise = ["本日の【ボリンジャー|±3σブレイク】引け 上抜け= 35 銘柄 下抜け= 9 銘柄 (9月18日)",
             "★本日の【サプライズ決算】速報 (10月22日) (訂正)", "本日の注目個別銘柄:シライ電子、ソフトバンクG、コメ兵HDなど",
             "上方修正“先回り”、27年3月期【業績上振れ】候補〔第2弾〕 <成長株特集>",
             "<みんかぶ・個人投資家の予想から>=「買い予想数上昇」3位に佐藤食品", "金下建設(株)【1897】:決算情報"]
    for t in noise:
        assert is_noise(dict(kind="news", ja=t, src="Kabutan")), t
    for t in ["金下建、今期経常を一転54%増益に上方修正", "佐藤食品は高い、株式分割を好感", "イサムが後場大幅高、価格改定効果で26年3月期業績は計画上振れ",
              "佐藤食品について、光通信(株)は保有割合が増加したと報告 [変更報告書No.1]"]:
        assert not is_noise(dict(kind="news", ja=t, src="Kabutan")), t
    assert is_noise(dict(kind="news", ja="佐藤食品工業 サトウのごはん", src="もぐナビ"))
    assert not is_noise(dict(kind="news", ja="イサム塗料滋賀工場、落雷で出荷一時停止", src="LOGISTICS TODAY"))
    assert not is_noise(dict(kind="filing", ja="本日の開示", src="TDnet"))
