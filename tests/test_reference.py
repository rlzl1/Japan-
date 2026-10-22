"""The screen must reproduce the reference RE net-net ranking (screenshots of 2026-10-09) and surface every name."""
from pathlib import Path

from jpscreen.value import build_lists, load_reference, reference_row

REF = Path(__file__).resolve().parent.parent / "data" / "reference_netnet_2026-10-09.csv"


def test_formula_reproduces_reference_index():
    for ref in load_reference(REF):
        row = reference_row(ref, None)
        # the published index is rounded and market caps are shown to 0.1億
        assert abs(row.re_index - float(ref["index"])) < 0.005, (ref["code"], row.re_index, ref["index"])


def test_every_reference_name_lands_on_the_cheapness_list_in_order():
    refs = load_reference(REF)
    rows = [reference_row(r, None) for r in refs]
    lists = build_lists(rows)
    got = [r["code"] for r in lists["re_netnet"]["rows"]]
    assert set(got) == {r["code"] for r in refs}
    ranks = [int(next(x for x in refs if x["code"] == c)["rank"]) for c in got]
    # same order as the reference, allowing ties that the 3-decimal index cannot separate
    assert sum(1 for a, b in zip(ranks, ranks[1:]) if b < a - 1) == 0


def test_isamu_paint_is_tracked():
    refs = {r["code"]: r for r in load_reference(REF)}
    assert "4624" in refs
    row = reference_row(refs["4624"], None)
    assert row.re_index < 0.6


def test_market_cap_after_split_and_cancellation():
    from jpscreen.fundamentals import Fundamentals
    from jpscreen.prices import Quote
    from jpscreen.value import market_cap
    f = Fundamentals(code="2814", shares_issued=7_377_460, treasury_shares=3_498_600)
    # 2-for-1 split after the filing: Yahoo shows the new count, treasury doubles too
    assert market_cap(f, Quote(code="2814", price=1812, shares_issued=14_754_920)) == 1812 * (14_754_920 - 6_997_200)
    # 500,000 treasury shares cancelled: outstanding unchanged
    assert market_cap(f, Quote(code="2814", price=1000, shares_issued=6_877_460)) == 1000 * (7_377_460 - 3_498_600)
    # no change
    assert market_cap(f, Quote(code="2814", price=1000, shares_issued=7_377_460)) == 1000 * (7_377_460 - 3_498_600)
