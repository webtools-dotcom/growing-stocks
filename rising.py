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
import time
from datetime import date, timedelta
import pandas as pd

import settings
from config import DATA, nse_session, open_db
from safeio import read_json, save_csv, save_json, save_text

BOARD, HISTORY = DATA / "rising_latest.csv", DATA / "rising_history.csv"


# Every threshold the labels use. classify(), the backtests and the app's "how it was ranked" panel all read
# these, so the explanation shown to users can never drift from what actually decides a label.
RULES = dict(
    streak=2,         # strong quarters (sales AND profit up >15% YoY, see growth_screen.features) out of the last 3
    sales_yoy=.20,    # latest quarter sales growth vs a year ago
    pat_yoy=.25,      # latest quarter profit growth vs a year ago
    seq_ups=2,        # quarters (of last 3) where sales rose vs the previous quarter
    near_high=.85,    # trend: price at least 85% of its 52-week high (within 15%)
    wait_floor=.60,   # WAIT only if price is still at least 60% of its high
    run_up=.80,       # 1-year rise that splits LEADER (already ran) from EMERGING (not yet)
    too_hot=1.50,     # research.py, Jun 2023 - Apr 2026: with the same growth + trend, stocks up >150% hit the -20% stop
                      # 61% of the time vs 29% for those up 80-150%, and did worse in every period -> HOT, not LEADER
)


def classify(f, rules=None):
    """Status rules — shared by the daily scan, the backtests and the app's rules sandbox (custom `rules`)."""
    R = RULES if rules is None else {**RULES, **rules}
    growth = (f.streak >= R["streak"]) & (f.sales_yoy > R["sales_yoy"]) & (f.pat_yoy > R["pat_yoy"]) & (f.seq_ups >= R["seq_ups"])
    accel = (f.sales_yoy > f.sales_yoy_2) | (f.opm_chg > 0)
    trend = f.above_200 & (f.off_high > R["near_high"])
    s = pd.Series(None, index=f.index, dtype=object)
    s[growth & trend & (f.ret1y >= R["run_up"]) & (f.ret1y < R["too_hot"])] = "LEADER"
    s[growth & trend & (f.ret1y >= R["too_hot"])] = "HOT"  # still growing, but ran too far: shown, not a buy
    s[growth & accel & trend & (f.ret1y < R["run_up"])] = "EMERGING"
    s[growth & accel & ~trend & (f.off_high > R["wait_floor"]) & (f.ret1y < R["run_up"]) & s.isna()] = "WAIT"
    return s


