import json

from jpscreen.dbsync import restore_value
from jpscreen.export import screen_parts


class F:
    net_income, op_income, source, major_holders = 7.5e8, 9e8, "edinet:S1", [{"name": "イサム興産株式会社", "pct": 56.0}]


class Store:
    def fundamentals(self, code):
        return F() if code == "4624" else None


def test_value_screen_round_trips_through_the_database(tmp_path):
    rows = [dict(code="4624", name="イサム塗料", market="東S", mcap=75e8, source="edinet:S1", price=3900, pe=9.8, ni=7.5e8,
                 nb="listed: Cash > market cap"),
            dict(code="9999", name="X", market="東S", mcap=None, source="edinet:S2")]
    parts = screen_parts(rows)
    assert sum(p["count"] for p in parts.values()) == 1                      # no market cap: not screened
    (tmp_path / "screen").mkdir()
    (tmp_path / "watchlist").mkdir()
    for k, v in parts.items():
        (tmp_path / "screen" / f"{k}.json").write_text(json.dumps(v))
    (tmp_path / "watchlist" / "re_netnet.json").write_text(json.dumps({"count": 1, "rows": [dict(code="4624", re_index=0.55, nca=113e8)]}))
    (tmp_path / "watchlist" / "meta.json").write_text(json.dumps({"date": "2026-11-07", "price_date": "2026-11-06"}))
    v = restore_value(Store(), tmp_path)
    r = v["rows_all"][0]
    assert r["code"] == "4624" and r["ni"] == 7.5e8 and r["source"] == "edinet" and r["re_index"] == 0.55 and r["nb"].startswith("listed")
    assert v["lists"]["_tags"] == {"4624": ["re_netnet"]} and v["meta"]["price_date"] == "2026-11-06"
    # rows written before the profit was stored are completed from the fundamentals
    for k in parts:
        (tmp_path / "screen" / f"{k}.json").write_text(json.dumps({"count": 0, "rows": []}))
    (tmp_path / "screen" / "p0.json").write_text(json.dumps({"count": 1, "rows": [dict(code="4624", name="イサム塗料", mcap=75e8)]}))
    r = restore_value(Store(), tmp_path)["rows_all"][0]
    assert r["ni"] == 7.5e8 and r["source"] == "edinet:S1" and r["top_holder_pct"] == 56.0
