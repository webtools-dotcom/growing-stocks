<div align="center">

# Rising Stocks Terminal

**A calm, evidence-first research terminal for Indian (NSE) growth stocks.**
Every evening it scans ~2,700 listed companies, finds the few whose business is genuinely accelerating
*and* whose price confirms it, and shows you — visually and transparently — exactly why each one made the list.

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![DuckDB](https://img.shields.io/badge/storage-DuckDB-FFF000?logo=duckdb&logoColor=black)](https://duckdb.org/)
[![Data](https://img.shields.io/badge/market%20data-public%20%C2%B7%20no%20keys-2ea44f)](#data-sources-and-privacy)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

![The board: leaders and emerging growers, ordered by steadiness](docs/screenshots/board.png)

</div>

---

## Why this exists

Most retail investors in India buy on a tip — a Telegram channel, a YouTube thumbnail, a friend — and then hold
and hope. The information needed to do better is public (NSE price files, quarterly results, filings, insider
trades), but it is scattered across a dozen sites and buried in tables.

**Rising Stocks Terminal** turns that public data into one daily answer:

> *Which companies are really growing right now — and is the market starting to notice?*

It is built around three principles:

| | |
|---|---|
| **Evidence, not opinion** | Every rule and every threshold is backtested on three years of point-in-time data, and the backtest ships with the app. When a rule didn't hold up, it was dropped. |
| **Transparent, not a black box** | Every stock shows the exact gates it passed, its value against each threshold, what would change its label, and how its score was built. The explanation runs the same code as the scan, and a test keeps them in sync. |
| **Yours, not ours** | Runs entirely on your machine. Market data needs no account or key. No data leaves your computer except the public requests needed to fetch it. |

> [!IMPORTANT]
> **This is a research tool, not investment advice.** It is not a SEBI-registered advisor and makes no buy/sell
> recommendations. Backtests describe the past, not the future. Markets can and do lose money.

---

## Contents

- [What you get](#what-you-get)
- [How it picks stocks](#how-it-picks-stocks)
- [The evidence](#the-evidence)
- [Quick start](#quick-start)
- [Daily workflow](#daily-workflow)
- [Data sources and privacy](#data-sources-and-privacy)
- [Optional AI brief](#optional-ai-brief)
- [Architecture](#architecture)
- [Reproduce the research](#reproduce-the-research)
- [Limitations](#limitations)
- [Contributing](#contributing)

---

## What you get

### The board — the whole market, narrowed to what matters

Every listed company is scanned after market close and labelled by backtested rules. A funnel shows how ~2,700
listed companies narrow to a buy-list of ~100, with the full rule set one click away. Filters for status,
industry, price (for small accounts) and one-off "lumpy" results.

| Label | Meaning |
|---|---|
| **leader** | Growing strongly, price near highs and above its 200-day average, already up 80–150% in a year |
| **emerging** | Growth accelerating and margins widening, price confirmed, up less than 80% in a year |
| **wait** | Growth is there but the price hasn't confirmed — the *trigger* is the price it must close above |
| **hot** | Passes growth and trend, but already up more than 150% in a year — shown, deliberately **not** a buy |
| **broke** | Dropped off the board since the last scan |

### The stock page — one company, fully explained

![Company snapshot: what the company does and five verdict meters](docs/screenshots/stock.png)

- **Company snapshot** — what the company actually does, plus five verdicts on a Best → Critical scale:
  *business*, *sector outlook*, *war & oil risk*, *before buying*, and *overall*. Each comes with a one-line,
  measured reason, e.g. *"fell 1.1× the market on its 12 worst days"*. These rule-based verdicts are free and
  always present; if you add an AI key, the AI's own verdicts sit beside them and a **≠** flags any disagreement.
- **Price chart** with range buttons and **insider buys/sells marked on the chart**.

### How it was ranked — no black box

![The four gates, each test as a bar against its threshold, and what would change the label](docs/screenshots/ranked.png)

On every stock, the four gates the scan applies — **growth → acceleration → trend → run-up** — appear as connected
cards, each test drawn as a bar against its threshold. Below them:

- **What would change it** — the exact price that breaks the trend, the price that turns an emerging stock into a
  leader (or a leader into *hot*), and the sales and profit the next quarter must beat.
- **Steadiness score waterfall** — how the score that orders the board was built, part by part.
- **Crowd strips** — where this stock sits among every scanned stock on each metric.
- **Label history** — when it carried which label, at what price.
- **Not scanned?** — the entry conditions it failed, with its numbers.

### Growth story — fundamentals as pictures

![Growth check, safety radar and fundamentals charts](docs/screenshots/growth.png)

- **10-point growth check** in plain English: sales every quarter, operating leverage, margins, cash vs profit,
  return on capital, debt, capacity build-up, promoter holding, receivables.
- **Safety radar** — seven trap checks as one shape (NSE surveillance list, cash backing profit, receivables, real
  dilution, insider selling, core profit, liquidity). Every dent is a named risk.
- **Future growth** — a waterfall from today's sales to what plants under construction could add, plus an
  order-book gauge (years of revenue already booked).
- **Priced in?** — P/E through time against its own usual range, plus PEG.
- **Owners** — promoter, FII and fund holding changes each quarter.
- Revenue and profit with year-on-year growth on every bar, margins against their 3-year band, cash flow, debt.

### Compare, themes, scoreboard and the rules sandbox

![Compare up to three stocks on one growth/quality radar](docs/screenshots/compare.png)

- **Compare** — two or three stocks on one growth/quality radar, a side-by-side table, and a quarterly sales
  "growth race".
- **Themes** — a treemap of industries by how many rising companies they hold, coloured by 3-month momentum.
  Many rising names in one industry usually means a real tailwind.
- **Scoreboard** — how each label performed in the backtest against every other stock, plus the live track record.
- **Rules sandbox** — a slider for every threshold. Move one and both today's board and the three-year backtest
  re-run instantly: which names join or leave, the median return per label, and warnings when a sample gets too
  small to trust.

![Scoreboard and rules sandbox](docs/screenshots/scoreboard.png)

### Your positions — with an exit rule that was tested

The **today** tab tracks what you hold against a backtested exit rule: **sell if the price falls 20% below your
buy price; at six months keep only if it is still a leader or emerging**. It also shows what changed on the board
since yesterday, results due this week, and red-flag filings or negative headlines for your holdings — all
without AI. A **thesis tracker** stores why you bought and shows each quarter whether that reason still holds.
On Windows, desktop notifications fire when a stop is hit, a trigger is crossed or a red flag appears.

---

## How it picks stocks

The scan is a sequence of gates. A company must pass each one, using only results already public and prices up
to that day.

```mermaid
flowchart LR
    A["~2,700 listed<br/>companies"] --> B["Recent results<br/>+ profitable, liquid<br/>(sales ≥ ₹25 Cr/qtr,<br/>₹1 Cr+ traded/day)"]
    B --> C{"Growth<br/>2 of last 3 qtrs: sales & profit +15% YoY<br/>latest: sales +20%, profit +25%<br/>sales up QoQ in 2 of 3"}
    C --> E{"Trend<br/>above 200-day average<br/>within 15% of 52-week high"}
    E --> F{"Run-up<br/>1-year price rise"}
    F -->|"< 80%"| D{"Acceleration<br/>growth faster than 2 qtrs ago<br/>or margins widening"}
    D --> G["EMERGING"]
    F -->|"80–150%"| H["LEADER"]
    F -->|"> 150%"| I["HOT · not a buy"]
    E -.->|"not confirmed yet,<br/>growth accelerating"| J["WAIT · trigger price"]
```

Within each label, stocks are ordered by a **steadiness score** (0–100), four equal parts, each a rank among board
stocks:

| Part | Why it is there |
|---|---|
| **Size** | Small caps fell hardest in the 2024–25 correction |
| **Calm price** | Stocks with smaller daily swings hit the −20% stop far less often |
| **Accumulation** | Delivery % rising (shares bought to keep, not traded intraday) |
| **Margin change** | Widening operating margins vs a year ago |

Every threshold lives in one place (`rising.RULES`). The scan, the backtests, the "how it was ranked" panel and the
rules sandbox all read it, so what you are shown can never drift from what actually decides a label.

---

## The evidence

All numbers below come from a **point-in-time study of 36 monthly entry dates (June 2023 – May 2026)**, covering
a strong rally (the typical stock +16% over six months), the 2024–25 small-cap fall, and the recovery. Each month
uses only results public by then (60-day lag) and prices up to that day. Prices are split-adjusted, and one-day
bad prints are removed.

**Labels — median return over the next six months** (scoreboard backtest, every results season):

| Label | Median 6-month return | Picks that rose |
|---|---:|---:|
| leader | **+14.1%** | 62% |
| emerging | **+10.3%** | 71% |
| hot | +7.3% | 68% (worst 10%: −12% or lower) |
| wait | +0.7% | 55% |
| every other profitable, liquid stock | +4.0% | 55% |

**Ordering — top 15 growers each month, return in excess of the typical stock:**

| Ranking method | Excess return | Picks that rose | Hit the −20% stop | Months it beat the market |
|---|---:|---:|---:|---:|
| **Steadiness score** (current) | **+9.9%** | **64%** | **20%** | **86%** |
| Fastest sales and profit growth (previous) | +5.2% | 55% | 34% | 59% |
| Random 15 growers (average of 50 draws) | +5.9% | 55% | 32% | 65% |

Two findings changed the app:

1. **Ranking by the biggest growth numbers did no better than chance.** Huge jumps are often one-offs or low-base
   effects. Steadiness nearly doubled the edge and cut stop-outs from about a third to a fifth, and it held in both
   the falling and the recovering market.
2. **Leaders that had already run more than 150% were the trap.** Capping LEADER there lifted its excess return
   from +4.9% to +8.2% and cut stop-outs from 40% to 32%. They now carry their own **hot** label instead of
   disappearing silently.

Ideas that were tested and **dropped** because they did not survive the longer history: excluding growth spikes
above 100%, a hard volatility filter, a market-breadth regime switch, and momentum as a ranking factor (it worked
in rising markets and lost money in the 2024–25 fall).

**Exit rule:** a 20% stop plus a 6-month review kept almost all of the return of a plain 6-month hold while
cutting the worst trade from about −45% to about −24%. Trailing stops and moving-average exits did worse.

---

## Quick start

**Requirements:** Python 3.11 or newer (developed on 3.14), about 1 GB of free disk space, and an internet connection.

```bash
git clone https://github.com/webtools-dotcom/growing-stocks.git
```

```bash
cd growing-stocks
```

```bash
pip install -r requirements.txt
```

**Open it like an app (no terminal):** create a desktop shortcut once, then double-click **Rising Stocks**. It
starts quietly and opens in its own window; closing the window stops it.

```bash
python make_shortcut.py
```

Or double-click `launch.pyw`, or run it the developer way:

```bash
streamlit run app.py
```

Then click **update data**. The first run builds a local database from public sources: about 1.5 years of NSE
end-of-day prices and the results of every liquid company (~1,500) — roughly 30 minutes, once. After that a daily
update takes a minute or two.

For the full three-year backtests, fetch more price history (about 30–40 minutes, once):

```bash
python data_sync.py backfill 1650
```

---

## Daily workflow

1. **After 7 pm IST** (when NSE publishes the day's file), click **update data** — or schedule `daily_update.bat`
   in Windows Task Scheduler. The update runs on its own, so you can close or reload the page while it works.
2. Open **today** — positions against the exit rule, what changed on the board, results this week, red flags.
3. Open **board** — new leaders and emerging names, best-ordered first.
4. Open any name on the **stock** page — read the snapshot, check how it was ranked, look at the growth story.
5. Write down **why** you bought in the thesis tracker. The app checks it every quarter.

---

## Data sources and privacy

| Data | Source | Key needed |
|---|---|---|
| Prices, volume, delivery % | NSE daily bhavcopy (public archive) | none |
| Listed companies, ISINs, ISIN changes | NSE equity list | none |
| Quarterly and annual results, balance sheet, cash flow, shareholding | Screener.in company pages | none |
| Announcements, results calendar, insider trades, surveillance lists, industries | NSE public APIs and archives | none |
| News headlines for red-flag alerts | Google News RSS | none |
| AI brief (optional) | your own LLM provider via litellm | yours |

- Everything is stored locally in `data/` (a DuckDB database plus small cache files). **No market data is stored
  in or distributed with this repository.**
- Public sources are read politely: one request at a time, with pauses. Respect each source's terms of use; this
  tool is for personal research.
- The app listens only on `localhost`, so other devices on your network can't open it.
- Data files are written crash-safely (temp file, then swap), so a power cut mid-update can't corrupt them, and
  updates wait their turn for the database instead of failing when two things touch it at once.

---

## Optional AI brief

Market data never needs a key. If you want plain-English explanations, open **settings** (top right), pick a
provider — **Gemini, Anthropic, OpenAI, Groq or OpenRouter** — paste your own key and press **test**.

- The brief reads the company's NSE filings (PDFs), red-flag announcements, recent news and the app's own measured
  facts, then explains the charts, why the company is growing, and what could go wrong — with sources.
- Inputs are digested locally first, so a brief costs about 5,000 tokens (free tiers work), and it is cached until
  something actually changes.
- It explains; it never picks stocks.
- Your key is stored in the operating system's credential store (Windows Credential Manager, macOS Keychain or
  Linux Secret Service) — never in a file, never in the repo, and never shown again after saving. Environment
  variables or a local `.env` (see `.env.example`) also work.

---

## Architecture

```mermaid
flowchart TB
    subgraph sources["Public sources"]
        NSE["NSE archives & APIs<br/>prices · filings · insider trades · calendar"]
        SCR["Screener.in<br/>results · balance sheet · shareholding"]
        NEWS["Google News RSS"]
    end
    subgraph local["Your machine"]
        SYNC["data_sync.py<br/>prices, ISIN changes, filings"] --> DB[("data/market.duckdb")]
        SREF["screener_refresh.py<br/>new results"] --> DB
        DB --> GS["growth_screen.py<br/>point-in-time features"]
        GS --> SCAN["rising.py<br/>RULES · classify · steadiness · scan · backtest"]
        SCAN --> FILES["data/*.csv · *.json<br/>board · history · funnel · scoreboard"]
        FILES --> APP["app.py (Streamlit)<br/>board · stock · compare · themes · scoreboard · sandbox"]
        FUND["fundamentals.py / insights.py<br/>growth check · safety radar · order book · P/E"] --> APP
        BRIEF["brief.py<br/>optional AI brief · red-flag alerts"] --> APP
        VIZ["viz.py<br/>every chart"] --> APP
    end
    NSE --> SYNC
    SCR --> SREF
    SCR --> FUND
    NEWS --> BRIEF
```

| File | Does |
|---|---|
| `app.py` | The Streamlit UI |
| `rising.py` | Rules, labels, the "how it was ranked" explainer, daily update and scan, scoreboard backtest, exit rule, notifications |
| `growth_screen.py` | Loads the database; point-in-time growth, trend, volatility and accumulation features |
| `data_sync.py` | Builds and updates the local database from NSE; split adjustment, ISIN-change merging, history backfill |
| `screener_refresh.py` | Latest quarters, capex and industry for scanned companies |
| `fundamentals.py` | Full financials per stock and the 10-point growth check |
| `insights.py` | Safety radar, capacity bridge, order book, margin band, P/E history, insider trades, rule-based verdicts |
| `brief.py` | Optional AI brief and daily red-flag alerts |
| `viz.py` | Every chart builder and the colour palette |
| `research.py` | The 36-month study behind the labels and the steadiness score |
| `exit_backtest.py` | The exit-rule backtest |
| `test_rules.py` | Self-checks for the rules, the explainer, price adjustment, the freshness banner and update tracking |
| `config.py` / `safeio.py` / `settings.py` | Paths and database access, crash-safe file I/O, AI provider and key storage |
| `launch.pyw` / `make_shortcut.py` / `daily_update.bat` | App window, desktop shortcut, scheduled update |

---

## Reproduce the research

Every number in [The evidence](#the-evidence) can be regenerated on your own machine.

```bash
python research.py
```

```bash
python rising.py backtest
```

```bash
python exit_backtest.py
```

```bash
python test_rules.py
```

`research.py` builds the point-in-time panel (36 monthly entries, about 30 candidate signals, the next six months'
return and worst drawdown for every stock), then compares rule sets and ranking methods across three separate
periods. The rules sandbox in the app re-tests any threshold you like in seconds.

---

## Limitations

Honest caveats, so you can weigh the evidence properly:

- **Survivorship bias.** The study uses today's listings, so companies delisted since 2023 are missing. That
  flatters every result a little.
- **Three years is one market cycle.** The period includes a rally, a fall and a recovery, but not a long bear
  market.
- **Historic market cap is estimated** from today's, scaled by price, so share issuance in between is ignored.
- **Results timing is approximated** with a 60-day lag after quarter end rather than exact filing dates.
- **Long-run consistency** (e.g. growth in 4 of the last 6 quarters) couldn't be tested properly yet: too few
  months have ten quarters of history. It will be revisited as data accumulates.
- Desktop notifications and the one-click launcher are Windows-only; the app itself runs anywhere Python does.

---

## Contributing

Issues and pull requests are welcome. Good contributions:

- New signals — with a point-in-time backtest in `research.py` showing they hold in more than one market phase.
- Better visualisations of existing data.
- Data-source robustness (NSE changes its endpoints from time to time).

Please run `python test_rules.py` before opening a pull request, and never commit anything from `data/` or any key.

---

## License

MIT — see [LICENSE](LICENSE). Not affiliated with NSE, Screener.in or any broker.
