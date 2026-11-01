"""Growth-compounder screen + point-in-time backtest on the local market DuckDB (data_sync.py).

Thesis tested: buy companies with consistent YoY sales+profit growth (optionally
only when price trend confirms), hold ~6 months.

Usage:  python growth_screen.py backtest
        python growth_screen.py screen
"""
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from config import DB
from data_sync import adjust_prices
LAG_DAYS = 60      # results assumed public 60d after quarter end (SEBI max: 45d, Q4: 60d)
HOLD = 126         # ~6 months of trading days
TOP = 10

con = duckdb.connect(str(DB), read_only=True)
inst = con.execute("select isin, symbol, company_name, sector, market_cap_cr from instruments").df()
qr = con.execute("""select isin, quarter_end, sales_cr, op_profit_cr, opm_pct, net_profit_cr
                    from quarterly_results where quarter_end >= '2022-06-30'""").df()
qr["quarter_end"] = pd.to_datetime(qr["quarter_end"])
# fresher quarters scraped from Screener (screener_refresh.py) override the DB's copy
_sq = Path(__file__).resolve().parent / "data" / "screener_q.csv"
if _sq.exists():
    sq = pd.read_csv(_sq, parse_dates=["quarter_end"]).merge(inst[["isin", "symbol"]], on="symbol").drop(columns="symbol")
    qr = pd.concat([qr, sq[qr.columns]]).drop_duplicates(["isin", "quarter_end"], keep="last")
px = con.execute("select isin, trade_date, close, turnover_cr from daily_market").df()
px["trade_date"] = pd.to_datetime(px["trade_date"])
con.close()  # release the file so the daily sync can write
close = px.pivot(index="trade_date", columns="isin", values="close").sort_index()
turn = px.pivot(index="trade_date", columns="isin", values="turnover_cr").sort_index()
# NSE prices are unadjusted: fix splits, bonuses and bad prints once, here, for the scan and all backtests
adj = adjust_prices(close)


def features(asof_q: pd.Timestamp, on: pd.Timestamp) -> pd.DataFrame:
    """Fundamental + price features using only data public on date `on`."""
    qs = [asof_q - pd.offsets.QuarterEnd(k) for k in range(0, 8)]
    w = qr[qr.quarter_end.isin(qs)].pivot(index="isin", columns="quarter_end")
    def g(col, k):  # YoY growth of quarter k back
        cur, prev = w[(col, qs[k])], w[(col, qs[k + 4])]
        return (cur - prev) / prev.abs()
    f = pd.DataFrame(index=w.index)
    f["sales"] = w[("sales_cr", qs[0])]
    f["pat"] = w[("net_profit_cr", qs[0])]
    f["sales_yoy"] = g("sales_cr", 0)
    f["pat_yoy"] = g("net_profit_cr", 0)
    f["opm_chg"] = w[("opm_pct", qs[0])] - w[("opm_pct", qs[4])]
    # consistency: how many of the last 3 quarters had sales YoY >15% AND profit YoY >15%
    f["streak"] = sum(((g("sales_cr", k) > .15) & (g("net_profit_cr", k) > .15)).astype(int) for k in range(3))
    f["seq_up"] = (w[("sales_cr", qs[0])] > w[("sales_cr", qs[1])]).astype(int)  # QoQ sales up
    f["seq_ups"] = sum((w[("sales_cr", qs[k])] > w[("sales_cr", qs[k + 1])]).astype(int) for k in range(3))
    f["sales_yoy_2"] = g("sales_cr", 2)  # growth 2 quarters ago, for acceleration
    f["opm_chg_2"] = w[("opm_pct", qs[2])] - w[("opm_pct", qs[6])]
    i = close.index.searchsorted(on)
    if i >= len(close):
        i = len(close) - 1
    hist = adj.iloc[max(0, i - 250): i + 1]
    f = f.join(pd.DataFrame({
        "close": close.iloc[i],
        "mom6": adj.iloc[i] / adj.iloc[max(0, i - 126)] - 1,
        "ret1y": adj.iloc[i] / adj.iloc[max(0, i - 250)] - 1,
        "off_high": adj.iloc[i] / hist.max(),
        "above_200": adj.iloc[i] > hist.tail(200).mean(),
        "turn_cr": turn.iloc[max(0, i - 60): i + 1].median(),
    }), how="inner")
    f["date_idx"] = i
    # base hygiene: real business, profitable, tradable
    f = f[(f.sales >= 25) & (f.pat > 0) & (f.turn_cr >= 1) & f.close.notna()]
    return f


def growth_score(f):
    r = lambda s: s.clip(-1, 5).rank(pct=True)
    return r(f.sales_yoy) + r(f.pat_yoy) + 0.5 * r(f.opm_chg)


