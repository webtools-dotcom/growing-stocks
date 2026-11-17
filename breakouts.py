"""Chart breakouts, merged from the breakout lab (D:/PROJECT-TESTINGS/breakout-lab): detectors, live scan, grades,
trade plans and the 20-year evidence they're judged by.

    python breakouts.py          # scan every liquid stock -> data/breakouts.csv (also runs in the daily update)

Detectors, all on closing prices (split/bonus-adjusted with data_sync.adjust_prices):
  box        26+ weeks in a tight range (top within 35% of bottom); first weekly close above the top
  base       a ceiling tested 3+ times over 3+ years (the highest close of the base), with a real dip in between
  trendline  a falling weekly line through lower highs   ·   swing: the same on the daily chart, 2-12 weeks old

Grades come from the lab's 20-year backtest (breakout_evidence.json, 112,311 breakouts on 2,639 stocks, 2006-2026):
  A  box breakout by a company Rising rates leader/emerging: the only group that beat the typical stock
     (86% made money, 9% hit the stop, +6.9% vs market — 22 cases since 2023, a small sample)
  B  any box breakout (57% made money, 17% stopped) or a long base tested 4+ times
  C  everything else: trendline and swing-line breakouts lost to the typical stock in every split tested
Rising's NSE prices start in 2022; run long_history.py once to add Upstox candles back to 2006, so long bases can
span up to 16 years (stitched in daily(); without it, bases are at most ~4.5 years)."""
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from config import DATA, open_db
from safeio import save_csv

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "breakout_evidence.json"
OUT = DATA / "breakouts.csv"
MIN_TURNOVER = 1e7  # Rs 1 crore traded a day
KIND = {"base": "base", "line": "trendline", "swing": "swing", "box": "box"}
SETUP = {"box_break": "box · broke out", "box_near": "box · near the top", "base_near": "long base · near",
         "base_retest": "long base · retest", "base_running": "long base · running", "line_near": "trendline · near",
         "line_break": "trendline · broke", "swing_near": "swing line · near", "swing_break": "swing line · broke"}

def resample(d, rule):
    """Daily -> weekly ('W-FRI') or monthly ('ME') candles."""
    d = d.set_index(pd.to_datetime(d.date)) if "date" in d else d
    g = d.resample(rule)
    out = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last(),
                        "volume": g.volume.sum(), "last_day": g.close.apply(lambda s: s.index.max() if len(s) else pd.NaT)})
    return out.dropna(subset=["close"])


def pivots(high, k=2):
    """Indices of swing highs: a bar whose high is the highest of the k bars on each side."""
    h = high.to_numpy()
    n = len(h)
    idx = [i for i in range(k, n - k) if h[i] == h[i - k:i + k + 1].max()]
    return np.array(idx, dtype=int)


def _vol_ratio(vol, i, n=20):
    base = vol[max(0, i - n):i]
    return float(vol[i] / base.mean()) if len(base) >= 5 and base.mean() > 0 else np.nan


# ------------------------------------------------------------------ 1. long base (monthly)
def _ceiling(t, cl, hi, lo, cpiv, cands, min_years=3, max_years=16, touch_band=.10, min_touches=3, min_dip=.30):
    """The longest closing-basis ceiling among `cands` (candidate levels) as of bar t, using bars before t.
    A ceiling L: no monthly close above L*1.02 for >= min_years, the highs reached within `touch_band` of L at least
    `min_touches` times (separate swings), and the price dipped >= `min_dip` below L inside the base.
    -> (L, start, touches, dip) or None."""
    best = None
    for L in cands:
        above = np.flatnonzero(cl[:t] > L * 1.02)
        start = max(int(above[-1]) + 1 if len(above) else 0, t - max_years * 12)
        if t - start < min_years * 12:
            continue
        swings = hi[cpiv[(cpiv >= start) & (cpiv < t)]]
        tp = cpiv[(cpiv >= start) & (cpiv < t)][swings >= L * (1 - touch_band)]
        touches = int(len(tp) and 1 + (np.diff(tp) >= 4).sum())  # touches at least 4 months apart
        if touches < min_touches:
            continue
        dip = 1 - lo[start:t].min() / L
        if dip < min_dip:
            continue
        if best is None or (t - start, touches) > (t - best[1], best[2]):
            best = (float(L), start, touches, float(dip))
    return best


