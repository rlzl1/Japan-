# Japan Filing Radar

The Japanese twin of the Korea Filing Radar: every TDnet disclosure sorted into special-situation
categories and rated one to five stars with the same rules, a weekly screen of the whole market for
cheap stocks (with the **不動産込みネットネット指数** / real-estate net-net ranking as its own tab), and a
pre-deal radar that tries to find the next MBO, parent buy-out or tender offer before it is announced.

```
python -m jpscreen radar            # today's TDnet disclosures (Mondays include the weekend)
python -m jpscreen radar --days 21  # backfill (TDnet keeps PDFs ~31 days)
python -m jpscreen value            # value lists + pre-deal radar + watchlist coverage
python -m jpscreen edinet           # whole-market fundamentals from EDINET (needs EDINET_API_KEY)
python -m jpscreen all              # edinet (if key) + value + radar + export
python -m jpscreen port --tracker-dir DIR [--positions-dir DIR]   # portfolio tab
python -m jpscreen daily --db-dir DIR --tracker-dir DIR --versions FILE   # what the scheduled refresh runs
```

Live dashboard: https://claude.ai/artifact/MBnsootP72UWgTs4ZZaLz9 (reads the `state/out/db/*.json`
documents, written to its database after each run).

Outputs land in `state/`: `runs/<date>.json`, `value.json`, `frontrun.json`, `coverage.json`,
`out/japan_radar.xlsx` (one tab per view) and `out/db/*.json` (the documents the dashboard reads).
The dashboard page is `dashboard/japan_radar.html`.

## Views

| Key | View | What it shows |
|---|---|---|
| 1 | RADAR | Rated disclosures by category, top ideas, warnings |
| 2 | PORT | Your holdings (Tokyo Filings Tracker + positions added on the page): P&L, rating, every filing and story |
| 3 | CHEAP | 不動産込みネットネット指数 ranking, cheapest first (same columns as the reference ranking) |
| 4 | VALUE | Net-nets, cash-rich, deep value, treasury piles, listed subsidiaries |
| 5 | PRE-DEAL | Probability-ranked buy-out candidates with the signals behind each |
| 6 | COVERAGE | Every name in `data/watchlist.csv` and the reference ranking: surfaced or missed, and why |
| 7 | HELP | Stars, categories, formulas, priors |

