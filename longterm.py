"""Long-term lens: for someone holding 4-5 years, what known on the day you buy came before a stock doubling?

The evidence (longterm_evidence.json) comes from the long-term lab: 1,078 NSE companies judged only on what was public
on 30 Sep 2017-2021, followed 5 years. Findings: spreading over 5 stocks cut the chance of losing money from ~1 in 5
to ~1 in 25; stocks cheap for their sales (P/S) doubled more often in every buy year; "good company" checks alone did
not raise the odds of doubling (they trimmed big falls); loss-makers lost money most often.

This module scores today's stocks with the same checks and groups, from the Screener pages Rising caches in
data/fundamentals/ (12 years of yearly results).

    python longterm.py              # score every cached page -> data/longterm.csv
    python longterm.py fetch        # one-time: fetch pages for every listed stock (~1 hour, resumable)
    python longterm.py TITAN ...    # score and print these
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from config import DATA, open_db
from safeio import save_csv

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "longterm_evidence.json"
OUT = DATA / "longterm.csv"
MIN_TURNOVER = 0.25  # ₹ crore a day: the study left thinner stocks out
PAGE_AGE = 120  # days a cached Screener page is good for here (yearly results change once a year)

# ---- the checks, exactly as fixed in advance in the lab (do not tune: the evidence was measured with these)
CHECKS = {
    "sales rose every year (last 3)": lambda d: d.sales_up == 3,
    "sales grew 10%+ a year (3 yrs)": lambda d: d.sales_g3 >= .10,
    "profit grew 10%+ a year (3 yrs)": lambda d: d.profit_g3 >= .10,
    "no loss in the last 4 years": lambda d: d.loss_years == 0,
    "earns 15%+ on its capital (3-yr avg)": lambda d: d.roce_avg3 >= 15,
    "debt below half of equity": lambda d: d.de < .5,
    "profit backed by cash (70%+)": lambda d: d.cash_conv >= .7,
    "margins not shrinking (3 yrs)": lambda d: d.opm_chg3 >= 0,
    "building capacity (assets +10%/yr)": lambda d: d.assets_g3 >= .10,
    "price not stretched (P/E under 40)": lambda d: d.pe < 40,
    "not already up 100%+ in a year": lambda d: d.past_1y < 1,
}
CORE = ["sales rose every year (last 3)", "sales grew 10%+ a year (3 yrs)", "no loss in the last 4 years",
        "earns 15%+ on its capital (3-yr avg)", "debt below half of equity", "profit backed by cash (70%+)"]
PLAIN = dict(zip(CORE, ["Is it selling more every year?", "Are sales growing at least 10% a year?",
                        "Has it stayed profitable (no loss in 4 years)?",
                        "Does it earn well on the money it uses (15%+ return on capital)?",
                        "Is its debt small (under half of its own money)?", "Is the profit real cash, not just on paper?"]))
GROUPS = {
    "cheap for its sales (cheapest 40% by P/S)": lambda d: d.ps_rank <= .4,
    "cheap for its sales + 4 of 6 core checks": lambda d: (d.ps_rank <= .4) & (d.core >= 4),
    "4 of 6 core checks (quality alone)": lambda d: d.core >= 4,
    "P/E under 40 and not up 100%+ in a year": lambda d: (d.pe < 40) & (d.past_1y < 1),
    "loss-making when bought": lambda d: d.pe.isna(),
}


def evidence():
    return json.loads(EVIDENCE.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ facts from one Screener page
def tables(page):
    """fundamentals.get() result -> {annual, balance, cashflow, ratios} with fiscal year-end Timestamp columns, + fin flag."""
    out = {}
    for k in ("annual", "balance", "cashflow", "ratios"):
        t = page["tables"].get(k)
        if t is None:
            continue
        cols = pd.to_datetime(pd.Series(t.columns, dtype=str), format="%b %Y", errors="coerce") + pd.offsets.MonthEnd(0)
        t = t.copy()
        t.columns = cols
        out[k] = t.loc[:, cols.notna().to_numpy()].apply(pd.to_numeric, errors="coerce")
    if "annual" not in out:
        return None
    ye = set(out["annual"].columns)  # fiscal year-ends only (drops a balance sheet's half-year column)
    out = {k: t.loc[:, [c for c in t.columns if c in ye]] for k, t in out.items()}
    r = out.get("ratios")
    out["fin"] = r is None or "ROCE %" not in r.index  # banks and lenders: the study left them out
    return out


def _row(t, name, cols):
    return t.loc[name, cols] if t is not None and name in t.index else pd.Series(np.nan, index=cols)


def _cagr(a, b, years):
    return (b / a) ** (1 / years) - 1 if a > 0 and b > 0 else np.nan


def traits(T, entry):
    """Facts from the yearly results public at `entry` (year-ends at least 90 days old). Same as the lab's panel."""
    a = T["annual"]
    cols = [c for c in a.columns if c <= entry - pd.Timedelta(days=90)][-6:]
    if len(cols) < 2:
        return None
    s, p, opm = _row(a, "Sales", cols), _row(a, "Net Profit", cols), _row(a, "OPM %", cols)
    b, cf, r = T.get("balance"), T.get("cashflow"), T.get("ratios")
    bc = [c for c in cols if b is not None and c in b.columns]
    eq = (_row(b, "Equity Capital", bc) + _row(b, "Reserves", bc)) if bc else pd.Series(dtype=float)
    k = lambda x, n: x.iloc[-1 - n] if len(x) > n else np.nan
    fa = (_row(b, "Fixed Assets", bc) + _row(b, "CWIP", bc).fillna(0)) if bc else pd.Series(dtype=float)
    roce = _row(r, "ROCE %", [c for c in cols if r is not None and c in r.columns])
    cfo = _row(cf, "Cash from Operating Activity", [c for c in cols if cf is not None and c in cf.columns])
    last3 = cols[-3:]
    return dict(
        fy=cols[-1], years=len(cols), fin=T["fin"], sales=s.iloc[-1], profit=p.iloc[-1],
        sales_g3=_cagr(k(s, 3), s.iloc[-1], 3), profit_g3=_cagr(k(p, 3), p.iloc[-1], 3),
        sales_up=int((s.diff().iloc[-3:] > 0).sum()) if len(s) >= 4 else np.nan,
        loss_years=int((p.iloc[-4:] <= 0).sum()), opm=opm.iloc[-1], opm_chg3=opm.iloc[-1] - k(opm, 3),
        roce_avg3=roce.iloc[-3:].mean() if len(roce) >= 3 else np.nan,
        de=_row(b, "Borrowings", bc).iloc[-1] / eq.iloc[-1] if len(eq) and eq.iloc[-1] > 0 else np.nan,
        cash_conv=cfo.reindex(last3).sum() / p.reindex(last3).sum() if p.reindex(last3).sum() > 0 and len(cfo) else np.nan,
        assets_g3=_cagr(k(fa, 3), fa.iloc[-1], 3) if len(fa) else np.nan)