def explain(row, sales_base=None, profit_base=None):
    """Why a stock got its label: every gate and test with its value vs threshold, and what would change it.
    row: one stock's features (as saved in data/universe.csv). sales_base/profit_base: the year-ago quarter
    that NEXT quarter will be compared against (Rs crore), for the "next results" condition."""
    R, g = RULES, lambda k: row.get(k) if hasattr(row, "get") else row[k]
    label = classify(pd.DataFrame([dict(row)])).iloc[0]
    label = label if isinstance(label, str) else None
    t = lambda name, value, need, ok, fmt, hint="": dict(name=name, value=value, need=need, passed=bool(ok), fmt=fmt, hint=hint)
    close, dma, high, ret1y = g("close"), g("dma200"), g("high52"), g("ret1y")
    vs_dma = close / dma - 1 if dma else None
    growth = [t("strong quarters (of last 3)", g("streak"), R["streak"], g("streak") >= R["streak"], "n",
                "sales AND profit up >15% vs a year ago"),
              t("sales growth, latest quarter", g("sales_yoy"), R["sales_yoy"], g("sales_yoy") > R["sales_yoy"], "pct"),
              t("profit growth, latest quarter", g("pat_yoy"), R["pat_yoy"], g("pat_yoy") > R["pat_yoy"], "pct"),
              t("quarters sales rose vs previous (of 3)", g("seq_ups"), R["seq_ups"], g("seq_ups") >= R["seq_ups"], "n")]
    accel = [t("sales growth now vs 2 quarters ago", g("sales_yoy"), g("sales_yoy_2"), g("sales_yoy") > g("sales_yoy_2"), "pct",
               "growth is speeding up"),
             t("operating margin vs a year ago (pts)", g("opm_chg"), 0, g("opm_chg") > 0, "pts", "or: margins are widening")]
    trend = [t("price vs 200-day average", vs_dma, 0, bool(g("above_200")), "pct", "long-term uptrend"),
             t("distance from 52-week high", g("off_high") - 1, R["near_high"] - 1, g("off_high") > R["near_high"], "pct",
               "near its highs, not broken")]
    gates = [dict(name="growth", rule="all must pass", tests=growth, passed=all(x["passed"] for x in growth)),
             dict(name="acceleration", rule="either passes" + (" · not needed for leaders" if label == "LEADER" else ""),
                  tests=accel, passed=any(x["passed"] for x in accel)),
             dict(name="trend", rule="both must pass", tests=trend, passed=all(x["passed"] for x in trend)),
             dict(name="run-up", rule="splits leader / emerging · too hot = off",
                  tests=[t("1-year price rise", ret1y, R["run_up"], ret1y >= R["run_up"], "pct",
                           "above = LEADER (already ran) · below = EMERGING (not yet)"),
                         t("not too hot (1-year rise)", ret1y, R["too_hot"], ret1y < R["too_hot"], "pct",
                           f"above +{R['too_hot']:.0%}: leaders this hot hit the -20% stop most often in the backtest")],
                  passed=None)]

    nxt = []
    line = max(dma or 0, R["near_high"] * (high or 0)) or None  # the price that passes the trend gate
    if label in ("LEADER", "EMERGING") and line:
        nxt.append(("down", f"falls below ₹{line:,.1f} ({line / close - 1:+.1%})",
                    "trend gate fails → " + ("drops off the board" if label == "LEADER" else "becomes WAIT")))
    if label == "EMERGING" and pd.notna(ret1y):
        lead = close * (1 + R["run_up"]) / (1 + ret1y)
        nxt.append(("up", f"rises to ₹{lead:,.1f} ({lead / close - 1:+.1%})", "1-year run passes 80% → becomes LEADER"))
    if label == "WAIT" and line:
        nxt.append(("up", f"closes above ₹{line:,.1f} ({line / close - 1:+.1%})", "trend gate passes → becomes EMERGING"))
    if label and sales_base:
        nxt.append(("watch", f"next results: sales above ₹{sales_base * (1 + R['sales_yoy']):,.0f} Cr"
                    + (f", profit above ₹{profit_base * (1 + R['pat_yoy']):,.0f} Cr" if profit_base and profit_base > 0 else ""),
                    f"needed to keep +{R['sales_yoy']:.0%} sales / +{R['pat_yoy']:.0%} profit growth vs a year ago"))
    if label == "LEADER" and pd.notna(ret1y):
        hot = close * (1 + R["too_hot"]) / (1 + ret1y)
        nxt.append(("watch", f"rises above ₹{hot:,.1f} ({hot / close - 1:+.1%})",
                    f"1-year run passes {R['too_hot']:.0%} → too hot, leaves the board"))
    if label == "HOT":
        nxt.append(("down", f"too hot: up {ret1y:+.0%} in a year",
                    f"growth and trend pass, but leaders above +{R['too_hot']:.0%} hit the -20% stop most often"))
    if not label:
        stop = next((gt for gt in gates[:3] if gt["passed"] is False), None)
        if stop:
            miss = [x for x in stop["tests"] if not x["passed"]]
            nxt.append(("down", f"stopped at the {stop['name']} gate", "fails: " + ", ".join(x["name"] for x in miss)))
    return dict(label=label, gates=gates, next=nxt)


# Exit rule, exit_backtest.py on NSE-verified prices (209 trades, every season since mid-2023, all rules capped at
# 6 months so they compare like for like): a 20% stop costs ~2.5 pts of mean return (+5.6% vs +8.2% for a plain
# 6-month hold) but cuts the worst trade from -50% to -34%: insurance for a small account. Selling when growth
# breaks adds nothing at the same horizon (+7.1%, worst -54%), so it is a WARN on positions, not an exit.
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
                    f"6-month review ({held}d held): " + ("still " + status + " - ok to keep" if keep else
                    "ran 150%+ in a year (HOT) - these hit the -20% stop most often, consider booking profit"
                    if status == "HOT" else "no longer on the board - exit")))
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
    cal[["symbol", "date", "purpose"]].pipe(save_csv, DATA / "results_calendar.csv", index=False)
    return len(cal)


SEASONS_FILE = DATA / "seasons.pkl"
SEASON_COLS = ["streak", "sales_yoy", "pat_yoy", "seq_ups", "sales_yoy_2", "opm_chg", "above_200", "off_high", "ret1y"]