Company names show in English (EDINET's register; the Japanese name is on hover). Type a code (`4624`) or a name,
in English or Japanese, in the command line to open the security panel; `1`–`7` switch views, `P` opens
the portfolio. Add a holding on the PORT tab (code, held/watching, shares, average cost): it shows at once with
its last price and is researched by the next refresh (or press REFRESH).

## RE net-net index (CHEAP)

```
index = market cap / (net current assets + rental real estate at fair value + 50% x land not already counted)
net current assets = cash + trade receivables + securities + investment securities
                     - total liabilities - non-controlling interests            (inventories excluded)
```

Rental real estate comes from the 賃貸等不動産 note of the annual report (fair value incl. buildings), summed over
every class of property the note lists (rental, and property partly used by the company); the land add-on is
`max(0, land book value - rental property book value) x 50%`. The parser reads the note's tables whether the
measures run down rows or across columns, skips last year's table, and handles footnote and ditto marks
(`tests/fixtures/rental_*.html` are real notes). The index formula reproduces all 50 rows of the 2026-10-09
reference ranking from its own columns (`data/reference_netnet_2026-10-09.csv`, `tests/test_reference.py`). Rebuilt
from the filings, all 50 come out below 1.0 and 41 of 50 match the reference's real-estate figure within 5%; the
other nine are 5-11% lower, because the note's book value includes buildings, so the land add-on is conservative.
The coverage check (COVERAGE tab) reports any reference or watchlist name the live screen fails to surface, with
the reason.

## Categories (same ids as Korea)

Special situations: tender offers & going-private (TOB, MBO, parent buy-out, self-tender, squeeze-out),
sale processes (press-report answers, special committees, strategic reviews), change of controlling
shareholder, mergers & share exchanges (implied value from the ratio), spin-offs, activists & 5% stakes
(incl. 公開買付けに準ずる行為 and takeover-defence fights), shareholder proposals, control lawsuits,
liquidations, reverse mergers, listing-criteria pressure (上場維持基準).
Returns: buybacks, cancellations, dividends, value-up plans. Business: guidance raised, big orders.
Assets: asset sales and hidden-asset gains. Warnings: distress, dilution, withdrawn deals.

## Stars

The Korea Filing Radar's current system: **1 + edge + value − risk** (5★ 4.75+, 4★ 3.75+, 3★ 2.75+, 2★ 1.75+).
Edge is what the event is worth on its own numbers (spread to a going-private bid, exchange ratio vs market,
buyback vs market cap, after-tax gain on a property sale, a special committee or proposal, listing pressure on a
cheap stock); value is the single best of the classic tests, with the RE net-net index added for Japan; risk is
thin trading, exchange designations and warnings. Workouts (bids, squeeze-outs, share exchanges, liquidations) are
priced on the deal, so value is not counted; elsewhere value counts only up to the edge plus one. Holdings use
the same parts, with edge from radar events on the company (120 days) or standing catalysts.

## Scheduled refresh

Tuesday to Saturday at 06:06 JST, after each trading day and before the open, a fresh session restores state
from the dashboard's database (`cache/fund_*` holds the fundamentals of every listed company), reads new EDINET
filings, scans TDnet, rebuilds the portfolio (holdings from the Tokyo Filings Tracker plus positions added on the
page), translates new titles, and writes back only the documents that changed (`state/out/batches.json`, pinned
to their versions). Saturdays also rerun the whole-market value screen on Friday's close. The REFRESH button on
the PORT tab starts the same task on demand. The task's prompt is `docs/refresh_prompt.md`. Holdings, positions
and the REFRESH button are visible only to the page's owner and editors.

Yahoo! Finance's quote and history pages fail intermittently (HTTP 500); a quote then falls back to the history
page for price, date and liquidity, and to the bulk ranking for shares and market cap.

## Backtest

```
python3 -m jpscreen backtest --codes 1897,2814,4624,9478 --months 12
```

Replays each company's TDnet disclosures over the window through the radar (classified, PDFs read, falling back
to Kabutan's permanent mirror for those TDnet has deleted, rated with the price and liquidity of the day) and
records the stock's return and its excess over the TOPIX ETF (1306, split-adjusted closes) 1 week, 1 month and
3 months after the next session. Without `--codes` it takes your holdings. Output: `state/backtest.json`.
Fundamentals are today's, so value points carry some hindsight; edge and risk use only the filing and the
prices of the time.

## Data sources

| Data | Source | Key |
|---|---|---|
| Disclosures + PDFs | TDnet via webapi.yanoshin.jp (fallback: TDnet list pages) | none |
| Earnings-release balance sheets | TDnet XBRL (harvested every run) | none |
| Annual/semi-annual reports, rental RE note, major shareholders, 5% reports | EDINET API v2 | `EDINET_API_KEY` (free) |
| Listed universe | JPX list + EDINET code list (incl. Nagoya/Fukuoka/Sapporo-only) | none |
| Prices, market caps, daily trading value | Yahoo! Finance Japan | none |

The EDINET key is read from the `EDINET_API_KEY` environment variable (or a gitignored `.env` locally); it is
never committed. Without it the radar runs fully; the value screen covers the reference ranking, your
watchlist and every company whose earnings release has been harvested (no rental-property notes, so
their real-estate value is land × 50% only). Register at https://api.edinet-fsa.go.jp/ and export
`EDINET_API_KEY` for the whole market.

## Tests

```
python -m pytest -q
```
