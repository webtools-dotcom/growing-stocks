"""Daily 'rising stocks' scan across the whole NSE universe.

Status per stock (rules come from the growth_screen.py backtest):
  LEADER       growth strong, price trend strong, already ran >80% in a year (best 6m results in backtest)
  EMERGING     growth accelerating, price confirmed (above 200DMA, within 15% of high), ran <80%
  WAIT         growth accelerating but price NOT confirmed yet -> trigger = price to close above
  BROKE        was on the board last scan, no longer qualifies today

Usage:
  python rising.py update   # sync NSE prices+filings, refresh Screener for new reporters, scan
  python rising.py scan     # scan only, from cached data
"""
import io
import os
import sys
from datetime import date, timedelta
import duckdb
import pandas as pd

import settings
from config import DATA, DB, nse_session

BOARD, HISTORY = DATA / "rising_latest.csv", DATA / "rising_history.csv"


def classify(f):
    """Status rules — shared by the daily scan and `python rising.py backtest`."""
    growth = (f.streak >= 2) & (f.sales_yoy > .20) & (f.pat_yoy > .25) & (f.seq_ups >= 2)
    accel = (f.sales_yoy > f.sales_yoy_2) | (f.opm_chg > 0)
    trend = f.above_200 & (f.off_high > .85)
    s = pd.Series(None, index=f.index, dtype=object)
    s[growth & trend & (f.ret1y >= .8)] = "LEADER"
    s[growth & accel & trend & (f.ret1y < .8)] = "EMERGING"
    s[growth & accel & ~trend & (f.off_high > .6) & (f.ret1y < .8) & s.isna()] = "WAIT"
    return s


# Exit rule chosen by exit_backtest.py (Sep-2025 + Dec-2025 entry seasons, 90 trades, split-adjusted prices):
# a 20% stop from entry + a review at 6 months kept most of the return (median +16% vs +17% for
# plain 6m hold) while cutting the worst trade from -45% to -24%. Trailing stops and 50/200-DMA
# exits did worse. "Growth broke" did not improve results, so it is a warning, not an exit.
STOP_PCT = .20
REVIEW_DAYS = 182  # calendar days, ~126 trading days


def position_alerts(buy_price, buy_date, last_close, today, status, latest_q=None):
    """-> list of (level, message) for one holding. latest_q: dict with quarter_end, sales_yoy, pat_yoy."""
    out = []
    if last_close <= buy_price * (1 - STOP_PCT):
        out.append(("EXIT", f"Stop hit: {last_close / buy_price - 1:+.0%} from buy (rule: -{STOP_PCT:.0%})"))
    held = (pd.Timestamp(today) - pd.Timestamp(buy_date)).days
    if held >= REVIEW_DAYS:
        keep = status in ("LEADER", "EMERGING")
        out.append(("EXIT" if not keep else "REVIEW",
                    f"6-month review ({held}d held): {'still ' + status + ' - ok to keep' if keep else 'no longer on the board - exit'}"))
    if latest_q and pd.Timestamp(latest_q["quarter_end"]) > pd.Timestamp(buy_date) - pd.Timedelta(days=60):
        if latest_q["sales_yoy"] < .15 or latest_q["pat_yoy"] < .15:
            out.append(("WARN", f"Growth slowed in {pd.Timestamp(latest_q['quarter_end']):%b %Y} quarter "
                                f"(sales {latest_q['sales_yoy']:+.0%}, profit {latest_q['pat_yoy']:+.0%})"))
    return out


def results_calendar():
    """Upcoming board meetings for financial results, from NSE's event calendar."""
    r = nse_session().get("https://www.nseindia.com/api/event-calendar?index=equities", timeout=20)
    cal = pd.DataFrame(r.json())
    cal = cal[cal.purpose.str.contains("Result", case=False, na=False)]
    cal["date"] = pd.to_datetime(cal.date, format="%d-%b-%Y").dt.date
    cal[["symbol", "date", "purpose"]].to_csv(DATA / "results_calendar.csv", index=False)
    return len(cal)


def backtest():
    """Forward 6m returns of each status bucket at every past results season (no look-ahead)."""
    import growth_screen as g
    rows = []
    for q in pd.date_range("2024-12-31", "2026-06-30", freq="QE"):
        on = q + pd.Timedelta(days=g.LAG_DAYS)
        f = g.features(q, on)
        f["status"] = classify(f)
        for st in ["LEADER", "EMERGING", "WAIT", None]:
            sel = f[f.status.isna()] if st is None else f[f.status == st]
            if sel.empty:
                continue
            r, held = g.fwd(sel)
            rows.append(dict(on=on.date(), status=st or "everything else", n=len(sel), held=held,
                             median=r.median(), hit=(r > 0).mean(), p10=r.quantile(.1)))
    d = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(d.assign(**{c: d[c].map("{:+.0%}".format) for c in ["median", "hit", "p10"]}).to_string(index=False))
    full = d[d.held >= g.HOLD - 5]
    print("\nCompleted 6m windows, averaged across seasons:")
    print(full.groupby("status")[["n", "median", "hit", "p10"]].mean()
          .assign(n=lambda x: x.n.round(0)).to_string(float_format="{:+.1%}".format))