def season_data(refresh=False):
    """Every past results season's features + each stock's next-6-month return, computed once and cached.
    The rules sandbox re-classifies this in milliseconds, so any threshold can be backtested instantly."""
    if SEASONS_FILE.exists() and not refresh:
        return pd.read_pickle(SEASONS_FILE)
    import growth_screen as g
    parts = []
    for q in pd.date_range("2023-06-30", "2026-06-30", freq="QE"):  # prices backfilled to 2022 (data_sync backfill)
        on = q + pd.Timedelta(days=g.LAG_DAYS)
        f = g.features(q, on)
        r, held = g.fwd(f)
        parts.append(f[SEASON_COLS].assign(fwd=r, held=held, on=on.date()))
    d = pd.concat(parts)
    d.to_pickle(SEASONS_FILE)
    return d


def backtest_rules(d, rules=None):
    """Median 6-month return per label per season under `rules` -> DataFrame (complete 6-month windows only)."""
    import growth_screen as g
    d = d[d.held >= g.HOLD - 5].assign(status=lambda x: classify(x, rules).fillna("everything else"))
    out = d.groupby(["on", "status"]).fwd.agg(n="size", median="median", hit=lambda s: (s > 0).mean(),
                                              p10=lambda s: s.quantile(.1)).reset_index()
    return out


