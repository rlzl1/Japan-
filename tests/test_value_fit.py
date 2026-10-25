from jpscreen.prices import Quote, parse_quote_page
from jpscreen.value import ValueRow, fit_scores, reprice


def row(code, **kw):
    r = ValueRow(code=code, name=code, market="東S", mcap=1e9, source="edinet:x", ni=1.0, years=5, profit_years=5)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def test_fit_is_a_percentile_of_the_weighted_mix():
    rows = [row("A", am_mcap=1.3, pe=6.0, treasury_pct=8, div_yield=2.0, top_holder_pct=55),
            row("B", am_mcap=0.2, pe=15.0, treasury_pct=1, div_yield=1.0, top_holder_pct=10),
            row("C", am_mcap=-0.5, pe=None, ni=-1.0, treasury_pct=0, div_yield=None, top_holder_pct=None),
            row("F", am_mcap=5.0, pe=2.0)]
    assert fit_scores(rows, financial_codes={"F"}) == 3
    assert rows[0].fit == 100 and rows[2].fit == 0 and rows[1].fit == 50 and rows[3].fit is None
    # the better asset test counts: net current assets above market cap with negative net cash
    rows = [row("A", am_mcap=-0.3, ncav_mcap=1.4, pe=6.0), row("B", am_mcap=0.1, ncav_mcap=0.1, pe=6.0)]
    fit_scores(rows)
    assert rows[0].fit > rows[1].fit


def test_reprice_scales_market_cap_ratios():
    value = dict(meta=dict(price_date="2026-10-20"), rows_all=[dict(code="4624", price=4000, price_date="2026-10-20", mcap=8e9,
                 re_index=0.6, pe=10.0, pb=0.44, am_mcap=1.2, div_yield=1.25)],
                 lists=dict(re_netnet=dict(count=1, rows=[dict(code="4624", price=4000, price_date="2026-10-20", re_index=0.6)])))
    n = reprice(value, {"4624": Quote(code="4624", price=3900.0, price_date="2026-10-23")})
    r = value["rows_all"][0]
    assert n == 1 and r["mcap"] == 7.8e9 and abs(r["re_index"] - 0.585) < 1e-9 and r["pe"] == 9.75 and abs(r["am_mcap"] - 1.2 / 0.975) < 1e-3
    assert value["lists"]["re_netnet"]["rows"][0]["price"] == 3900.0 and value["meta"]["price_date"] == "2026-10-23"
    # an older quote changes nothing
    assert reprice(value, {"4624": Quote(code="4624", price=100.0, price_date="2026-10-01")}) == 0


def test_quote_page_dividend_yield_from_dps():
    html = ('{"name":"イサム塗料【4624.T】","previousPrice":{"name":"前日終値","value":"3,950"},"price":{"value":"3,900"},'
            '"dps":{"name":"1株配当","value":"50.00"},"eps":{"name":"EPS","value":"456.59"},'
            '"sharesIssued":{"name":"発行済株式数","value":"2,000,000"},"totalPrice":{"name":"時価総額","value":"7,800"}}')
    q = parse_quote_page("4624", "4624.T", html)
    assert q.price == 3900 and q.dps == 50 and q.div_yield == 1.28 and q.eps == 456.59


def test_light_quote_takes_the_books_date_and_is_not_refetched():
    from jpscreen.prices import PriceBook
    from jpscreen.http import Http
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        book = PriceBook(Http(cache_dir=d + "/http", offline=True), path=d + "/p.json")
        book._put("1001", Quote(code="1001", price=100.0, price_date="2026-10-23", shares_issued=1e6))
        light = Quote(code="4624", price=3900.0, shares_issued=2e6, bps=9639.5, eps=456.6)
        book._put("4624", light)
        assert book.quotes["4624"].price_date == "2026-10-23"
        # a second light pass skips it (nothing to fetch, so offline mode raises nothing)
        book.prefetch({"4624": ["4624.T"]}, store=False, light=True)
        assert book.quotes["4624"].eps == 456.6
