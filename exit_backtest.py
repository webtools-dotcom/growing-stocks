"""Which exit rule works for the terminal's EMERGING / LEADER entries?

Every results season (quarter end + 60 days) the stocks the terminal would have labelled
EMERGING or LEADER are "bought" at that day's close and each exit rule is walked forward day by day.
Trades still open at the end of data are marked to the last close (flagged `open`).

Usage: python exit_backtest.py
"""
import numpy as np
import pandas as pd

import growth_screen as g
from rising import classify

MAX_DAYS = 250   # hard cap: one year
SEASONS = pd.date_range("2024-12-31", "2026-06-30", freq="QE")

adj = g.adj
dma200 = adj.rolling(200, min_periods=150).mean()
dma50 = adj.rolling(50, min_periods=40).mean()
qi = g.qr.set_index(["isin", "quarter_end"])


def growth_broken(isin, q):
    """True/False if quarter q (vs a year earlier) breaks the growth thesis; None if not reported."""
    try:
        cur, prev = qi.loc[(isin, q)], qi.loc[(isin, q - pd.offsets.QuarterEnd(4))]
    except KeyError:
        return None
    if cur.net_profit_cr <= 0:
        return True
    sy = (cur.sales_cr - prev.sales_cr) / abs(prev.sales_cr) if prev.sales_cr else np.nan
    py = (cur.net_profit_cr - prev.net_profit_cr) / abs(prev.net_profit_cr) if prev.net_profit_cr else np.nan
    return bool(sy < .15 or py < .15)


def walk(isin, i0, q0, rule):
    """-> (return, days held, open?)"""
    p = adj[isin].to_numpy()
    d200, d50 = dma200[isin].to_numpy(), dma50[isin].to_numpy()
    entry, peak = p[i0], p[i0]
    last = min(len(p) - 1, i0 + MAX_DAYS)
    # result-check days: each later season's "public" date (quarter end + 60d)
    checks = {}
    for k in range(1, 5):
        qk = q0 + pd.offsets.QuarterEnd(k)
        j = adj.index.searchsorted(qk + pd.Timedelta(days=g.LAG_DAYS))
        if j <= last:
            checks[j] = qk
    for j in range(i0 + 1, last + 1):
        c = p[j]
        if np.isnan(c):
            continue
        peak = max(peak, c)
        hit = False
        for part in rule.split("+"):
            if part == "hold6m":
                hit |= j - i0 >= 126
            elif part.startswith("trail"):
                hit |= c < peak * (1 - int(part[5:]) / 100)
            elif part.startswith("stop"):
                hit |= c < entry * (1 - int(part[4:]) / 100)
            elif part == "dma200":
                hit |= c < d200[j]
            elif part == "dma50":
                hit |= c < d50[j]
            elif part == "growth" and j in checks:
                hit |= bool(growth_broken(isin, checks[j]))
        if hit:
            return c / entry - 1, j - i0, False
    return p[last] / entry - 1, last - i0, last == len(p) - 1 and last - i0 < MAX_DAYS


RULES = ["hold6m", "trail15", "trail20", "trail25", "stop15+hold6m", "stop20+hold6m", "stop25+hold6m",
         "dma50", "dma200", "growth", "growth+stop20", "growth+stop25", "growth+trail20", "growth+trail25", "growth+dma200"]


def run():
    entries = []
    for q in SEASONS:
        on = q + pd.Timedelta(days=g.LAG_DAYS)
        f = g.features(q, on)
        f["status"] = classify(f)
        for isin, st in f.status.dropna().items():
            if st in ("EMERGING", "LEADER"):
                entries.append((q, int(f.date_idx.iloc[0]), isin, st))
    print(f"{len(entries)} entries across {len(SEASONS)} seasons")
    rows = []
    for q, i0, isin, st in entries:
        for r in RULES:
            ret, days, open_ = walk(isin, i0, q, r)
            rows.append(dict(season=q.date(), isin=isin, status=st, rule=r, ret=ret, days=days, open=open_))
    return pd.DataFrame(rows)


def summarize(t, title):
    s = t.groupby("rule").agg(trades=("ret", "size"), mean=("ret", "mean"), median=("ret", "median"),
                              hit=("ret", lambda x: (x > 0).mean()), worst=("ret", "min"),
                              p10=("ret", lambda x: x.quantile(.1)), days=("days", "mean"), still_open=("open", "mean"))
    s["per_100d"] = s["mean"] / s.days * 100  # return per 100 trading days in the trade (capital efficiency)
    s = s.reindex(RULES)
    print(f"\n== {title} ==")
    fmt = {c: "{:+.1%}".format for c in ["mean", "median", "hit", "worst", "p10", "still_open", "per_100d"]}
    print(s.to_string(formatters=fmt | {"days": "{:.0f}".format}))


if __name__ == "__main__":
    t = run()
    t.to_csv(g.Path(__file__).resolve().parent / "data" / "exit_trades.csv", index=False)
    t = t.assign(season=pd.to_datetime(t.season))
    # entries with a full 126 trading days of data after them
    mature = t[t.season.map(lambda q: adj.index.searchsorted(q + pd.Timedelta(days=g.LAG_DAYS)) + 126 < len(adj))]
    print("mature entries per season:", mature[mature.rule == "hold6m"].groupby("season").size().to_dict())
    summarize(mature, "Entries with 6m+ of history")
    for st in ("EMERGING", "LEADER"):
        summarize(mature[mature.status == st], f"{st} only, mature entries")
    summarize(t, "ALL entries incl. recent (open trades marked to market)")
    by_season = mature.pivot_table(index="season", columns="rule", values="ret", aggfunc="mean")[RULES]
    print("\nMean return per entry season (does a rule win every season or just one?):")
    print(by_season.map("{:+.0%}".format).to_string())
    med = mature.pivot_table(index="season", columns="rule", values="ret", aggfunc="median")[RULES]
    print("\nMedian return per entry season:")
    print(med.map("{:+.0%}".format).to_string())