def backtest():
    """Forward 6m returns of each status bucket at every past results season (no look-ahead)."""
    d = backtest_rules(season_data(refresh=True))
    # saved for the app's scoreboard chart
    save_text(DATA / "backtest.json", d.assign(on=d.on.astype(str)).to_json(orient="records"))
    pd.set_option("display.width", 200)
    print(d.assign(**{c: d[c].map("{:+.0%}".format) for c in ["median", "hit", "p10"]}).to_string(index=False))
    print("\nCompleted 6m windows, averaged across seasons:")
    print(d.groupby("status")[["n", "median", "hit", "p10"]].mean()
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
    # one row per symbol: an ISIN change can leave two ISINs on one symbol (data_sync merges them; never crash on it)
    f = f.sort_values("qtr").loc[lambda d: ~d.symbol.duplicated(keep="last")]

    # entry conditions exactly as the scan saw them (growth_screen.features applies these filters), so the app can
    # show why a company was or wasn't scanned using the same numbers
    lq = g.qr.sort_values("quarter_end").groupby("isin").tail(1).set_index("isin")
    i_ = len(g.close) - 1
    turn60 = g.turn.iloc[max(0, i_ - 60): i_ + 1].median()
    (pd.DataFrame({"qtr": lq.quarter_end.dt.date, "sales": lq.sales_cr, "pat": lq.net_profit_cr,
                   "turn_cr": turn60.reindex(lq.index)})
     .join(g.inst.set_index("isin").symbol).assign(scanned=lambda d: d.index.isin(f.index))
     .pipe(save_csv, DATA / "entry.csv", index=False))

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
    # steadiness score (0-100) orders stocks within a label. research.py, top 15 growers a month: ranking by the biggest
    # sales/profit jumps did no better than random (+9.9% vs +10.9% over the typical stock, ~1/3 hit a -20% stop);
    # steadiness did +14.5% with 20% stopped out, and held in both the 2024-25 fall and the recovery. Each part is a
    # rank 0..25 among board stocks; kept separately so the app can show the breakdown.
    rk = lambda s: 25 * s.rank(pct=True)
    b["s_size"], b["s_calm"] = rk(b.market_cap_cr), rk(-b.vol60)
    b["s_accum"], b["s_margin"] = rk(b.deliv_chg).fillna(12.5), rk(b.opm_chg)
    b["score"] = b[["s_size", "s_calm", "s_accum", "s_margin"]].sum(axis=1).round(1)

    # everything the "how it was ranked" panels need: every scanned stock (for the crowd strips) + funnel counts
    keep = ["symbol", "status", "close", "dma200", "high52", "streak", "sales_yoy", "pat_yoy", "seq_ups", "sales_yoy_2",
            "opm_chg", "above_200", "off_high", "ret1y", "ret3m", "lumpy", "qtr", "vol60", "deliv_chg"]
    f[keep].pipe(save_csv, DATA / "universe.csv", index=False)
    # the market's day-by-day move (median of liquid stocks) — used to measure how a stock behaves on panic days
    liquid = g.turn.rolling(60, min_periods=20).median() >= 1
    mret = g.adj.pct_change(fill_method=None).where(liquid).median(axis=1)
    mret.rename("market").dropna().to_frame().pipe(save_csv, DATA / "market_days.csv")
    R = RULES
    gp = (f.streak >= R["streak"]) & (f.sales_yoy > R["sales_yoy"]) & (f.pat_yoy > R["pat_yoy"]) & (f.seq_ups >= R["seq_ups"])
    ap = gp & ((f.sales_yoy > f.sales_yoy_2) | (f.opm_chg > 0))
    save_json(DATA / "funnel.json", dict(
        listed=int(len(g.inst)), recent=int(sum(len(ids) for q, ids in latest.groupby(latest) if q >= on - pd.Timedelta(days=200))),
        scanned=int(len(f)), growth=int(gp.sum()), accel=int(ap.sum()),
        labels={k: int(v) for k, v in f.status.value_counts().items()}, asof=str(on.date())))
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
    order = {"LEADER": 0, "EMERGING": 1, "WAIT": 2, "HOT": 3, "BROKE": 4}
    out = out.sort_values(["status", "score"], key=lambda s: s.map(order) if s.name == "status" else -s.fillna(0))
    out.pipe(save_csv, BOARD, index=False)
    h = out[["asof", "symbol", "status", "close", "score"]]
    if HISTORY.exists():
        old = pd.read_csv(HISTORY)
        h = pd.concat([old[old["asof"] != str(on.date())], h])
    h.pipe(save_csv, HISTORY, index=False)
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
        pd.concat(parts).drop_duplicates("Symbol").set_axis(["symbol", "industry"], axis=1).pipe(save_csv, DATA / "industry.csv", index=False)


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


STATE = DATA / "sync_state.json"
RUNNING = DATA / "update.running"  # exists while an update runs; the app follows its log (data/update.log)


def _alive(pid):
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def update_running():
    """True while an update is in progress. The marker holds the update's process id, so a run killed by a crash or
    power cut (marker left behind) reads as not running; anything older than an hour is ignored too."""
    try:
        fresh = time.time() - RUNNING.stat().st_mtime < 3600  # not pd.Timestamp.now().timestamp(): reads local time as UTC
        pid = int(RUNNING.read_text().strip() or 0)
    except (OSError, ValueError):
        return False
    return fresh and (not pid or _alive(pid))
PUBLISH_HOUR = 19  # NSE's end-of-day file is normally out by ~7 pm IST


def _next_publish(d):
    d = pd.Timestamp(d).normalize() + pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d += pd.Timedelta(days=1)
    return d + pd.Timedelta(hours=PUBLISH_HOUR)


def freshness(asof, state, now):
    """-> ("failed", when) | ("stale", publish time) | ("ok", None) for the app's banner.
    New data can exist from 7 pm on the next weekday after `asof`. If an update already checked NSE after that
    moment and found nothing (holiday, late file), move on to the next weekday — so it never nags after an update."""
    checked = pd.Timestamp(state["checked_at"]) if state.get("checked_at") else None
    if state and not state.get("ok", True):
        return "failed", checked
    pub = _next_publish(asof)
    while checked is not None and checked >= pub:
        pub = _next_publish(pub)
    return ("stale", pub) if now >= pub else ("ok", None)


def update():
    """Run the whole daily update and record its outcome (data/sync_state.json) for the app's freshness banner."""
    started = pd.Timestamp.now()
    RUNNING.write_text(str(os.getpid()))
    try:
        out = _update()
    except Exception as e:
        save_json(STATE, dict(checked_at=str(started), ok=False, error=f"{type(e).__name__}: {e}"[:300]))
        raise
    finally:
        RUNNING.unlink(missing_ok=True)
    con = open_db()
    latest = con.execute("select max(trade_date) from daily_market").fetchone()[0]
    con.close()
    save_json(STATE, dict(checked_at=str(started), ok=True, latest=str(latest)))
    return out


def _update():
    import data_sync
    from screener_refresh import refresh
    print("1/3 NSE end-of-day prices + filings...")
    data_sync.update()
    print("2/3 Screener: fundamentals for new results / unseen companies...")
    con = open_db()
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
    import portfolio
    try:
        portfolio.refresh_actions(set(portfolio.load(actions=False).symbol))  # bonuses/splits change share counts
    except Exception as e:
        print(f"   corporate actions skipped: {e}")
    opened = portfolio.open_positions()
    held = list(opened.symbol)
    watch = (DATA / "watchlist.txt").read_text().split() if (DATA / "watchlist.txt").exists() else []
    if held or watch:  # bad-news check: red-flag filings + negative headlines, no AI tokens
        from brief import alerts
        con = open_db()
        names = dict(con.execute("select symbol, company_name from instruments").fetchall())
        con.close()
        try:  # network extras after the scan must never mark a good update as failed
            found = alerts(list(dict.fromkeys(held + watch)), names)
        except Exception as e:
            found = {}
            print(f"   alerts skipped: {e}")
        print(f"   alerts: {sum(map(len, found.values()))} red flags / negative headlines in the last 14 days"
              + (f" ({', '.join(found)})" if found else ""))
    if held and settings.ai_ready():
        from brief import brief  # only calls the LLM when a holding filed something new
        for s in held:
            try:
                brief(s, status=out.set_index("symbol").status.get(s, ""))
            except Exception as e:
                print(f"   brief {s} failed: {e}")

    # pre-fetch fundamentals (7-day cache) so stock pages open instantly
    import fundamentals
    fresh = list(dict.fromkeys(held + watch + list(out[out.status.isin(["LEADER", "EMERGING"])].symbol)))
    print(f"   fundamentals cache: checking {len(fresh)} names")
    for s in fresh:
        try:
            fundamentals.get(s)
        except Exception as e:
            print(f"   fundamentals {s}: {e}")

    bt = DATA / "backtest.json"
    if not bt.exists() or (pd.Timestamp.now() - pd.Timestamp(bt.stat().st_mtime, unit="s")).days >= 7:
        print("   weekly scoreboard backtest...")
        try:
            backtest()
        except Exception as e:
            print(f"   backtest skipped: {e}")

    try:
        notify_changes(out, held, watch, found if (held or watch) else {})
    except Exception as e:
        print(f"   notification skipped: {e}")
    return out


def notify(title, body):
    """Desktop notification (Windows toast via PowerShell, no extra packages); elsewhere just printed."""
    print(f"   notify: {title} — {body}")
    if sys.platform != "win32":
        return
    import subprocess
    esc = lambda s: s.replace("'", "''").replace("<", "").replace(">", "").replace("&", "and")
    ps = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml('<toast><visual><binding template="ToastGeneric"><text>{esc(title)}</text><text>{esc(body)}</text></binding></visual></toast>')
$app = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def notify_changes(out, held, watch, found):
    """Toast only what's new since the last notification: exits, triggers, red flags, results tomorrow."""
    seen_p = DATA / "notified.json"
    seen = set(read_json(seen_p, []))
    msgs = []
    import portfolio
    if held:
        con = open_db()
        for p in portfolio.open_positions().itertuples():
            row = con.execute("""select close, trade_date from daily_market join instruments using (isin) where symbol = ?
                                 order by trade_date desc limit 1""", [p.symbol]).fetchone()
            if row:
                st = out.set_index("symbol").status.get(p.symbol, "")
                for lvl, m in position_alerts(p.buy_price, p.buy_date, row[0], row[1], st):
                    msgs.append((f"{lvl}:{p.symbol}:{row[1]}", f"{p.symbol}: {lvl.lower()}", m))
        con.close()
    for r in out[out.symbol.isin(watch) & (out.prev_status == "WAIT") & out.status.isin(["LEADER", "EMERGING"])].itertuples():
        msgs.append((f"trig:{r.symbol}:{r.asof}", f"{r.symbol}: trigger crossed", f"now {r.status.lower()} at ₹{r.close:,.1f}"))
    for s, items in found.items():
        for i in items:
            msgs.append((f"flag:{s}:{i['date']}:{i['text'][:40]}", f"{s}: {i['kind']} red flag", i["text"][:120]))
    cal = DATA / "results_calendar.csv"
    if cal.exists():
        c = pd.read_csv(cal, parse_dates=["date"])
        soon = c[c.symbol.isin(held + watch) & c.date.dt.date.isin([date.today(), date.today() + timedelta(1)])]
        for r in soon.itertuples():
            msgs.append((f"res:{r.symbol}:{r.date:%Y%m%d}", f"{r.symbol}: results {r.date:%a %d %b}", "watch the reaction before acting"))
    new = [m for m in msgs if m[0] not in seen]
    for key, title, body in new[:5]:
        notify(title, body)
    if len(new) > 5:
        notify(f"{len(new) - 5} more alerts", "open Rising Stocks → today")
    save_json(seen_p, sorted(seen | {m[0] for m in new}))


if __name__ == "__main__":
    {"update": update, "scan": scan, "backtest": backtest}[sys.argv[1] if len(sys.argv) > 1 else "scan"]()
