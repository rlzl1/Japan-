from jpscreen.prices import Quote, parse_history_page
from jpscreen.value import reference_row

HTML = ('<script>self.__next_f.push([1,"{\\"histories\\":[{\\"date\\":\\"2026-10-21\\",\\"values\\":[{\\"value\\":\\"7,650\\"},'
        '{\\"value\\":\\"7,650\\"},{\\"value\\":\\"7,650\\"},{\\"value\\":\\"7,650\\"},{\\"value\\":\\"100\\"},{\\"value\\":\\"7,650\\"}]},'
        '{\\"date\\":\\"2026-10-07\\",\\"values\\":[{\\"value\\":\\"7,450\\"},{\\"value\\":\\"7,450\\"},{\\"value\\":\\"7,450\\"},'
        '{\\"value\\":\\"7,450\\"},{\\"value\\":\\"100\\"},{\\"value\\":\\"7,450\\"}]}],'
        '\\"marginHistories\\":[{\\"date\\":\\"2026-10-09\\",\\"values\\":[{\\"value\\":\\"0\\"},{\\"value\\":\\"0\\"},'
        '{\\"value\\":\\"0\\"},{\\"value\\":\\"200\\"},{\\"value\\":\\"0\\"}]}]}"])</script>')


def test_history_ignores_margin_table():
    rows = parse_history_page(HTML)
    assert [r[0] for r in rows] == ["2026-10-21", "2026-10-07"]
    assert rows[0][1] == 7650


def test_reference_move_uses_last_trade_before_reference_date():
    ref = dict(rank="28", code="9311", name="アサガミ", market="東S", index="0.499", market_cap_oku="106.0",
               net_current_assets_oku="-59.5", real_estate_oku="271.8", pbr="0.45", market_cap_supplementary="1",
               asof="2026-10-09")
    q = Quote(code="9311", price=7650.0, history=parse_history_page(HTML))
    row = reference_row(ref, q)
    assert abs(row.flags["price_move_since_ref"] - (7650 / 7450 - 1) * 100) < 0.01
    assert 0.49 < row.re_index < 0.53


def test_quote_page_500_falls_back_to_history_and_bulk():
    from jpscreen.http import HttpError
    from jpscreen.prices import PriceBook, Quote

    hist = ('"histories":[{"date":"2026-10-22","values":[{"value":"3,900"},{"value":"3,960"},{"value":"3,890"},'
            '{"value":"3,940"},{"value":"1,200"},{"value":"3,940"}]}]')

    class Flaky:
        cache_dir, min_interval, offline = None, 0, False

        def get_text(self, url, **k):
            if url.endswith("/history"):
                return hist
            raise HttpError(url, 500, "")

    pb = PriceBook.__new__(PriceBook)
    from jpscreen.prices import YahooJapan
    pb.yj, pb.quotes = YahooJapan(Flaky()), {}
    pb.quotes["4624"] = Quote(code="4624", name="イサム塗料", price=3800, shares_issued=2_000_000, mcap_issued=7.6e9,
                              market_label="東証STD")
    q = pb.get("4624", ["4624.T"])
    assert q.price == 3940 and q.price_date == "2026-10-22" and q.adv == 3940 * 1200
    assert q.shares_issued == 2_000_000 and q.mcap_issued == 3940 * 2_000_000 and q.name == "イサム塗料"
