"""Standalone market data: builds and updates data/market.duckdb from public NSE files + Screener.in.

  instruments        NSE equity list (symbol, ISIN, name) + market cap from Screener
  daily_market       NSE end-of-day "bhavcopy" (OHLC, volume, delivery %, turnover)
  quarterly_results  Screener.in quarterly results (via screener_refresh cache)
  corporate_filings  NSE corporate announcements

No API key needed. Usage:
  python data_sync.py update               # daily: new prices, filings (first run backfills ~1.5 years)
  python data_sync.py backfill 1100        # longer price history for the backtests
  python data_sync.py import-alphasniper   # one-time: copy history from an AlphaSniper DuckDB (ALPHASNIPER_DIR)
"""
import io
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from config import DATA, DB, nse_session

SCHEMA = """
create table if not exists instruments (isin varchar primary key, symbol varchar, company_name varchar,
    series varchar, sector varchar, market_cap_cr double, updated_at date);
create table if not exists daily_market (isin varchar, trade_date date, open double, high double, low double,
    close double, volume double, delivery_qty double, delivery_pct double, turnover_cr double, primary key (isin, trade_date));
create table if not exists quarterly_results (isin varchar, quarter_end date, sales_cr double, op_profit_cr double,
    opm_pct double, net_profit_cr double, primary key (isin, quarter_end));
create table if not exists corporate_filings (filing_id varchar primary key, isin varchar, symbol varchar,
    filing_datetime timestamp, category varchar, headline varchar);
"""
FIRST_RUN_DAYS = 550   # ~1.5 years: enough for 200-day averages, 1-year returns and the yearly comparison
PAUSE = 0.35           # seconds between NSE file downloads — be polite


MAX_DAILY_MOVE = .25  # NSE price bands cap normal moves at ~20%; anything bigger is a corporate action or a bad print


def adjust_prices(close):
    """Raw NSE closes (Series, or DataFrame dates x stocks) -> prices adjusted for splits, bonuses and one-day bad
    prints, in today's price units. NSE files are not adjusted, so a 2-for-1 split looks like a -50% crash.
    - a >25% move that reverses the next day is a bad print: that day is dropped
    - any other >25% move is a split/bonus/data break: earlier prices are scaled to match
    ponytail: rule-based, not NSE's corporate-action list; a genuine >25% day (rare: no-band F&O stocks) gets
    flattened. Upgrade path: apply exact ratios from /api/corporates-corporateActions."""
    r = close.pct_change(fill_method=None)
    spike = (r.abs() > MAX_DAILY_MOVE) & (((1 + r) * (1 + r.shift(-1)) - 1).abs() < .10)
    clean = close.mask(spike)
    r = clean.ffill(limit=5).pct_change(fill_method=None)
    r = r.mask(r.abs() > MAX_DAILY_MOVE, 0.0)
    growth = (1 + r.fillna(0)).cumprod().where(clean.notna())
    return growth * (clean.ffill().iloc[-1] / growth.ffill().iloc[-1])


def connect():
    con = duckdb.connect(str(DB))
    con.execute(SCHEMA)
    return con


def sync_instruments(con, s, log=print):
    r = s.get("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", timeout=30)
    r.raise_for_status()
    e = pd.read_csv(io.StringIO(r.text))
    e.columns = e.columns.str.strip()
    e = pd.DataFrame({"isin": e["ISIN NUMBER"].str.strip(), "symbol": e["SYMBOL"].str.strip(),
                      "company_name": e["NAME OF COMPANY"].str.strip(), "series": e["SERIES"].str.strip()})
    con.register("e", e)
    # keep market cap / sector already learned; update names, symbols (renames keep the same ISIN)
    con.execute("""insert into instruments (isin, symbol, company_name, series, updated_at)
                   select isin, symbol, company_name, series, current_date from e
                   on conflict (isin) do update set symbol = excluded.symbol, company_name = excluded.company_name,
                   series = excluded.series, updated_at = excluded.updated_at""")
    merged = merge_isin_changes(con, e)
    con.unregister("e")
    log(f"instruments: {len(e)} listed equities" + (f", {merged} ISIN changes merged" if merged else ""))


