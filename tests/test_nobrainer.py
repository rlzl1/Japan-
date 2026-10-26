import datetime as dt

from jpscreen.nobrainer import general, workout

TODAY = dt.date(2026, 10, 26)


class F:
    liabilities, equity, going_concern = 30.0, 100.0, False
    fcf_history = [4.0e8, 3.5e8, 5.0e8]


def row(**kw):
    r = dict(code="4624", name="イサム塗料", market="東S", mcap=75e8, source="edinet:x", ni=7.5e8, profit_years=5, years=5, adv=3.4e6,
             pe=9.8, pe_fwd=8.5, pb=0.42, am_mcap=1.27, ncav_mcap=1.1, re_index=0.55, div_yield=1.28, treasury_pct=4.7,
             top_holder_pct=56.0, top_holder="イサム興産", fit=96.0)
    r.update(kw)
    return r


def test_isamu_style_company_passes_cash_and_re():
    g = general(row(), F(), [], [], {"stars": 4, "reasons": ["founder control"]}, TODAY)
    assert set(g["kinds"]) >= {"cash", "re", "owner"} and g["top"]
    assert any("1.27×" in w for w in g["why"]) and g["live"]["fcf3"] > 0.05 and "thin: ¥3.4M a day" in g["flags"]


def test_gates_drop_losses_debt_and_illiquid_names():
    assert general(row(ni=-1.0), F(), [], [], None, TODAY) is None
    assert general(row(profit_years=2), F(), [], [], None, TODAY) is None
    assert general(row(adv=5e5), F(), [], [], None, TODAY) is None
    f = F(); f.liabilities = 250.0
    assert general(row(), f, [], [], None, TODAY) is None
    f = F(); f.going_concern = True
    assert general(row(), f, [], [], None, TODAY) is None


def test_cash_box_with_nothing_coming_out_is_flagged_not_listed():
    g = general(row(div_yield=0.3, treasury_pct=0.5, re_index=None, ncav_mcap=0.5, top_holder_pct=10), F(), [], [], None, TODAY)
    assert g is None or "cash" not in g["kinds"]


def test_treasury_pile_and_netnet_rules():
    g = general(row(treasury_pct=44.7, pb=0.35, pe=30.6, pe_fwd=21.9, re_index=0.39, am_mcap=2.2, ncav_mcap=1.26, top_holder_pct=9.4, fit=77.0),
                F(), [], [], None, TODAY)
    assert "treasury" in g["kinds"] and "re" not in g["kinds"]            # P/E 21.9 is above the RE rule's 20
    g = general(row(ncav_mcap=1.45, am_mcap=-0.57, re_index=None, pe_fwd=10.9, treasury_pct=8.3, top_holder_pct=18.0), F(), [], [], None, TODAY)
    assert g["kinds"] == ["netnet"]


def test_buyback_history_counts():
    ev = [dict(date="2026-07-17", kind="buyback"), dict(date="2025-11-01", kind="buyback")]
    g = general(row(treasury_pct=2.0, re_index=None, am_mcap=0.3, pb=0.6, pe_fwd=11.0, top_holder_pct=10), F(), ev, [], None, TODAY)
    assert "buy" in g["kinds"] and "2 buyback decisions" in g["why"][0]


def test_workouts_need_a_real_spread_or_a_contest():
    base = dict(code="3297", name="東武住販", kind="tender_offer", status="open", price=2000.0, last=1993.0, spread=0.0035, days_left=38,
                end="2026-12-03", bump_score=0, flags=[], board_view="support")
    assert workout(base, TODAY) is None                                   # 0.35% in 38 days
    w = workout(dict(base, spread=0.025), TODAY)
    assert w and w["kinds"] == ["wo"] and "+2.5% to the bid" in w["why"][0]
    w = workout(dict(base, code="2612", price=2514.0, last=2955.0, spread=-0.149, bump_score=2, flags=["trading above the bid", "low premium"]), TODAY)
    assert w and w["top"] and "contested" in w["why"][-1] and "a year" not in w["why"][0]
    assert workout(dict(base, status="closed"), TODAY) is None
