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
```

Outputs land in `state/`: `runs/<date>.json`, `value.json`, `frontrun.json`, `coverage.json`,
`out/japan_radar.xlsx` (one tab per view) and `out/db/*.json` (the documents the dashboard reads).
The dashboard page is `dashboard/japan_radar.html`.

## Views

| Key | View | What it shows |
|---|---|---|
| 1 | RADAR | Rated disclosures by category, top ideas, warnings |
| 2 | CHEAP | 不動産込みネットネット指数 ranking, cheapest first (same columns as the reference ranking) |
| 3 | VALUE | Net-nets, cash-rich, deep value, treasury piles, listed subsidiaries |
| 4 | PRE-DEAL | Probability-ranked buy-out candidates with the signals behind each |
| 5 | MY LIST | Every name in `data/watchlist.csv` and the reference ranking: surfaced or missed, and why |
| 6 | HELP | Stars, categories, formulas, priors |

Type a code (`4624`) or a name in the command line to open the security panel; `1`–`6` switch views.

## RE net-net index (CHEAP)

```
index = market cap / (net current assets + rental real estate at fair value + 50% x land not already counted)
net current assets = cash + trade receivables + securities + investment securities
                     - total liabilities - non-controlling interests            (inventories excluded)
```

Rental real estate comes from the 賃貸等不動産 note of the annual report (fair value incl. buildings);
the land add-on is `max(0, land book value - rental property book value) x 50%`. The formula reproduces
all 50 rows of the 2026-10-09 reference ranking (`data/reference_netnet_2026-10-09.csv`,
`tests/test_reference.py`), and the coverage check reports any reference or watchlist name the live
screen fails to surface, with the reason.

## Categories (same ids as Korea)

Special situations: tender offers & going-private (TOB, MBO, parent buy-out, self-tender, squeeze-out),
sale processes (press-report answers, special committees, strategic reviews), change of controlling
shareholder, mergers & share exchanges (implied value from the ratio), spin-offs, activists & 5% stakes
(incl. 公開買付けに準ずる行為 and takeover-defence fights), shareholder proposals, control lawsuits,
liquidations, reverse mergers, listing-criteria pressure (上場維持基準).
Returns: buybacks, cancellations, dividends, value-up plans. Business: guidance raised, big orders.
Assets: asset sales and hidden-asset gains. Warnings: distress, dilution, withdrawn deals.

## Stars

Ported from the Korean `rate()`: a base per category, then the size of the edge (spread to the bid,
buyback vs market cap, yield, gain vs market cap), +1 on a value list, −½/−1½ for thin trading
(¥15M / ¥3M a day), −1 for exchange designations, capped at 2 when the company fails the profit and
debt filters (special situations are priced on deal terms and skip that cap). Bases are the Korean
backtest's until a Japanese backtest replaces them.

## Data sources

| Data | Source | Key |
|---|---|---|
| Disclosures + PDFs | TDnet via webapi.yanoshin.jp (fallback: TDnet list pages) | none |
| Earnings-release balance sheets | TDnet XBRL (harvested every run) | none |
| Annual/semi-annual reports, rental RE note, major shareholders, 5% reports | EDINET API v2 | `EDINET_API_KEY` (free) |
| Listed universe | JPX list + EDINET code list (incl. Nagoya/Fukuoka/Sapporo-only) | none |
| Prices, market caps, daily trading value | Yahoo! Finance Japan | none |

Without an EDINET key the radar runs fully; the value screen covers the reference ranking, your
watchlist and every company whose earnings release has been harvested (no rental-property notes, so
their real-estate value is land × 50% only). Register at https://api.edinet-fsa.go.jp/ and export
`EDINET_API_KEY` for the whole market.

## Tests

```
python -m pytest -q
```
