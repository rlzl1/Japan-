import datetime as dt

from jpscreen.rating import holding_rating, rate, standing_catalysts, value_points


def test_re_index_is_a_value_test():
    pts, why = value_points(dict(re_index=0.45, op=1e8, ni=1e8))
    assert pts == 2.0 and "RE net-net" in why
    pts, why = value_points(dict(re_index=0.65, op=None, ni=None))
    assert pts == 1.0 and "profits not read" in why


def test_workout_ignores_value_and_general_caps_it():
    h = dict(premium_pct=12.0, asset_margin_vs_mcap=1.5, passes=True, adv=5e7)
    stars, why, r = rate(h, "tender_offer", "tender", "special", {"delisting": True}, {})
    assert r["workout"] and r["value_used"] == 0 and stars == 5
    h = dict(asset_margin_vs_mcap=1.5, passes=True, adv=5e7, buyback_vs_mcap=0.005)
    stars, why, r = rate(h, "buyback", "buyback", "returns", {}, {})
    assert r["edge"] == 0 and r["value_used"] == 1.0 and stars == 2      # cheapness alone stops at two stars


def test_holding_rating_uses_recent_radar_edge():
    today = "2026-10-22"
    ev = [dict(id="a", date="2026-10-01", edge=3.0, label="Tender offers", headline="x")]
    stars, r = holding_rating(dict(re_index=0.5, op=1, ni=1, adv=2e7), ev, [], today)
    assert r["edge"] == 3.0 and stars == 5
    old = [dict(id="a", date="2026-07-01", edge=3.0, label="Tender offers", headline="x")]   # 113 days: half weight
    stars, r = holding_rating(dict(adv=2e7), old, [], today)
    assert r["edge"] == 1.5


def test_standing_catalysts_from_history():
    hist = [dict(date="2026-05-01", kind="buyback"), dict(date="2026-06-01", kind="buyback"),
            dict(date="2026-09-01", kind="listing", flags={"breach": True})]
    out = standing_catalysts(hist, dict(div_yield=0.055), None, [], [], "2026-10-22")
    assert out[0][0] == 1.0 and any("listing" in w for _, w in out) and any("dividend yield" in w for _, w in out)