def scan():
    import growth_screen as g  # loads DB + Screener cache into memory

    on = g.close.index[-1]
    latest = g.qr.groupby("isin").quarter_end.max()
    parts = []
    for q, ids in latest.groupby(latest):
        if q < on - pd.Timedelta(days=200):  # no result in ~2 quarters: stale, skip
            continue
        f = g.features(q, on)
        parts.append(f[f.index.isin(ids.index)].assign(qtr=q.date()))
    f = pd.concat(parts).join(g.inst.set_index("isin")[["symbol", "company_name", "market_cap_cr"]])

    i = len(g.adj) - 1
    hist = g.adj.iloc[max(0, i - 250): i + 1]
    f["dma200"] = (g.close.iloc[i] * hist.tail(200).mean() / g.adj.iloc[i]).reindex(f.index)  # in today's price units
    f["high52"] = (g.close.iloc[i] / f.off_high.reindex(g.close.columns)).reindex(f.index)
    f["ret3m"] = (g.adj.iloc[i] / g.adj.iloc[max(0, i - 63)] - 1).reindex(f.index)
    f["chg1d"] = (g.adj.iloc[i] / g.adj.iloc[i - 1] - 1).reindex(f.index)

    f["status"] = classify(f)
    # lumpy = order-driven/one-off sales (best of last 4 quarters > 2x worst); growth may not repeat
    last4 = g.qr[g.qr.quarter_end > g.qr.groupby("isin").quarter_end.transform("max") - pd.Timedelta(days=300)]
    sl = last4.groupby("isin").sales_cr
    f["lumpy"] = (sl.max() / sl.min().clip(lower=1) > 2).reindex(f.index).fillna(False)
    f["trigger"] = pd.concat([f.dma200, .85 * f.high52], axis=1).max(axis=1).where(f.status == "WAIT")

    b = f[f.status.notna()].copy()
    b["score"] = g.growth_score(b) + b.ret3m.rank(pct=True) * .5 - b.lumpy * 1.0
    b = b.join(industries(), on="symbol")
    meta = pd.read_csv(DATA / "screener_meta.csv") if (DATA / "screener_meta.csv").exists() else pd.DataFrame(columns=["symbol"])
    b = b.join(meta.set_index("symbol")[[c for c in ("pe", "roce", "capex") if c in meta]], on="symbol")
    b["asof"] = on.date()

    prev = pd.read_csv(BOARD) if BOARD.exists() else pd.DataFrame(columns=["symbol", "status", "asof"])
    prev = prev[prev.status != "BROKE"]
    b["new"] = ~b.symbol.isin(prev.symbol)
    b["prev_status"] = b.symbol.map(prev.set_index("symbol").status)
    gone = prev[~prev.symbol.isin(b.symbol)]
    broke = pd.DataFrame({"symbol": gone.symbol, "status": "BROKE", "prev_status": gone.status, "asof": on.date(), "new": True})
    if len(broke):
        cur = f.set_index("symbol")
        for c in ["company_name", "close", "qtr", "sales_yoy", "pat_yoy", "off_high", "above_200", "ret1y"]:
            broke[c] = broke.symbol.map(cur[c]) if c in cur else None
    out = pd.concat([b.reset_index(drop=True), broke], ignore_index=True)
    order = {"LEADER": 0, "EMERGING": 1, "WAIT": 2, "BROKE": 3}
    out = out.sort_values(["status", "score"], key=lambda s: s.map(order) if s.name == "status" else -s.fillna(0))
    out.to_csv(BOARD, index=False)
    h = out[["asof", "symbol", "status", "close", "score"]]
    if HISTORY.exists():
        old = pd.read_csv(HISTORY)
        h = pd.concat([old[old["asof"] != str(on.date())], h])
    h.to_csv(HISTORY, index=False)
    print(f"{on.date()}: " + ", ".join(f"{k} {v}" for k, v in out.status.value_counts().items()))
    return out