STRATS = {
    "universe (all profitable)":   lambda f: f,
    "momentum only (top 6m ret)":  lambda f: f.nlargest(TOP, "mom6"),
    "growth only (streak=3)":      lambda f: f[f.streak == 3].assign(s=growth_score).nlargest(TOP, "s"),
    "growth + uptrend":            lambda f: f[(f.streak == 3) & f.above_200 & (f.off_high > .80)]
                                               .assign(s=growth_score).nlargest(TOP, "s"),
    "growth + uptrend + strong RS": lambda f: f[(f.streak >= 2) & f.above_200 & (f.off_high > .80)]
                                               .assign(s=lambda d: growth_score(d) + d.mom6.rank(pct=True))
                                               .nlargest(TOP, "s"),
    # same growth bar, split by how far the price has already run in the past year
    "EMERGING: accelerating, 1y run <60%": lambda f: emerging(f)[lambda d: d.ret1y < .6]
                                               .assign(s=growth_score).nlargest(TOP, "s"),
    "FRESH BREAKOUT: accelerating, 1y <80%, at highs": lambda f: emerging(f)[lambda d: (d.ret1y < .8) & (d.off_high > .92)]
                                               .assign(s=growth_score).nlargest(TOP, "s"),
    "ALREADY RUN: accelerating, 1y run >100%": lambda f: emerging(f, trend=False)[lambda d: d.ret1y > 1]
                                               .assign(s=growth_score).nlargest(TOP, "s"),
}


def emerging(f, trend=True):
    """Growth that is speeding up: 2+ strong quarters, YoY sales growth higher now than 2 qtrs ago,
    margins expanding, sales rising QoQ."""
    m = ((f.streak >= 2) & (f.sales_yoy > .25) & (f.sales_yoy > f.sales_yoy_2)
         & (f.opm_chg > 0) & (f.seq_ups >= 2))
    if trend:
        m &= f.above_200 & (f.off_high > .75)
    return f[m]


def fwd(f, n=HOLD):
    i = int(f.date_idx.iloc[0])
    j = min(i + n, len(adj) - 1)
    return (adj.iloc[j][f.index] / adj.iloc[i][f.index] - 1), j - i


def backtest():
    rows, picks = [], []
    for q in pd.date_range("2024-06-30", "2026-06-30", freq="QE"):
        on = q + pd.Timedelta(days=LAG_DAYS)
        f = features(q, on)
        for name, pick in STRATS.items():
            sel = pick(f)
            if sel.empty:
                continue
            r, held = fwd(sel)
            rows.append(dict(rebalance=on.date(), strat=name, n=len(sel), held_days=held,
                             avg=r.mean(), median=r.median(), hit=(r > 0).mean(), worst=r.min(), best=r.max()))
            if name == "growth + uptrend + strong RS":
                picks.append(pd.DataFrame({"rebalance": on.date(), "ret": r}).join(inst.set_index("isin").symbol))
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)
    print(df.pivot(index="rebalance", columns="strat", values="avg").map("{:+.0%}".format).to_string())
    full = df[df.held_days >= HOLD - 5]
    print("\nCompleted 6m windows only:")
    print(full.groupby("strat")[["avg", "median", "hit", "worst"]].mean().map("{:+.1%}".format).to_string())
    print("\nPer-pick detail, growth+uptrend+RS:")
    print(pd.concat(picks).assign(ret=lambda d: d.ret.map("{:+.0%}".format))
          .groupby("rebalance").agg(lambda s: ", ".join(s.astype(str))).to_string())


def screen():
    # latest: use each company's most recent reported quarter
    latest = qr.groupby("isin").quarter_end.max()
    on = close.index[-1]
    out = []
    for q, ids in latest.groupby(latest):
        if q < pd.Timestamp("2026-06-30"):
            continue
        f = features(q, on)
        out.append(f[f.index.isin(ids.index)].assign(qtr=q.date()))
    f = pd.concat(out)
    f = f[(f.streak >= 2) & f.above_200 & (f.off_high > .80)]
    f = f.assign(s=growth_score(f) + f.mom6.rank(pct=True)).sort_values("s", ascending=False)
    f = f.join(inst.set_index("isin")[["symbol", "sector", "market_cap_cr"]])
    cols = ["symbol", "sector", "qtr", "close", "market_cap_cr", "sales_yoy", "pat_yoy", "opm_chg",
            "streak", "seq_up", "mom6", "off_high", "s"]
    v = f[cols].head(40).copy()
    for c in ["sales_yoy", "pat_yoy", "mom6", "off_high"]:
        v[c] = v[c].map("{:+.0%}".format)
    v["market_cap_cr"] = v.market_cap_cr.round(0)
    print(f"prices as of {on.date()}")
    print(v.to_string(index=False))
    f[cols].to_csv("screen_latest.csv", index=False)


if __name__ == "__main__":
    {"backtest": backtest, "screen": screen}[sys.argv[1] if len(sys.argv) > 1 else "backtest"]()
