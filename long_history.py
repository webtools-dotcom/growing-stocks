"""Older daily prices (2006 onwards) from the Upstox historical-candle API, so the breakouts tab can see bases that
started before Rising's own NSE history (May 2022). One-time job; the daily update doesn't need it.

    python long_history.py                 # fetch from Upstox (needs UPSTOX_ACCESS_TOKEN in .env), ~1.5 hours
    python long_history.py import FILE     # or copy from a duckdb file that has a `candles` table (breakout lab)
    python long_history.py status

Saves after every stock and skips what it already has, so it can be stopped and restarted any time.
Upstox prices are split/bonus-adjusted as of the day they were fetched; breakouts.daily() rescales them onto Rising's
own adjusted prices using the overlap, so later splits don't break the join."""
import os
import sys
import time
from datetime import date, timedelta

import pandas as pd

from config import open_db

URL = "https://api.upstox.com/v3/historical-candle/{key}/days/1/{to}/{frm}"
START = date(2006, 1, 1)
OVERLAP = 60  # days past Rising's first date, for the stitch
GAP = 0.95  # seconds between requests: under Upstox's ~2,000 per 30 minutes
SCHEMA = """
create table if not exists long_daily (isin varchar, date date, open double, high double, low double, close double,
                                       volume double, primary key (isin, date));
create table if not exists long_fetched (isin varchar primary key, rows_ integer, fetched_at timestamp);
"""


def _end(con):
    return con.execute("select min(trade_date) from daily_market").fetchone()[0] + timedelta(days=OVERLAP)


def _windows(end):
    """Upstox serves at most ten years of daily candles per request."""
    out, to = [], end
    while to > START:
        frm = max(START, to.replace(year=to.year - 10) + timedelta(days=1))
        out.append((frm, to))
        to = frm - timedelta(days=1)
    return out


def _fetch(session, isin, frm, to):
    import requests
    key = requests.utils.quote(f"NSE_EQ|{isin}", safe="")
    for attempt in range(4):
        try:
            r = session.get(URL.format(key=key, to=to, frm=frm), timeout=30)
        except (requests.Timeout, requests.ConnectionError):
            time.sleep(10 * (attempt + 1))
            continue
        if r.status_code == 429:
            time.sleep(20 * (attempt + 1))
            continue
        if r.status_code == 401:
            raise RuntimeError("Upstox token rejected (expired?) — renew UPSTOX_ACCESS_TOKEN in .env")
        if r.status_code in (400, 404):
            return pd.DataFrame()
        if r.status_code != 200:
            raise RuntimeError(f"Upstox returned {r.status_code}")
        c = pd.DataFrame(r.json().get("data", {}).get("candles", []), columns=["ts", "open", "high", "low", "close", "volume", "oi"])
        if c.empty:
            return c
        c["date"] = pd.to_datetime(c.ts.str[:10]).dt.date
        return c.assign(isin=isin)[["isin", "date", "open", "high", "low", "close", "volume"]]
    return None  # network trouble: left unmarked so the next run retries it


def _store(isin, c):
    con = open_db(read_only=False)
    try:
        if len(c):
            con.register("c", c)
            con.execute("insert or replace into long_daily select * from c")
            con.unregister("c")
        con.execute("insert or replace into long_fetched values (?, ?, current_timestamp)", [isin, len(c)])
    finally:
        con.close()


def run(log=print):
    import requests
    token = os.getenv("UPSTOX_ACCESS_TOKEN")
    if not token:
        raise SystemExit("set UPSTOX_ACCESS_TOKEN in .env (an Upstox analytics token works)")
    con = open_db(read_only=False)
    con.execute(SCHEMA)
    end = _end(con)
    todo = [r[0] for r in con.execute("""select isin from instruments where isin like 'INE%'
                                         and isin not in (select isin from long_fetched) order by symbol""").fetchall()]
    con.close()
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
    log(f"long history: {len(todo)} stocks to fetch, {START} to {end}")
    t0 = time.time()
    for n, isin in enumerate(todo, 1):
        parts = []
        for frm, to in _windows(end):
            tick = time.time()
            c = _fetch(s, isin, frm, to)
            time.sleep(max(0, GAP - (time.time() - tick)))
            if c is None or c.empty:  # nothing older than this window either (or a network failure)
                break
            parts.append(c)
        if c is None:
            log(f"   {isin}: network trouble, skipped for now")
            continue
        _store(isin, pd.concat(parts) if parts else pd.DataFrame())
        if n % 100 == 0:
            log(f"   {n}/{len(todo)} · about {(time.time() - t0) / n * (len(todo) - n) / 60:.0f} min left")
    log(status())


def import_from(path, log=print):
    con = open_db(read_only=False)
    try:
        con.execute(SCHEMA)
        end = _end(con)
        con.execute(f"attach '{path}' as src (read_only)")
        con.execute("""insert or replace into long_daily select c.isin, c.date, c.open, c.high, c.low, c.close, c.volume
                       from src.candles c join instruments i using (isin) where c.date <= ?""", [end])
        con.execute("""insert or replace into long_fetched select isin, count(*), current_timestamp from long_daily
                       group by 1""")
        con.execute("detach src")
        log(status(con))
    finally:
        con.close()


def status(con=None):
    own = con is None
    con = con or open_db()
    try:
        n, d0, d1, rows = con.execute("select count(distinct isin), min(date), max(date), count(*) from long_daily").fetchone()
        return f"long history: {n} stocks · {rows:,} daily candles · {d0} to {d1}"
    except Exception:
        return "long history: none yet — run python long_history.py"
    finally:
        if own:
            con.close()


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["status"]:
        print(status())
    elif a[:1] == ["import"] and len(a) == 2:
        import_from(a[1])
    else:
        run()