def base_events(m, **kw):
    """First monthly close above a multi-year closing-basis ceiling. -> list of dict (one per breakout)."""
    hi, cl, lo, vol = m.high.to_numpy(), m.close.to_numpy(), m.low.to_numpy(), m.volume.to_numpy()
    cpiv = pivots(m.high, k=2)
    clpiv = pivots(m.close, k=2)
    out, last = [], -99
    for t in range(36, len(m)):
        if t - last < 12 or not (cl[t] > cl[max(0, t - 12):t].max()):  # a breakout is always a 12-month closing high
            continue
        cands = [c for c in cl[clpiv[clpiv < t - 2]] if cl[t - 1] <= c < cl[t]]
        lv = _ceiling(t, cl, hi, lo, cpiv, sorted(set(cands)), **kw)
        if lv:
            L, start, touches, dip = lv
            last = t
            out.append(dict(i=t, date=m.index[t], level=L, years=(t - start) / 12, touches=touches, dip=dip,
                            vol_ratio=_vol_ratio(vol, t, 12), close=cl[t], base_low=float(lo[start:t].min())))
    return out


def base_state(m, recent=6, near=.15, retest=.10, **kw):
    """Where a stock stands against its long base now:
       near      a ceiling within `near` above the last close (not yet broken)
       breakout  closed above it within the last `recent` months and still within `retest` of the level -> retest zone
       running   broke out within `recent` months and is now further above
    -> dict or None."""
    t = len(m) - 1
    if t < 36:
        return None
    hi, cl, lo = m.high.to_numpy(), m.close.to_numpy(), m.low.to_numpy()
    ev = [e for e in base_events(m, **kw) if e["i"] >= t - recent]
    if ev:
        e = ev[-1]
        up = cl[t] / e["level"] - 1
        return dict(state="retest" if up <= retest else "running", level=e["level"], years=e["years"], touches=e["touches"],
                    dip=e["dip"], gap=-up, since=e["date"], vol_ratio=e["vol_ratio"], base_low=e["base_low"])
    cpiv, clpiv = pivots(m.high, k=2), pivots(m.close, k=2)
    cands = sorted({c for c in cl[clpiv[clpiv < t - 2]] if cl[t] <= c <= cl[t] * (1 + near)})
    lv = _ceiling(t + 1, cl, hi, lo, cpiv, cands, **kw)
    if not lv:
        return None
    L, start, touches, dip = lv
    return dict(state="near", level=L, years=(t + 1 - start) / 12, touches=touches, dip=dip, gap=L / cl[t] - 1,
                vol_ratio=float(m.volume.iloc[-3:].mean() / m.volume.iloc[-39:-3].mean()) if len(m) > 40 else np.nan,
                base_low=float(lo[start:t + 1].min()))


# ------------------------------------------------------------------ 2. falling trendline (weekly)
def trendline_events(w, lookback=78, min_age=12, tol=.01, touch=.025, min_touches=2, k=2):
    """First close above a falling line through >= `min_touches` lower highs (weekly bars, or daily with **SWING).
    -> list of dict."""
    hi, cl, lo = w.high.to_numpy(), w.close.to_numpy(), w.low.to_numpy()
    piv = pivots(w.high, k=k)
    out, last = [], -99
    for t in range(min_age + 10, len(w)):
        if t - last < 8 or not (cl[t] > cl[t - 4:t].max()):
            continue
        ln = _line(t, piv, hi, cl, lookback, min_age, tol, touch, min_touches)
        if ln is None:
            continue
        a, slope, at_t, touches = ln
        if cl[t] > at_t and cl[t - 1] <= at_t - slope:
            last = t
            out.append(dict(i=t, date=w.index[t], level=at_t, anchor=hi[a], bars=t - a, touches=touches,
                            vol_ratio=_vol_ratio(w.volume.to_numpy(), t, 20), close=cl[t],
                            swing_low=float(lo[max(0, t - 6):t].min())))
    return out


def _line(t, piv, hi, cl, lookback, min_age, tol, touch, min_touches):
    p = piv[(piv < t - 1) & (piv >= t - lookback)]
    if len(p) < min_touches:
        return None
    best = None
    for a in p[hi[p].argsort()[::-1][:3]]:  # anchor on one of the 3 highest swing highs
        if t - a < min_age:
            continue
        for b in p[p > a + 3]:
            if hi[b] >= hi[a]:
                continue
            slope = (hi[b] - hi[a]) / (b - a)
            line = hi[a] + slope * (np.arange(a, t) - a)
            if (cl[a:t] > line * (1 + tol)).any():  # a close above the line before t: not respected
                continue
            later = p[p >= a]
            on = np.abs(hi[later] - (hi[a] + slope * (later - a))) <= touch * (hi[a] + slope * (later - a))
            k = int(on.sum())
            if k >= min_touches and (best is None or k > best[3]):
                best = (int(a), float(slope), float(hi[a] + slope * (t - a)), k)
    return best


SWING = dict(lookback=60, min_age=10, k=3)   # daily chart: a falling line 2-12 weeks old (swing trades)
POSITION = dict(lookback=78, min_age=12, k=2)  # weekly chart: 3 months to 18 months old


