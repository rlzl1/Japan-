import datetime as dt

from jpscreen.nobrainer import general, workout

TODAY = dt.date(2026, 10, 26)


class F:
    liabilities, equity, going_concern = 30.0, 100.0, False
    fcf_history = [4.0e8, 3.5e8, 5.0e8]
    net_income_history = [7.5e8, 7.0e8, 6.5e8, 7.2e8, 6.8e8]
    cash, securities, nci = 60e8, 5e8, 0.0


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


def test_workouts_are_graded_positive_spreads_only():
    base = dict(code="3297", name="東武住販", type="tender", status="open", price=2000.0, last=1993.0, spread=0.0035, days=38,
                date="2026-12-03", grade="A", terms="¥2,000 cash", plus=["board supports and recommends tendering"], concerns=[], flags=[])
    assert workout(base, TODAY) is None                                   # 0.35% in 38 days
    w = workout(dict(base, spread=0.025, ann=0.24), TODAY)
    assert w and w["kinds"] == ["wo"] and "+2.5% to the deal value" in w["why"][0] and w["top"]
    # a contested bid trading above its price is for the board, not a no-brainer; nor is a grade-C row
    assert workout(dict(base, spread=-0.149, grade="C"), TODAY) is None
    assert workout(dict(base, spread=0.44, grade="C"), TODAY) is None
    assert workout(dict(base, spread=0.03, status="closed"), TODAY) is None
    assert workout(dict(base, type="self_tender", spread=0.05, grade="B"), TODAY) is None


def test_one_off_profit_and_reasons():
    from jpscreen.nobrainer import assess, _rule_pe
    f = F(); f.net_income_history = [30e8, 5e8, 5e8, 5e8]
    pe, note = _rule_pe(row(pe=2.5, pe_fwd=None, mcap=75e8), f)
    assert round(pe, 1) == 5.6 and "3-year average" in note                # 75 / ((30+5+5)/3)
    pe, note = _rule_pe(row(pe=5.0, pe_fwd=12.0), F())
    assert pe == 12.0 and "forecasts a profit well below" in note
    card, why = assess(row(pe=5.0, pe_fwd=12.0), F(), [], [], None, TODAY)
    assert card and not card["top"] and "forecasts" in card["flags"][0] and why.startswith("listed: ")
    assert assess(row(ni=-1.0), F(), [], [], None, TODAY) == (None, "gate: a loss last year")
    assert assess(row(), F(), [], [], None, TODAY, blocked={"4624": "under a deal"})[1] == "under a deal"
    none, why = assess(row(am_mcap=0.2, ncav_mcap=0.5, re_index=0.9, pb=0.9, pe=14.0, pe_fwd=14.0, div_yield=2.0, treasury_pct=1.0, top_holder_pct=10),
                       F(), [], [], None, TODAY)
    assert none is None and why.startswith("passes the gates, no rule: P/E 14.0, net cash 0.20×")


def test_cash_rule_reports_cash_alone():
    g = general(row(), F(), [], [], None, TODAY)
    assert "(0.87× on cash and short-term securities alone)" in g["why"][0]     # (60 + 5) / 75
