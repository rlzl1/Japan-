from jpscreen.fundamentals import parse_rental_note
from jpscreen.xbrl import parse_inline

NOTE = """<p>（単位：百万円）</p><table>
<tr><td></td><td></td><td>前連結会計年度</td><td>当連結会計年度</td></tr>
<tr><td rowspan="3">連結貸借対照表計上額</td><td>期首残高</td><td>1,200</td><td>1,180</td></tr>
<tr><td>期中増減額</td><td>△20</td><td>△15</td></tr>
<tr><td>期末残高</td><td>1,180</td><td>1,165</td></tr>
<tr><td colspan="2">期末時価</td><td>9,800</td><td>10,450</td></tr>
</table><p>（注）期末の時価は、主として不動産鑑定士による不動産鑑定評価書に基づく金額であります。</p>"""


def test_rental_note_table():
    r = parse_rental_note(NOTE)
    assert r["method"] == "table"
    assert r["fair_value"] == 10_450e6 and r["book_value"] == 1_165e6


def test_rental_note_immaterial():
    r = parse_rental_note("<p>賃貸等不動産の総額に重要性が乏しいため、注記を省略しております。</p>")
    assert r["fair_value"] == 0 and r["method"] == "immaterial"


IX = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body>
<ix:nonFraction name="jppfs_cor:CashAndDeposits" contextRef="CurrentYearInstant" unitRef="JPY" decimals="-6" scale="6" format="ixt:numdotdecimal">1,234</ix:nonFraction>
<ix:nonFraction name="jppfs_cor:Liabilities" contextRef="CurrentYearInstant" unitRef="JPY" decimals="-6" scale="6" sign="-" format="ixt:numdotdecimal">56</ix:nonFraction>
</body></html>"""


def test_inline_scale_and_sign():
    fs = parse_inline(IX)
    assert fs.num("CashAndDeposits") == 1_234e6
    assert fs.num("Liabilities") == -56e6


def _fx(code):
    from pathlib import Path
    return (Path(__file__).parent / "fixtures" / f"rental_{code}.html").read_text()


def test_rental_note_real_layouts():
    """Real notes: two classes of property (3494), footnote markers in labels (5921), measures as columns with one
    row per class and last year's table first (5946, 9036), ditto marks (5915), row-spanning labels (6396)."""
    cases = {"3494": (19_818_760e3, 15_719_735e3), "5921": (1_802_007e3, 456_477e3), "5946": (10_255e6, 6_266e6),
             "9036": (9_801_380e3, 6_892_414e3), "5915": (7_683e6, 1_158e6), "6396": (13_680_000e3, 1_388_956e3)}
    for code, (fv, bv) in cases.items():
        r = parse_rental_note(_fx(code))
        assert abs(r["fair_value"] - fv) < 1e6 and abs(r["book_value"] - bv) < 1e6, (code, r)


def test_rental_note_not_applicable():
    r = parse_rental_note("<p>（賃貸等不動産関係）</p><p>当事業年度（自　2025年４月１日　至　2026年３月31日）</p><p>該当事項はありません。</p>")
    assert r["fair_value"] == 0 and r["method"] == "immaterial"