def merge_isin_changes(con, current):
    """A split / face-value change often gets a NEW ISIN for the same symbol. Move the old ISIN's price history and
    results onto the current ISIN (keeping the current one's rows where both exist) and retire the old ISIN, so the
    company keeps one continuous history. -> number of old ISINs merged."""
    con.register("cur", current[["isin", "symbol"]])
    pairs = con.execute("""select i.isin as old_isin, c.isin as new_isin from instruments i join cur c on i.symbol = c.symbol
                           where i.isin <> c.isin""").fetchall()
    con.unregister("cur")
    for old, new in pairs:
        con.execute("""insert or ignore into daily_market select ?, trade_date, open, high, low, close, volume, delivery_qty,
                       delivery_pct, turnover_cr from daily_market where isin = ?""", [new, old])
        con.execute("""insert or ignore into quarterly_results select ?, quarter_end, sales_cr, op_profit_cr, opm_pct,
                       net_profit_cr from quarterly_results where isin = ?""", [new, old])
        con.execute("update instruments set market_cap_cr = coalesce(market_cap_cr, (select market_cap_cr from instruments "
                    "where isin = ?)), sector = coalesce(sector, (select sector from instruments where isin = ?)) where isin = ?",
                    [old, old, new])
        for tbl in ("daily_market", "quarterly_results", "instruments"):
            con.execute(f"delete from {tbl} where isin = ?", [old])
    return len(pairs)


