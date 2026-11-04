# Rising Stocks Terminal

A calm, terminal-style tracker for Indian (NSE) growth stocks. It answers one question every day:
**which companies are really growing, and is the market starting to notice?**

- **Board** — every listed company scanned daily; labelled by backtested rules (leader / emerging / wait), with a
  funnel showing how ~2,700 listed companies narrow to the buy-list and the full rule set
- **Company snapshot** — what the company does (Screener's description) and five verdict meters on a Best → Critical
  scale, **computed from public data with no AI**: business (the growth check), sector outlook (its industry peers in
  our scan), war & oil risk (industry oil map + the company's own description + how far it fell on the market's
  worst days since 2023), before-buying checks (the safety radar) and overall. Research ratings, not buy/sell calls
- **How it was ranked** — on every stock: the four gates as connected cards with bullet bars (value vs threshold),
  what would change the label (price that breaks the trend, price that makes it a leader, sales needed next quarter),
  a waterfall of the ranking score, where the stock sits among all scanned stocks on each metric, and its label
  history. The panel runs the exact same rules (`rising.RULES`) as the scan — a test keeps them in sync
- **Growth story** for any stock — a 10-point plain-English growth check (sales every quarter, operating
  leverage, margins, cash vs profit, ROCE, debt, capacity build-up, promoter holding, receivables) plus
  interactive charts: revenue & profit with year-on-year growth on every bar, margins, cash vs profit,
  return on capital, capacity under construction, debt vs equity, shareholding
- **See it, don't read it** — every signal is a picture you can hover:
  - *safety radar*: 7 trap checks (NSE surveillance list, cash vs profit, receivables, real dilution, insider
    selling, core profit, liquidity/circuit-locks) as one shape — every dent is a named risk
  - *future growth*: a waterfall from today's sales to what the plants under construction could add, an
    order-book gauge (years of revenue already booked) and order wins
  - *margin breakout*: operating margin against its own 3-year range
  - *priced in?*: P/E through time vs its usual level, plus PEG
  - *smart money*: insider buys/sells marked on the price chart, ownership change each quarter
- **Themes** — treemap of industries by how many rising companies they hold, coloured by momentum
- **Compare** — 2–3 stocks on one growth/quality radar plus a sales "growth race"
- **Scoreboard** — how each label did in the backtest vs every other stock, and the live track record
- **Rules sandbox** — sliders for every threshold; today's board and the backtest re-run instantly with your
  rules (names that join/leave, median return per label, sample-size and over-fitting warnings)
- **Thesis tracker** — write why you bought; each quarter it shows whether that reason still holds
- **Desktop notifications** — stop hit, trigger crossed, red flag, results tomorrow (Windows)
- **Today** — your positions with a backtested exit rule, what changed since yesterday, results coming up
- **AI brief** (optional, your own key) — explains every chart in plain English and why the company is growing,
  using its NSE filings, red-flag announcements and news headlines. Pre-digested locally so a brief costs ~5k
  tokens (free tiers work) and is only regenerated when something changes. It explains; it never picks stocks
- **Bad-news alerts** — red-flag filings and negative headlines for your holdings and watchlist, daily, no AI

> **Not investment advice.** This is a personal research tool, not a SEBI-registered advisor. Labels come
> from a small backtest (2024–2026, mostly a rising market). Do your own research; markets can lose you money.

## Quick start

```bash
pip install -r requirements.txt
```

**Open it like an app (no terminal):** create a desktop shortcut once, then just double-click **Rising Stocks**.
It starts the app quietly and opens it in its own window; closing the window stops it.

```bash
python make_shortcut.py
```

Or double-click `launch.pyw` directly, or run it the developer way: `streamlit run app.py`.

Click **update data**. The first run builds a local database from public sources: ~1.5 years of NSE
end-of-day prices (~5 min) and the results of every liquid company (~1,500, about 25 min, once). After that a
daily update takes a minute or two — run it after market close (~7 pm IST), then read the **today** tab.

No API key is needed for market data. For the optional AI brief, open **settings** (top right), pick a provider
(Gemini, Anthropic, OpenAI, Groq, OpenRouter), paste your own key and press **test**.

## Keys and data — bring your own

| Data | Source | Key |
|---|---|---|
| prices, volume, delivery % | NSE daily bhavcopy (public archive) | none |
| listed companies, ISINs | NSE equity list | none |
| quarterly/annual results, balance sheet, cash flow, shareholding | Screener.in company pages | none |
| announcements, results calendar, industry lists | NSE public APIs / archives | none |
| AI brief (optional) | your LLM provider via litellm | `GEMINI_API_KEY` (yours) |

Everything is stored locally in `data/market.duckdb`. For longer price history (the backtests use ~3 years):

```bash
python data_sync.py backfill 1100
```

Your AI key is stored in the operating system's credential store (Windows Credential Manager / macOS Keychain /
Linux Secret Service) — never in a file, never in the repo, never shown again after saving (only its last 4
characters). Environment variables or a local `.env` (see `.env.example`) also work and take priority.
The app only listens on `localhost`, so other devices on your network can't open it.

Public sources are read on your machine, politely (one request at a time, with pauses): NSE announcements
and event calendar, NSE index lists, and Screener.in company pages. **No market data is stored in or
distributed with this repository.** Respect each source's terms of use; this tool is for personal research.

## How the labels work

| Label | Rule | Backtest, next 6 months (median) |
|---|---|---|
| leader | 2+ strong growth quarters, price near highs and above its 200-day average, already up >80% in a year | +25% (83% of picks up) |
| emerging | growth accelerating, margins up, price confirmed, up <80% in a year | +13% (rest of market: +2%) |
| wait | growth accelerating but price not confirmed — *trigger* = price to close above | −10% (watch only) |
| broke | dropped off the board since the last scan | |

Exit rule (tested against trailing stops and moving-average exits, which did worse): sell if price falls 20%
below your buy price; at 6 months keep only if still leader/emerging.

Reproduce the tests:

```bash
python rising.py backtest
```
```bash
python exit_backtest.py
```
```bash
python test_rules.py
```

## Files

| File | Does |
|---|---|
| `app.py` | the Streamlit UI |
| `rising.py` | daily update, scan, status rules, exit rule |
| `fundamentals.py` | full financials per stock + the growth checklist |
| `insights.py` | trap scores, capacity bridge, order book, margin band, P/E history, insider trades |
| `viz.py` | every chart builder + the palette |
| `brief.py` | AI brief that explains the charts; filings, red flags and news pre-digested locally (~3k input tokens), cached until inputs change; daily bad-news alerts |
| `growth_screen.py` | loads data, growth features, first backtest |
| `exit_backtest.py` | exit-rule backtest |
| `screener_refresh.py` | latest quarters / capex / industry for scanned names |
| `data_sync.py` | builds/updates the local market database from public sources |
| `settings.py` | AI provider/model; keys in the OS credential store |
| `launch.pyw` / `make_shortcut.py` | double-click app window / desktop shortcut |
| `config.py` | paths and the NSE session |
| `data/` | your scans, caches, positions and watchlist — local only, never committed |

## License

MIT — see `LICENSE`.