def trendline_state(w, near=.06, **kw):
    """The falling line overhead now (if any): its value on the last bar and how far above the close it is.
    Works on weekly bars (default) or daily bars with **SWING."""
    t = len(w) - 1
    hi, cl = w.high.to_numpy(), w.close.to_numpy()
    ln = _line(t, pivots(w.high, k=kw.get("k", 2)), hi, cl, kw.get("lookback", 78), kw.get("min_age", 12), kw.get("tol", .01),
               kw.get("touch", .025), kw.get("min_touches", 2))
    if ln is None:
        return None
    a, slope, at_t, touches = ln
    gap = at_t / cl[t] - 1
    if gap < -near or gap > near:
        return None
    return dict(state="breakout" if gap < 0 else "near", level=at_t, anchor=float(hi[a]), bars=t - a, touches=touches,
                gap=gap, swing_low=float(w.low.iloc[max(0, t - 6):t + 1].min()))


# ------------------------------------------------------------------ 3. box (weekly)
def box_events(w, weeks=26, max_range=.35):
    """First weekly close above a 26-week box whose top is within 35% of its bottom. -> list of dict."""
    hi, cl, lo = w.high.to_numpy(), w.close.to_numpy(), w.low.to_numpy()
    out, last = [], -99
    for t in range(weeks + 1, len(w)):
        top, bot = hi[t - weeks:t].max(), lo[t - weeks:t].min()
        if cl[t] > top >= cl[t - 1] and top / bot - 1 <= max_range and t - last > weeks // 2:
            last = t
            out.append(dict(i=t, date=w.index[t], level=float(top), floor=float(bot), weeks=weeks,
                            vol_ratio=_vol_ratio(w.volume.to_numpy(), t, 20), close=cl[t]))
    return out


def box_state(w, weeks=26, max_range=.35, near=.05):
    """A 26-week box now: 'near' if the close is within `near` under its top, 'breakout' if the last week closed
    above it (first close above). -> dict or None."""
    if len(w) < weeks + 2:
        return None
    hi, cl, lo = w.high.to_numpy(), w.close.to_numpy(), w.low.to_numpy()
    t = len(w) - 1
    for end, state in ((t, "breakout"), (t + 1, "near")):  # box ending before the last bar, or including it
        top, bot = hi[end - weeks:end].max(), lo[end - weeks:end].min()
        if top / bot - 1 > max_range:
            continue
        if state == "breakout" and cl[t] > top >= cl[t - 1]:
            return dict(state=state, level=float(top), floor=float(bot), gap=top / cl[t] - 1,
                        vol_ratio=_vol_ratio(w.volume.to_numpy(), t, 20))
        if state == "near" and 0 <= top / cl[t] - 1 <= near:
            return dict(state=state, level=float(top), floor=float(bot), gap=top / cl[t] - 1,
                        vol_ratio=float(w.volume.iloc[-4:].mean() / w.volume.iloc[-24:-4].mean()))
    return None


# ------------------------------------------------------------------ evidence, grades, live scan
def evidence():
    return json.loads(EVIDENCE.read_text(encoding="utf-8")) if EVIDENCE.exists() else {"patterns": {}}


def odds(pattern, gain, ev=None):
    """Share of past breakouts of `pattern` whose best gain (before the stop or the time limit) reached `gain`."""
    q = (ev or evidence())["patterns"].get(pattern, {}).get("maxgain_quantiles")
    if not q:
        return np.nan
    return float(1 - np.searchsorted(q, gain, side="left") / (len(q) - 1))


def grade(setup, pattern, touches, growth_label):
    grower = growth_label in ("LEADER", "EMERGING")
    if setup.startswith("box") and grower:
        return "A"
    if setup.startswith("box") or (pattern == "base" and (touches or 0) >= 4):
        return "B"
    return "C"


