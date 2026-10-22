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