def price_facts(px):
    """px: daily close/volume indexed by date (breakouts.daily). -> price, past_1y, turnover (₹ Cr a day)."""
    c = px.close
    if len(c) < 200:
        return None
    return dict(price=c.iloc[-1], past_1y=c.iloc[-1] / c.iloc[-min(252, len(c))] - 1,
                turnover_cr=(px.close * px.volume).iloc[-63:].median() / 1e7)


def _finish(d, ref=None):
    """Valuation, P/S rank (among `ref`'s liquid non-financial stocks, else d's own), checks, core count, groups."""
    d = d.copy()
    d["pe"] = d.mcap / d.profit.where(d.profit > 0)
    d["ps"] = d.mcap / d.sales.where(d.sales > 0)
    d["studied"] = ~d.fin.astype(bool) & (d.turnover_cr >= MIN_TURNOVER)
    base = (ref if ref is not None else d)
    pool = np.sort(base.ps[base.studied & base.ps.notna()].to_numpy())
    d["ps_rank"] = [np.searchsorted(pool, x, side="right") / len(pool) if pd.notna(x) and len(pool) else np.nan for x in d.ps]
    for name, f in CHECKS.items():
        d[name] = f(d).fillna(False).astype(bool)
    d["core"] = sum(d[c].astype(int) for c in CORE)
    d["groups"] = ["; ".join(g for g, f in GROUPS.items() if bool(f(d.loc[[i]]).fillna(False).iloc[0])) for i in d.index]
    return d


def _score_rows(symbols, max_age=PAGE_AGE):
    import breakouts
    import fundamentals
    c = open_db()
    mc = dict(c.execute("select symbol, market_cap_cr from instruments").fetchall())
    c.close()
    px = breakouts.daily(list(symbols))
    now = pd.Timestamp.today().normalize()
    rows = []
    for s in symbols:
        page = fundamentals.get(s, max_age_days=max_age)
        T = tables(page) if page else None
        t = traits(T, now) if T else None
        p = price_facts(px[s].set_index("date")) if s in px else None
        if t and p:
            rows.append(dict(symbol=s, mcap=mc.get(s), **t, **p))
    return pd.DataFrame(rows)


def score(log=print):
    """Score every stock with a cached Screener page (no network) -> data/longterm.csv."""
    from fundamentals import CACHE
    syms = sorted(p.stem for p in CACHE.glob("*.json"))
    d = _finish(_score_rows(syms, max_age=10 ** 6))
    save_csv(d.drop(columns=["fy"]).assign(fy=d.fy.dt.strftime("%Y-%m-%d")), OUT, index=False)
    log(f"   long-term: {len(d)} stocks scored · {int(d.studied.sum())} like the ones studied")
    return d


def load():
    if not OUT.exists():
        return pd.DataFrame()
    d = pd.read_csv(OUT)
    d["fy"] = pd.to_datetime(d.fy)
    d["groups"] = d.groups.fillna("")
    return d


def check(sym, ref=None):
    """Score one stock now (fetches its Screener page if not cached), ranked against today's saved scores."""
    ref = load() if ref is None else ref
    d = _score_rows([sym])
    return None if d.empty else _finish(d, ref if len(ref) else None).iloc[0]


def fetch(log=print, pause=1.2):
    """One-time: cache Screener pages for every listed stock that has none (or older than PAGE_AGE days)."""
    import fundamentals
    c = open_db()
    syms = [r[0] for r in c.execute("select symbol from instruments where isin like 'INE%' order by 1").fetchall()]
    c.close()
    todo = [s for s in syms if not (fundamentals.CACHE / f"{s}.json").exists()]
    log(f"{len(todo)} company pages to fetch")
    for n, s in enumerate(todo, 1):
        try:
            fundamentals.get(s, max_age_days=PAGE_AGE)
        except Exception as e:
            log(f"   {s}: {str(e)[:80]}")
        if n % 100 == 0:
            log(f"   {n}/{len(todo)}")
        time.sleep(pause)
    score(log)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a == ["fetch"]:
        fetch()
    elif a:
        ref = load()
        for s in a:
            r = check(s.upper(), ref)
            if r is None:
                print(s, "— not scored (no Screener page or under ~10 months of prices)")
                continue
            print(f"\n{r.symbol}: {r.core}/6 good-company checks · P/S rank {r.ps_rank:.0%} (0% = cheapest) · {r.groups or 'no group'}")
            for c_, q in PLAIN.items():
                print(f"   {'yes' if r[c_] else 'no '}  {q}")
    else:
        score()