def scan_one(args):
    sym, d, tg = args
    dd = d.set_index("date")[["open", "high", "low", "close", "volume"]]
    if len(dd) < 140 or (dd.close * dd.volume).iloc[-60:].median() < MIN_TURNOVER:
        return []
    last = float(dd.close.iloc[-1])
    m, w = resample(dd, "ME"), resample(dd, "W-FRI")
    rows = []

    def plan(setup, kind, s, trigger, stop):
        t = tg.get(KIND[kind], {})
        t1, t2 = t.get("target1", np.nan), t.get("target2", np.nan)
        base = max(trigger, last)
        risk = 1 - stop / base if stop and base else np.nan
        rows.append(dict(symbol=sym, setup=setup, pattern=KIND[kind], close=last, level=s["level"], gap=s.get("gap"),
                         trigger=trigger, stop=stop, risk=risk, target1=base * (1 + t1), target2=base * (1 + t2),
                         reward_risk=t1 / risk if risk and risk > 0 else np.nan, touches=s.get("touches"), years=s.get("years"),
                         bars=s.get("bars"), vol_ratio=s.get("vol_ratio"), anchor=s.get("anchor"), date=dd.index[-1].date()))

    x = box_state(w)
    if x:
        plan(f"box_{'break' if x['state'] == 'breakout' else 'near'}", "box", x, x["level"], x["floor"])
    b = base_state(m)
    if b:
        plan(f"base_{b['state']}", "base", b, b["level"], b["level"] * .85)
    t = trendline_state(w, **POSITION)
    if t:
        plan("line_break" if t["state"] == "breakout" else "line_near", "line", t, t["level"], t["swing_low"])
    s = trendline_state(dd, **SWING)
    if s:
        plan("swing_break" if s["state"] == "breakout" else "swing_near", "swing", s, s["level"], s["swing_low"])
    return rows


def daily(symbols=None):
    """Split-adjusted daily candles from Rising's database: {symbol: DataFrame(date, open, high, low, close, volume)}."""
    from data_sync import adjust_prices
    c = open_db()
    try:
        where = " where i.symbol in (" + ",".join("?" * len(symbols)) + ")" if symbols else ""
        args = list(symbols or [])
        d = c.execute("""select i.symbol, d.trade_date date, d.open, d.high, d.low, d.close, d.volume from daily_market d
                         join instruments i using (isin)""" + where, args).df()
        try:  # older Upstox candles, if long_history.py has been run
            old = c.execute("""select i.symbol, l.date, l.open, l.high, l.low, l.close, l.volume from long_daily l
                               join instruments i using (isin)""" + where, args).df()
        except Exception:
            old = pd.DataFrame(columns=d.columns)
    finally:
        c.close()
    d["date"] = pd.to_datetime(d.date)
    old["date"] = pd.to_datetime(old.date)
    old = dict(tuple(old.groupby("symbol")))
    out = {}
    for sym, g in d.sort_values("date").groupby("symbol"):
        g = g.drop_duplicates("date").reset_index(drop=True)
        f = adjust_prices(g.close) / g.close  # the same factor scales open/high/low, and volume the other way
        g = g.assign(open=g.open * f, high=g.high * f, low=g.low * f, close=g.close * f,
                     volume=g.volume / f).dropna(subset=["close"])
        out[sym] = stitch(old[sym], g) if sym in old else g
    return out


def stitch(old, new, n=20):
    """Put older candles in front of Rising's, rescaled by the median price ratio over their first n shared days (this
    absorbs any split since the old ones were fetched). Volume is scaled the other way. No clean overlap: new only."""
    both = new.merge(old, on="date", suffixes=("", "_o")).head(n)
    if len(both) < 5:
        return new
    ratio = (both.close / both.close_o).median()
    if not ((both.close / both.close_o / ratio - 1).abs() < .03).mean() >= .8:  # the two sources disagree: don't guess
        return new
    old = old[old.date < new.date.iloc[0]].sort_values("date")
    if old.empty or (new.date.iloc[0] - old.date.iloc[-1]).days > 10:  # a hole at the seam (e.g. a reused ISIN)
        return new
    old = old.assign(**{k: old[k] * ratio for k in ("open", "high", "low", "close")}, volume=old.volume / ratio)
    return pd.concat([old, new], ignore_index=True)


def scan(log=print):
    tg = evidence()["patterns"]
    data = daily()
    rows = []
    with ProcessPoolExecutor() as ex:
        for r in ex.map(scan_one, [(s, g, tg) for s, g in data.items()], chunksize=16):
            rows += r
    sc = pd.DataFrame(rows, columns=["symbol", "setup", "pattern", "close", "level", "gap", "trigger", "stop", "risk", "target1",
                                     "target2", "reward_risk", "touches", "years", "bars", "vol_ratio", "anchor", "date"])
    board = DATA / "rising_latest.csv"
    lab = pd.read_csv(board)[["symbol", "status"]] if board.exists() else pd.DataFrame(columns=["symbol", "status"])
    sc = sc.merge(lab[lab.status != "BROKE"].rename(columns={"status": "growth_label"}), on="symbol", how="left")
    sc["grade"] = [grade(a, b, c, d) for a, b, c, d in zip(sc.setup, sc.pattern, sc.touches, sc.growth_label)]
    sc = sc.sort_values(["grade", "pattern", "gap"])
    sc.pipe(save_csv, OUT, index=False)
    log(f"   breakouts: {len(sc)} live setups · grade A {int((sc.grade == 'A').sum())} · B {int((sc.grade == 'B').sum())}")
    return sc


if __name__ == "__main__":
    scan()
