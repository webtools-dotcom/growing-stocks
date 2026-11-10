"""How the labels and the steadiness score were chosen — reproduce every number in the README.

    python research.py          # builds a point-in-time panel (monthly entries, ~30 signals, next-6-month return),
                                # then compares today's rules / alternatives and ranking scores across three periods

Point-in-time: each month only uses results public by then (60-day lag) and prices up to that day. Limits: the stock
list is today's listings (companies delisted since are missing), and market cap back then is estimated from today's.
Needs price history from 2022: python data_sync.py backfill 1650"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
import growth_screen as g

OUT = ROOT / "data" / "research_panel.pkl"

# full-depth quarterly history (growth_screen trims to 2022+), Screener overrides DB
con = g.open_db()
qr = con.execute("select isin, quarter_end, sales_cr, op_profit_cr, opm_pct, net_profit_cr from quarterly_results").df()
dm = con.execute("select isin, trade_date, delivery_pct, volume from daily_market").df()
mcap_now = con.execute("select isin, market_cap_cr from instruments").df().set_index("isin").market_cap_cr
con.close()
qr["quarter_end"] = pd.to_datetime(qr.quarter_end)
sq = pd.read_csv(ROOT / "data/screener_q.csv", parse_dates=["quarter_end"]).merge(g.inst[["isin", "symbol"]], on="symbol").drop(columns="symbol")
qr = pd.concat([qr, sq[qr.columns]]).drop_duplicates(["isin", "quarter_end"], keep="last")
dm["trade_date"] = pd.to_datetime(dm.trade_date)
deliv = dm.pivot(index="trade_date", columns="isin", values="delivery_pct").reindex(g.close.index)
ind = pd.read_csv(ROOT / "data/industry.csv").merge(g.inst[["isin", "symbol"]], on="symbol").set_index("isin").industry

adj, close, turn = g.adj, g.close, g.turn
ret = adj.pct_change(fill_method=None)
liquid = turn.rolling(60, min_periods=20).median() >= 1
mkt = ret.where(liquid).median(axis=1)               # equal-weight "typical stock" daily move
mkt_idx = (1 + mkt.fillna(0)).cumprod()


def qfeat(asof_q):
    qs = [asof_q - pd.offsets.QuarterEnd(k) for k in range(12)]
    w = qr[qr.quarter_end.isin(qs)].pivot(index="isin", columns="quarter_end")
    S = lambda c, k: w[(c, qs[k])] if (c, qs[k]) in w else pd.Series(np.nan, index=w.index)
    yoy = lambda c, k: (S(c, k) - S(c, k + 4)) / S(c, k + 4).where(S(c, k + 4) > 0)
    yoyp = lambda c, k: (S(c, k) - S(c, k + 4)) / S(c, k + 4).abs().where(S(c, k + 4).abs() > 0)
    f = pd.DataFrame(index=w.index)
    f["sales"], f["pat"] = S("sales_cr", 0), S("net_profit_cr", 0)
    f["sales_yoy"], f["pat_yoy"] = yoy("sales_cr", 0), yoyp("net_profit_cr", 0)
    f["sales_yoy_2"] = yoy("sales_cr", 2)
    f["opm"], f["opm_chg"] = S("opm_pct", 0), S("opm_pct", 0) - S("opm_pct", 4)
    f["streak"] = sum(((yoy("sales_cr", k) > .15) & (yoyp("net_profit_cr", k) > .15)).astype(int) for k in range(3))
    f["seq_ups"] = sum((S("sales_cr", k) > S("sales_cr", k + 1)).astype(int) for k in range(3))
    # long consistency: of the last 6 quarters, how many had sales (and profit) up >15% YoY — needs 10 quarters
    sy6 = pd.concat([yoy("sales_cr", k) for k in range(6)], axis=1)
    py6 = pd.concat([yoyp("net_profit_cr", k) for k in range(6)], axis=1)
    f["cons6_sales"] = (sy6 > .15).sum(axis=1).where(sy6.notna().all(axis=1))
    f["cons6_both"] = ((sy6 > .15) & (py6 > .15)).sum(axis=1).where(sy6.notna().all(axis=1))
    f["sales_yoy_min6"] = sy6.min(axis=1).where(sy6.notna().all(axis=1))  # worst of 6: no bad quarter hiding
    ttm = lambda c, a: sum(S(c, k) for k in range(a, a + 4))
    f["ttm_sales_g"] = ttm("sales_cr", 0) / ttm("sales_cr", 4).where(ttm("sales_cr", 4) > 0) - 1
    tp0, tp4 = ttm("net_profit_cr", 0), ttm("net_profit_cr", 4)
    f["ttm_pat"] = tp0
    f["ttm_pat_g"] = (tp0 / tp4.where(tp4 > 0) - 1)
    f["accel"] = f.sales_yoy - f.sales_yoy_2
    f["oplev"] = f.pat_yoy.clip(-1, 5) - f.sales_yoy.clip(-1, 5)
    f["pat_accel"] = yoyp("net_profit_cr", 0).clip(-1, 5) - yoyp("net_profit_cr", 1).clip(-1, 5)
    return f


def pfeat(i, asof_q):
    h = adj.iloc[max(0, i - 250): i + 1]
    p = adj.iloc[i]
    sd = ret.iloc[max(0, i - 120): i + 1]
    f = pd.DataFrame({
        "close": close.iloc[i], "turn_cr": turn.iloc[max(0, i - 60): i + 1].median(),
        "mom6": p / adj.iloc[max(0, i - 126)] - 1, "mom3": p / adj.iloc[max(0, i - 63)] - 1,
        "mom1": p / adj.iloc[max(0, i - 21)] - 1, "ret1y": p / adj.iloc[max(0, i - 250)] - 1,
        "off_high": p / h.max(), "above_200": p > h.tail(200).mean(),
        "dma50_200": h.tail(50).mean() / h.tail(200).mean(),
        "vol60": sd.tail(60).std(), "vcp": sd.tail(20).std() / sd.std(),
        "turn_exp": turn.iloc[max(0, i - 20): i + 1].median() / turn.iloc[max(0, i - 120): i + 1].median(),
        "deliv_chg": deliv.iloc[max(0, i - 20): i + 1].mean() - deliv.iloc[max(0, i - 120): i + 1].mean(),
        "deliv": deliv.iloc[max(0, i - 20): i + 1].mean(),
    })
    f["rs6"] = f.mom6 - (mkt_idx.iloc[i] / mkt_idx.iloc[max(0, i - 126)] - 1)
    # results reaction: stock vs market from 20 to 60 days after quarter end (when results land)
    a, b = close.index.searchsorted(asof_q + pd.Timedelta(days=20)), min(close.index.searchsorted(asof_q + pd.Timedelta(days=60)), i)
    if b > a:
        f["reaction"] = (adj.iloc[b] / adj.iloc[a] - 1) - (mkt_idx.iloc[b] / mkt_idx.iloc[a] - 1)
    # point-in-time market cap: today's mcap scaled by the price move since then (ignores share issuance)
    f["mcap"] = mcap_now.reindex(f.index) * close.iloc[i] / close.iloc[-1]
    return f


rows = []
dates = pd.date_range("2023-06-01", g.close.index[-1] - pd.Timedelta(days=185), freq="MS") + pd.Timedelta(days=19)
for d in dates:
    i = close.index.searchsorted(d)
    on = close.index[i]
    asof_q = (on - pd.Timedelta(days=g.LAG_DAYS)) - pd.offsets.QuarterEnd(0)
    if asof_q + pd.Timedelta(days=g.LAG_DAYS) > on:
        asof_q -= pd.offsets.QuarterEnd(1)
    f = qfeat(asof_q).join(pfeat(i, asof_q), how="inner")
    f = f[(f.sales >= 25) & (f.pat > 0) & (f.turn_cr >= 1) & f.close.notna()]
    j = min(i + g.HOLD, len(adj) - 1)
    f["fwd"] = adj.iloc[j][f.index] / adj.iloc[i][f.index] - 1
    f["fwd3"] = adj.iloc[min(i + 63, len(adj) - 1)][f.index] / adj.iloc[i][f.index] - 1
    # worst drawdown in the holding window (did it hit a -20% stop?)
    path = adj.iloc[i: j + 1][f.index] / adj.iloc[i][f.index]
    f["maxdd"] = path.min() - 1
    f["held"] = j - i
    f["on"], f["asof_q"] = on.date(), asof_q.date()
    f["industry"] = ind.reindex(f.index)
    rows.append(f)
    print(on.date(), asof_q.date(), len(f))
P = pd.concat(rows)
P["ex"] = P.fwd - P.groupby("on").fwd.transform("median")  # excess vs the typical stock that month
P["pe"] = P.mcap / P.ttm_pat.where(P.ttm_pat > 0)
P["peg"] = P.pe / (P.ttm_pat_g * 100).where(P.ttm_pat_g > 0)
# industry breadth: share of an industry's companies with sales up >15% (sector tailwind)
P["ind_breadth"] = P.groupby(["on", "industry"]).sales_yoy.transform(lambda s: (s > .15).mean() if len(s) >= 4 else np.nan)
P["ind_mom"] = P.groupby(["on", "industry"]).mom6.transform(lambda s: s.median() if len(s) >= 4 else np.nan)
P.to_pickle(OUT)
print(P.shape)

# ------------------------------------------------------------------ evaluation
from rising import RULES  # noqa: E402


P = P[P.held >= 120].rename_axis("isin").reset_index()
ons = sorted(P.on.unique())
cut = [ons[len(ons) // 3], ons[2 * len(ons) // 3]]
P["period"] = np.select([P.on < cut[0], P.on < cut[1]], ["A", "B"], "C")
print(f"{len(ons)} monthly entries {ons[0]} -> {ons[-1]};  periods A < {cut[0]} <= B < {cut[1]} <= C")
print("typical stock 6m return by period:", P.groupby("period").apply(lambda x: x.groupby("on").fwd.median().mean()).map("{:+.0%}".format).to_dict())


def cls(f, spike=None, vol=None, hot=None, R=RULES):
    growth = (f.streak >= R["streak"]) & (f.sales_yoy > R["sales_yoy"]) & (f.pat_yoy > R["pat_yoy"]) & (f.seq_ups >= R["seq_ups"])
    if spike:
        growth &= f.sales_yoy <= spike
    accel = (f.sales_yoy > f.sales_yoy_2) | (f.opm_chg > 0)
    trend = f.above_200 & (f.off_high > R["near_high"])
    calm = (f.vol60 <= vol) if vol else True
    s = pd.Series(None, index=f.index, dtype=object)
    lead = growth & trend & (f.ret1y >= R["run_up"]) & calm
    if hot:
        lead &= f.ret1y < hot
    s[lead] = "LEADER"
    s[growth & accel & trend & (f.ret1y < R["run_up"]) & calm] = "EMERGING"
    return s


def ev(x):
    m = x.groupby("on").agg(med=("ex", "median"), hit=("fwd", lambda s: (s > 0).mean()),
                            stop=("maxdd", lambda s: (s <= -.2).mean()), n=("ex", "size"))
    per = x.groupby("period").apply(lambda y: y.groupby("on").ex.median().mean()) if len(x) else pd.Series(dtype=float)
    return dict(n=m.n.reindex(ons).fillna(0).median(), med_ex=m.med.mean(), hit=m.hit.mean(), stop20=m.stop.mean(),
                beat=(m.med > 0).mean(), **{p: per.get(p, np.nan) for p in "ABC"})


fmt = lambda v: f"{v:+.1%}" if isinstance(v, float) and abs(v) < 5 else str(v)
pd.set_option("display.width", 230)
rows = []
for name, kw in [("old rules (no 150% cap)", {}), ("no spike", dict(spike=1.0)), ("no spike + calm 3%", dict(spike=1.0, vol=.03)),
                 ("no spike + calm 3.5%", dict(spike=1.0, vol=.035)), ("no spike + leader<150%", dict(spike=1.0, hot=1.5)),
                 ("no spike + calm 3% + leader<150%", dict(spike=1.0, vol=.03, hot=1.5)),
                 ("no spike + calm 3.5% + leader<150%", dict(spike=1.0, vol=.035, hot=1.5))]:
    s = cls(P, **kw)
    for lab in ("LEADER", "EMERGING"):
        rows.append(dict(rules=name, label=lab, **ev(P[s == lab])))
print("\n=== LABELS: 6m return in excess of the typical stock (A/B/C = the three periods) ===")
print(pd.DataFrame(rows).to_string(index=False, formatters={c: fmt for c in ["med_ex", "hit", "stop20", "beat", "A", "B", "C"]}))

# ---- ranking inside the board
R_ = lambda s: s.groupby(P.on).rank(pct=True)
growth = (P.streak >= 2) & (P.sales_yoy > .2) & (P.pat_yoy > .25) & (P.seq_ups >= 2)
P["spike"] = P.sales_yoy > 1.0
G = P[growth].copy()
g = lambda c: G[c].groupby(G.on).rank(pct=True)
G["score_today"] = g("sales_yoy") + g("pat_yoy") + .5 * g("opm_chg") + .5 * g("mom3")      # the board score before Nov 2026 (minus lumpy)
G["score_steady"] = (g("mcap") + (1 - g("vol60")) + G.deliv_chg.groupby(G.on).rank(pct=True).fillna(.5) + g("opm_chg")) / 4
G["score_steady_rs"] = (G.score_steady * 4 + g("rs6")) / 5
print("\n=== RANKING growers (growth gate, no spikes): top 15 per month by each score ===")
rows = []
base = G[~G.spike]
for sc in ["score_old", "score_steady", "score_steady_rs"]:
    rows.append(dict(score=sc, **ev(base.sort_values(sc, ascending=False).groupby("on").head(15))))
rows.append(dict(score="random 15 (avg of 50)", **pd.DataFrame([ev(base.groupby("on").sample(15, replace=True, random_state=i)) for i in range(50)]).mean().to_dict()))
rows.append(dict(score="all growers", **ev(base)))
rows.append(dict(score="spike growers (>100%)", **ev(G[G.spike])))
print(pd.DataFrame(rows).to_string(index=False, formatters={c: fmt for c in ["med_ex", "hit", "stop20", "beat", "A", "B", "C"]}))

print("\n=== signal stability: rank IC inside growers, per period ===")
for c in ["rs6", "mom3", "sales_yoy", "pat_yoy", "opm_chg", "vol60", "mcap", "deliv_chg", "reaction", "pe", "cons6_sales"]:
    v = G.groupby(["period", "on"]).apply(lambda x: x[c].rank().corr(x.ex.rank()) if x[c].notna().sum() > 20 else np.nan)
    print(f"  {c:12s} " + "  ".join(f"{p}: {v.get(p, pd.Series(dtype=float)).mean():+.3f} ({v.get(p, pd.Series(dtype=float)).notna().sum():2d}m)" for p in "ABC"))
