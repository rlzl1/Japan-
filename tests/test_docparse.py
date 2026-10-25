from jpscreen.docparse import parse_amount, parse_dividend, parse_guidance, parse_tender

TOB = """
２．買付け等の概要
 公 開 買 付 け の 目 的   完全子会社化
                    2026 年 10 月 14 日（水曜日）から 2026 年 12 月３日（木曜日）ま
 買 付 け 等 の 期 間
                    で（35 営業日）
 買 付 け 等 の 価 格   普通株式１株につき、金 2,000 円
 買 付 予 定 数 の 上 限   ―（株）
 買 付 予 定 数 の 下 限 （ 注 ）  1,776,600（株）
 当 社 の 意 見   賛同及び応募推奨
"""


def test_tender_terms():
    out = parse_tender(TOB)
    assert out["price"] == 2000
    assert out["start"] == "2026-10-14" and out["end"] == "2026-12-03"
    assert out["partial"] is False and out["delisting"] is True
    assert out["board_view"] == "support"


def test_tender_bump_takes_latest_price():
    txt = "本公開買付価格を 3,680 円から 3,681 円に変更することを決定し、…本公開買付価格を 3,681 円から 3,682 円に変更"
    out = parse_tender(txt)
    assert out["price"] == 3682 and out["old_price"] == 3681


DIV = """
                                                   1株当たり年間配当金(円)
                                   中間                   期    末               合    計
直 近 配 当 予 想                                                     20円00銭               36円50銭
当       期       実       績                16円50銭
前       期       実       績                12円50銭                  20円00銭               32円50銭
"""


def test_dividend_annual_rows():
    out = parse_dividend(DIV)
    assert out["dps"] == 36.5 and out["prior_year"] == 32.5
    assert round(out["raise_pct"], 1) == 12.3


GUIDE = """
                            百万円         百万円          百万円      百万円      円   銭
前 回 発 表 予 想 ( A )           8,104           22        13      △18      △4.03
今 回 修 正 予 想 ( B )           8,187          △58       △52      △72     △16.32
増 減 額 ( B - A )                83          △80       △65      △54
"""


def test_guidance_direction():
    out = parse_guidance(GUIDE)
    assert out["op_prev"] == 22 and out["op_new"] == -58
    assert out["direction"] == "down"


def test_amounts():
    assert parse_amount("約1,234百万円") == 1_234_000_000
    assert parse_amount("12億円") == 1_200_000_000
    assert parse_amount("35億4,000万円") == 3_540_000_000
    assert parse_amount("1,000,000千円") == 1_000_000_000


def test_guidance_first_forecast_full_year_vs_last_year():
    """Isamu Paint, 5 Aug 2026: the forecast withheld in May, published with a half-year and a full-year table."""
    from pathlib import Path
    from jpscreen.docparse import parse_guidance
    r = parse_guidance((Path(__file__).parent / "fixtures" / "guidance_4624_first.txt").read_text())
    assert r["first_forecast"] and r["direction"] == "up"
    assert r["op_new"] == 1060 and r["op_prev"] == 915 and round(r["op_chg"]) == 16
    assert r["ord_new"] == 1240 and r["ord_prev"] == 1077


def test_tender_price_is_the_terms_table_not_an_earlier_proposal():
    from jpscreen.docparse import parse_tender
    target_doc = ("ヒルハウスは、2026年9月15日、本公開買付価格を1,629円(提案実施日の前営業日である同月14日の終値921円に対して76.87%のプレミアム)"
                  "とする提案を行いました。買付け等の期間 2026年10月23日(金曜日)から2026年12月7日(月曜日)まで(30営業日)"
                  "(1)普通株式 当社株式1株につき、金2,020円(本公開買付けにおける当社株式1株当たりの買付け等の価格を、以下「本公開買付価格」といいます。)"
                  "買付予定数の上限 ―(株) 買付予定数の下限 2,970,800(株) 当社取締役会は賛同の意見を表明し、応募を推奨")
    r = parse_tender(target_doc)
    assert r["price"] == 2020 and r["lower_limit"] == 2970800 and r["upper_limit"] is None and r["partial"] is False
    assert r["start"] == "2026-10-23" and r["end"] == "2026-12-07" and r["board_view"] == "support"
    bidder_doc = ("(4)買付け等の価格 1 普通株式1株につき、金2,020円 2 新株予約権 イ 第1回新株予約権1個につき、金1円 "
                  "(5)買付予定の株券等の数 株券等の種類 買付予定数 買付予定数の下限 買付予定数の上限 普通株式 4,456,145(株) 2,970,800(株) ―(株) "
                  "(注2)本公開買付けにおいては買付予定数の上限を設定しておりませんので、応募株券等の全部の買付け等を行います。")
    r = parse_tender(bidder_doc)
    assert r["price"] == 2020 and r["partial"] is False and r["upper_limit"] is None