def bhavcopy(s, d):
    """-> DataFrame of one trading day, or None (holiday / not published yet)."""
    r = s.get(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv", timeout=30)
    if r.status_code in (403, 429) or r.status_code >= 500:  # blocked / NSE down: fail loudly, don't pass as "holiday"
        raise RuntimeError(f"NSE returned {r.status_code} for the {d:%d %b} price file — try again in a few minutes")
    if r.status_code != 200 or not r.text.startswith("SYMBOL"):
        return None
    b = pd.read_csv(io.StringIO(r.text), skipinitialspace=True)
    b.columns = b.columns.str.strip()
    b = b[b.SERIES.str.strip().isin(["EQ", "BE", "BZ", "SM", "ST"])]
    if len(b) and pd.to_datetime(str(b.DATE1.iloc[0]).strip(), format="%d-%b-%Y").date() != d:
        return None  # file is for another day: never store it under the wrong date
    num = lambda c: pd.to_numeric(b[c].astype(str).str.strip(), errors="coerce")
    return pd.DataFrame({"symbol": b.SYMBOL.str.strip(), "trade_date": d, "open": num("OPEN_PRICE"), "high": num("HIGH_PRICE"),
                         "low": num("LOW_PRICE"), "close": num("CLOSE_PRICE"), "volume": num("TTL_TRD_QNTY"),
                         "delivery_qty": num("DELIV_QTY"), "delivery_pct": num("DELIV_PER"),
                         "turnover_cr": num("TURNOVER_LACS") / 100})


def sync_prices(con, s, since, log=print):
    have = {r[0] for r in con.execute("select distinct trade_date from daily_market where trade_date >= ?", [since]).fetchall()}
    days = [since + timedelta(n) for n in range((date.today() - since).days + 1)]
    days = [d for d in days if d.weekday() < 5 and d not in have]
    added = 0
    for n, d in enumerate(days, 1):
        b = bhavcopy(s, d)
        if b is not None and len(b):
            con.register("b", b)
            con.execute("""insert or replace into daily_market
                           select i.isin, b.trade_date, b.open, b.high, b.low, b.close, b.volume, b.delivery_qty,
                                  b.delivery_pct, b.turnover_cr from b join instruments i using (symbol)
                           qualify row_number() over (partition by i.symbol order by i.updated_at desc nulls last) = 1""")
            con.unregister("b")
            added += 1
        if n % 50 == 0:
            log(f"prices: {n}/{len(days)} days checked, {added} trading days added")
        time.sleep(PAUSE)
    log(f"prices: {added} new trading days")


def sync_filings(con, s, days=7, log=print):
    rows, end = [], date.today()
    start = end - timedelta(days=days)
    while start <= end:
        stop = min(start + timedelta(days=6), end)
        r = s.get("https://www.nseindia.com/api/corporate-announcements?index=equities"
                  f"&from_date={start:%d-%m-%Y}&to_date={stop:%d-%m-%Y}", timeout=40)
        try:
            items = r.json() if r.status_code == 200 else []
        except ValueError:  # NSE sometimes answers with an HTML block page
            items = []
        for x in items if isinstance(items, list) else []:
            rows.append(dict(filing_id=str(x.get("seq_id") or f"{x.get('symbol')}_{x.get('an_dt')}"), isin=x.get("sm_isin"),
                             symbol=x.get("symbol"), filing_datetime=pd.to_datetime(x.get("an_dt"), format="%d-%b-%Y %H:%M:%S", errors="coerce"),
                             category=x.get("desc"), headline=str(x.get("attchmntText") or "")[:500]))
        start = stop + timedelta(days=1)
        time.sleep(PAUSE)
    if rows:
        f = pd.DataFrame(rows).drop_duplicates("filing_id")
        con.register("f", f)
        con.execute("insert or replace into corporate_filings select filing_id, isin, symbol, filing_datetime, category, headline from f")
        con.unregister("f")
    log(f"filings: {len(rows)} announcements in the last {days} days")


def load_screener(con, log=print):
    """Copy Screener quarterly results + market cap (cached by screener_refresh.py) into the DB."""
    qp, mp = DATA / "screener_q.csv", DATA / "screener_meta.csv"
    if qp.exists():
        q = pd.read_csv(qp, parse_dates=["quarter_end"])
        con.register("q", q)
        con.execute("""insert or replace into quarterly_results
                       select i.isin, q.quarter_end::date, q.sales_cr, q.op_profit_cr, q.opm_pct, q.net_profit_cr
                       from q join instruments i using (symbol) where q.sales_cr is not null""")
        con.unregister("q")
    if mp.exists():
        m = pd.read_csv(mp)
        if "mcap" in m:
            con.register("m", m[["symbol", "mcap", "sector"]])
            con.execute("""update instruments set market_cap_cr = m.mcap, sector = coalesce(m.sector, instruments.sector)
                           from m where instruments.symbol = m.symbol and m.mcap is not null""")
            con.unregister("m")
    n = con.execute("select count(distinct isin) from quarterly_results").fetchone()[0]
    log(f"fundamentals: {n} companies with quarterly results in the database")


def liquid_universe(con, min_turnover_cr=1.0):
    """Symbols worth fetching fundamentals for: median turnover >= ₹1 Cr/day over the last 60 sessions."""
    return [r[0] for r in con.execute("""
        with recent as (select isin, turnover_cr from daily_market
                        where trade_date >= (select max(trade_date) from daily_market) - 90)
        select i.symbol from recent join instruments i using (isin)
        group by i.symbol having median(turnover_cr) >= ? order by i.symbol""", [min_turnover_cr]).fetchall()]


def update(log=print):
    s = nse_session()
    with connect() as con:
        if con.execute("select count(*) from instruments").fetchone()[0] == 0 or date.today().weekday() == 0:
            sync_instruments(con, s, log)
        last = con.execute("select max(trade_date) from daily_market").fetchone()[0]
        sync_prices(con, s, (last + timedelta(1)) if last else date.today() - timedelta(FIRST_RUN_DAYS), log)
        sync_filings(con, s, 7 if last else 30, log)


def backfill(days, log=print):
    s = nse_session()
    with connect() as con:
        if con.execute("select count(*) from instruments").fetchone()[0] == 0:
            sync_instruments(con, s, log)
        sync_prices(con, s, date.today() - timedelta(int(days)), log)


def import_alphasniper(log=print):
    src = Path(os.getenv("ALPHASNIPER_DIR", "")) / "local_data" / "alpha_terminal.duckdb"
    if not src.exists():
        sys.exit(f"not found: {src} — set ALPHASNIPER_DIR in .env")
    with connect() as con:
        con.execute(f"attach '{src}' as a (read_only)")
        con.execute("""insert or replace into instruments (isin, symbol, company_name, sector, market_cap_cr, updated_at)
                       select isin, symbol, company_name, nullif(sector, 'Unknown'), market_cap_cr, current_date from a.instruments""")
        con.execute("insert or replace into daily_market select isin, trade_date, open, high, low, close, volume, delivery_qty, delivery_pct, turnover_cr from a.daily_market")
        con.execute("insert or replace into quarterly_results select isin, quarter_end, sales_cr, op_profit_cr, opm_pct, net_profit_cr from a.quarterly_results")
        con.execute("insert or replace into corporate_filings select filing_id, isin, symbol, filing_datetime, category, headline from a.corporate_filings")
        for t in ("instruments", "daily_market", "quarterly_results", "corporate_filings"):
            log(f"{t}: {con.execute(f'select count(*) from {t}').fetchone()[0]:,} rows")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "update"
    {"update": lambda: update(), "backfill": lambda: backfill(sys.argv[2] if len(sys.argv) > 2 else 1100),
     "import-alphasniper": import_alphasniper}[cmd]()