def refresh_industries():
    """NSE index constituent lists carry an industry per symbol (~1,000 stocks). Fetched, never committed."""
    s = nse_session()
    parts = []
    for name in ("ind_niftytotalmarket_list", "ind_niftymicrocap250_list"):
        r = s.get(f"https://nsearchives.nseindia.com/content/indices/{name}.csv", timeout=20)
        if r.ok and r.text.startswith("Company Name"):
            parts.append(pd.read_csv(io.StringIO(r.text))[["Symbol", "Industry"]])
    if parts:
        pd.concat(parts).drop_duplicates("Symbol").set_axis(["symbol", "industry"], axis=1).to_csv(DATA / "industry.csv", index=False)


def industries():
    """symbol -> industry, NSE index lists first, Screener's industry as fallback."""
    p = DATA / "industry.csv"
    nse = pd.read_csv(p).set_index("symbol").industry if p.exists() else pd.Series(dtype=object)
    m = DATA / "screener_meta.csv"
    if m.exists():
        sm = pd.read_csv(m).set_index("symbol")
        nse = nse.combine_first(sm.sector.dropna())
        return pd.DataFrame({"industry": nse, "sub_industry": sm.industry})
    return pd.DataFrame({"industry": nse})


def update():
    import data_sync
    from screener_refresh import refresh
    print("1/3 NSE end-of-day prices + filings...")
    data_sync.update()
    print("2/3 Screener: fundamentals for new results / unseen companies...")
    con = duckdb.connect(str(DB), read_only=True)
    # liquid companies with no result in the DB for ~2 quarters (all of them on a fresh install)
    have_recent = {r[0] for r in con.execute("""select i.symbol from quarterly_results q join instruments i using (isin)
                                                group by 1 having max(q.quarter_end) >= current_date - 200""").fetchall()}
    universe = set(data_sync.liquid_universe(con)) - have_recent
    since = date.today() - timedelta(days=4)
    rep = con.execute("""select symbol, max(cast(filing_datetime as date)) filed from corporate_filings
        where filing_datetime >= ? and (category = 'Outcome of Board Meeting' or category ilike '%financial result%')
        group by 1""", [since]).df()
    con.close()
    board = set(pd.read_csv(BOARD).symbol) if BOARD.exists() else set()
    meta = DATA / "screener_meta.csv"
    stale = set()
    reporters = set(rep.symbol)
    if meta.exists():  # board members not re-checked for 7 days
        m = pd.read_csv(meta)
        # skip reporters already fetched after they filed (fetched date only, so same-day filings get one re-check)
        fetched = pd.to_datetime(m.set_index("symbol").fetched)
        rep["fetched"] = rep.symbol.map(fetched)
        reporters = set(rep[rep.fetched.isna() | (rep.fetched <= pd.to_datetime(rep.filed))].symbol)
        stale = set(m[pd.to_datetime(m.fetched) < pd.Timestamp(date.today() - timedelta(days=7))].symbol)
        unseen = (board | universe) - set(m.symbol)
    else:
        unseen = board | universe
    todo = sorted(reporters | (board & stale) | unseen)
    print(f"   {len(todo)} symbols to refresh" + (" — first run fetches every liquid company, ~1 s each" if len(todo) > 300 else ""))
    refresh(todo)
    with data_sync.connect() as con:
        data_sync.load_screener(con)
    try:  # calendar is a nice-to-have; never block the scan on it
        print(f"   results calendar: {results_calendar()} upcoming result meetings")
    except Exception as e:
        print(f"   results calendar failed: {e}")
    ind = DATA / "industry.csv"
    if not ind.exists() or (pd.Timestamp.now() - pd.Timestamp(ind.stat().st_mtime, unit="s")).days > 30:
        try:
            refresh_industries()
        except Exception as e:
            print(f"   industry list failed: {e}")
    print("3/3 scan")
    out = scan()
    pos = DATA / "positions.csv"
    held = list(pd.read_csv(pos).symbol) if pos.exists() else []
    watch = (DATA / "watchlist.txt").read_text().split() if (DATA / "watchlist.txt").exists() else []
    if held or watch:  # bad-news check: red-flag filings + negative headlines, no AI tokens
        from brief import alerts
        con = duckdb.connect(str(DB), read_only=True)
        names = dict(con.execute("select symbol, company_name from instruments").fetchall())
        con.close()
        found = alerts(list(dict.fromkeys(held + watch)), names)
        print(f"   alerts: {sum(map(len, found.values()))} red flags / negative headlines in the last 14 days"
              + (f" ({', '.join(found)})" if found else ""))
    if pos.exists() and settings.ai_ready():
        from brief import brief  # only calls the LLM when a holding filed something new
        for s in pd.read_csv(pos).symbol:
            try:
                brief(s, status=out.set_index("symbol").status.get(s, ""))
            except Exception as e:
                print(f"   brief {s} failed: {e}")
    return out


if __name__ == "__main__":
    {"update": update, "scan": scan, "backtest": backtest}[sys.argv[1] if len(sys.argv) > 1 else "scan"]()
