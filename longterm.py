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

# ---- round 2 (long-term lab, study2.py): cheap first, then a healthy, improving business. Chosen on buy years
# 2017-2020, confirmed on hidden 2021 / 2022: vs stocks bought the same day, "cheap + healthy + improving" doubled
# 8 points more often and lost money 6 points less often (5/5 years); trap warnings each raised the chance of losing.
SIGNALS = {  # signs of real potential: an improving business and a price that agrees
    "growth speeding up": lambda d: d.sales_accel > 0,
    "profit up 15%+ last year": lambda d: d.profit_yoy >= .15,
    "price above its 200-day average": lambda d: d.above_200 == 1,
    "strong 12-month price trend": lambda d: d.mom_rank >= .6,
    "cheaper than its usual P/E": lambda d: d.pe_own < .8,
}
WARNINGS = {  # value traps: shown next to the stock, not hidden (some were big winners, but far more often losers)
    "weak F-score": lambda d: d.fscore <= 3,
    "can barely pay interest": lambda d: (d.int_cover < 2) & (d.debt > 0),
    "falling knife": lambda d: (d.past_1y <= -.4) & (d.profit_fell == 1),
    "price crashed 50%+ in a year": lambda d: d.past_1y <= -.5,  # lab: lost money 10 pts more often, even with profits fine
    "already up 150%+ in a year": lambda d: d.past_1y >= 1.5,    # lab: lost money 8 pts more often, worse in 3/3 years
    "many new shares": lambda d: d.shares_g3 > .25,
    "tiny or penny": lambda d: (d.mcap < 200) | (d.price < 10),
    "loss-making": lambda d: d.pe.isna(),
}


def healthy(d):
    """Cheap for its sales, F-score 6+ of 9, profitable, and able to pay its interest (2x+) or no debt."""
    return (d.ps_rank <= .4) & (d.fscore >= 6) & d.pe.notna() & ((d.int_cover >= 2) | (d.debt <= 0))


# The list view: every stock sorted into the best tested group it belongs to (first match wins); each shows its
# group's past odds from longterm_evidence.json["round2"]["tiers"], so nothing here is an untested rule.
TIERS = [  # (name, plain meaning, evidence key, test)
    ("cheap + healthy + improving", "cheap for its sales, healthy business (F-score 6+, profitable, pays its interest), "
     "and 3+ of 5 improving signs", "cheap + healthy + improving", lambda d: healthy(d) & (d.n_signals >= 3)),
    ("cheap + healthy", "the same, with 2 of 5 improving signs", "cheap + healthy", lambda d: healthy(d) & (d.n_signals >= 2)),
    ("cheap", "cheap for its sales and profitable", "cheap", lambda d: (d.ps_rank <= .4) & d.pe.notna()),
    ("fair price", "P/E under 40 and not already up 100%+ in a year", "fair price", lambda d: (d.pe < 40) & (d.past_1y < 1)),
    ("strong but pricey", "4+ of 6 good-company checks, but not cheap", "strong but pricey", lambda d: (d.core >= 4) & d.pe.notna()),
    ("loss-making", "made a loss in its latest year", "loss-making", lambda d: d.pe.isna()),
]


def tier(d):
    """-> tier number (1 = best past odds … len(TIERS)), or len(TIERS) + 1 for 'none of the tested groups'."""
    out = pd.Series(len(TIERS) + 1, index=d.index)
    for i, (_, _, _, f) in reversed(list(enumerate(TIERS, 1))):  # reversed: the first (best) match ends up on top
        out[f(d).fillna(False).astype(bool)] = i
    return out


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


def extra(T, entry):
    """Round-2 facts (value traps, improving business, Piotroski F-score), same point-in-time rule as traits().
    F-score (Piotroski 2000), adapted to Screener's yearly tables: 1 profit > 0, 2 cash from operations > 0,
    3 return on assets up, 4 cash from operations > profit, 5 borrowings / assets down, 6 cash-conversion cycle not
    longer (stand-in for the current ratio, which Screener doesn't give), 7 no new shares (net profit / EPS not up 2%+),
    8 operating margin up, 9 sales / assets up. Each needs this year and last year; missing data = not scored."""
    a = T["annual"]
    cols = [c for c in a.columns if c <= entry - pd.Timedelta(days=90)][-6:]
    if len(cols) < 2:
        return {}
    b, cf, r = T.get("balance"), T.get("cashflow"), T.get("ratios")
    g = lambda t, name: _row(t, name, [c for c in cols if t is not None and c in t.columns]).reindex(cols)
    s, p, opm, eps = g(a, "Sales"), g(a, "Net Profit"), g(a, "OPM %"), g(a, "EPS in Rs")
    intr, pbt, dep, op = g(a, "Interest"), g(a, "Profit before tax"), g(a, "Depreciation"), g(a, "Operating Profit")
    ta, debt = g(b, "Total Assets"), g(b, "Borrowings")
    cfo, roce, ccc = g(cf, "Cash from Operating Activity"), g(r, "ROCE %"), g(r, "Cash Conversion Cycle")
    shares = (p / eps).where((p.abs() > 0) & (eps.abs() > 0))
    roa, lev, turn = p / ta, debt / ta, s / ta
    up = lambda x: np.nan if pd.isna(x.iloc[-1]) or pd.isna(x.iloc[-2]) else float(x.iloc[-1] > x.iloc[-2])
    f = [np.nan if pd.isna(p.iloc[-1]) else float(p.iloc[-1] > 0),
         np.nan if pd.isna(cfo.iloc[-1]) else float(cfo.iloc[-1] > 0),
         up(roa),
         np.nan if pd.isna(cfo.iloc[-1]) or pd.isna(p.iloc[-1]) else float(cfo.iloc[-1] > p.iloc[-1]),
         np.nan if pd.isna(lev.iloc[-1]) or pd.isna(lev.iloc[-2]) else float(lev.iloc[-1] <= lev.iloc[-2]),
         np.nan if pd.isna(ccc.iloc[-1]) or pd.isna(ccc.iloc[-2]) else float(ccc.iloc[-1] <= ccc.iloc[-2]),
         np.nan if pd.isna(shares.iloc[-1]) or pd.isna(shares.iloc[-2]) else float(shares.iloc[-1] <= shares.iloc[-2] * 1.02),
         up(opm), up(turn)]
    known = [x for x in f if not pd.isna(x)]
    k = lambda x, n: x.iloc[-1 - n] if len(x) > n else np.nan
    yoy = lambda x: x.iloc[-1] / x.iloc[-2] - 1 if x.iloc[-2] > 0 and x.iloc[-1] > -np.inf else np.nan
    return dict(
        fscore=sum(known) if len(known) >= 7 else np.nan, fscore_n=len(known),   # out of the signals that could be scored
        sales_yoy=yoy(s), profit_yoy=yoy(p) if p.iloc[-2] > 0 else np.nan,
        profit_fell=float(p.iloc[-1] < p.iloc[-2]) if not pd.isna(p.iloc[-2]) else np.nan,
        sales_accel=(yoy(s) - _cagr(k(s, 3), s.iloc[-1], 3)) if len(s.dropna()) >= 4 else np.nan,  # last year vs 3-yr pace
        roce_chg2=roce.iloc[-1] - k(roce, 2), opm_chg2=opm.iloc[-1] - k(opm, 2), roa_chg=roa.iloc[-1] - roa.iloc[-2],
        int_cover=(pbt.iloc[-1] + intr.iloc[-1]) / intr.iloc[-1] if intr.iloc[-1] > 0 else np.inf,
        shares_g3=shares.iloc[-1] / k(shares, 3) - 1 if not pd.isna(k(shares, 3)) and k(shares, 3) > 0 else np.nan,
        ebit=(op.iloc[-1] - dep.iloc[-1]) if not pd.isna(op.iloc[-1]) else np.nan, debt=debt.iloc[-1],
        profit_hist=json.dumps({c.strftime("%Y-%m-%d"): v for c, v in p.dropna().items()}))  # for P/E vs its own past


def pe_own(profit_hist, px, entry, mcap_now, pe_now):
    """Today's P/E divided by the median of its own year-end P/Es over the past (up to 5) years. <1 = cheaper than usual."""
    if pd.isna(pe_now) or not mcap_now:
        return np.nan
    past = []
    for d_, prof in json.loads(profit_hist).items():
        d_ = pd.Timestamp(d_)
        c = px.close[:d_ + pd.Timedelta(days=90)]  # price when that year's results were out
        if d_ >= entry - pd.Timedelta(days=90) or prof <= 0 or c.empty or d_ < entry - pd.DateOffset(years=6):
            continue
        past.append(mcap_now * c.iloc[-1] / px.close.iloc[-1] / prof)
    return pe_now / np.median(past) if len(past) >= 3 else np.nan


def price_facts(px):
    """px: daily close/volume indexed by date (breakouts.daily). -> price, past_1y, turnover (₹ Cr a day)."""
    c = px.close
    if len(c) < 200:
        return None
    n = min(252, len(c))
    return dict(price=c.iloc[-1], past_1y=c.iloc[-1] / c.iloc[-n] - 1, past_12_1=c.iloc[-21] / c.iloc[-n] - 1,
                above_200=float(c.iloc[-1] > c.iloc[-200:].mean()),
                turnover_cr=(px.close * px.volume).iloc[-63:].median() / 1e7)


def _finish(d, ref=None):
    """Valuation, P/S rank (among `ref`'s liquid non-financial stocks, else d's own), checks, core count, groups."""
    d = d.copy()
    d["pe"] = d.mcap / d.profit.where(d.profit > 0)
    d["ps"] = d.mcap / d.sales.where(d.sales > 0)
    d["studied"] = ~d.fin.astype(bool) & (d.turnover_cr >= MIN_TURNOVER) & ~d.symbol.isin(_financials())
    base = (ref if ref is not None else d)
    rank = lambda col: (lambda pool: [np.searchsorted(pool, x, side="right") / len(pool) if pd.notna(x) and len(pool) else np.nan
                                      for x in d[col]])(np.sort(base[col][base.studied & base[col].notna()].to_numpy()))
    d["ps_rank"], d["mom_rank"] = rank("ps"), rank("past_12_1")
    d["n_signals"] = sum(f(d).fillna(False).astype(int) for f in SIGNALS.values())
    d["signals"] = ["; ".join(k for k, f in SIGNALS.items() if bool(f(d.loc[[i]]).fillna(False).iloc[0])) for i in d.index]
    surv, flags = _surveillance(), _filing_flags()
    d["warnings"] = ["; ".join([k for k, f in WARNINGS.items() if bool(f(d.loc[[i]]).fillna(False).iloc[0])]
                               + (["on NSE watch list"] if d.symbol[i] in surv else [])
                               + ([f"serious filing: {flags[d.symbol[i]]}"] if d.symbol[i] in flags else [])) for i in d.index]
    for name, f in CHECKS.items():
        d[name] = f(d).fillna(False).astype(bool)
    d["core"] = sum(d[c].astype(int) for c in CORE)
    d["groups"] = ["; ".join(g for g, f in GROUPS.items() if bool(f(d.loc[[i]]).fillna(False).iloc[0])) for i in d.index]
    d["tier"] = tier(d)
    return d


SERIOUS = r"show cause|sebi order|adjudicat|penalt|fraud|forensic|raid|search and seizure|insolvency|nclt|winding|"           r"default|qualified opinion|resignation of (?:the )?(?:statutory )?auditor|whistle"


def _filing_flags(days=120):
    """{symbol: what} for serious announcements in the filings Rising stores (live only; the store keeps ~2 months)."""
    try:
        c = open_db()
        try:
            f = c.execute("select symbol, category, headline from corporate_filings where filing_datetime >= now() - ?::interval "
                          "order by filing_datetime desc", [f"{days} days"]).df()
        finally:
            c.close()
        from brief import BENIGN
        txt = (f.category.fillna("") + " " + f.headline.fillna("")).str.lower()
        what = txt.str.extract(f"({SERIOUS})", expand=False)
        hit = f.assign(what=what)[what.notna() & ~txt.str.contains(BENIGN, regex=True)]
        return dict(hit.drop_duplicates("symbol")[["symbol", "what"]].values)
    except Exception:
        return {}


def _financials():
    """Insurers, lenders and holding companies (Screener's sector): left out of the study, so not scored as if tested."""
    try:
        m = pd.read_csv(DATA / "screener_meta.csv", usecols=["symbol", "sector"])
        return set(m.symbol[m.sector == "Financial Services"])
    except Exception:
        return set()


def _surveillance():
    """Stocks on NSE's ASM/GSM watch lists today (live only: there's no history to test it on)."""
    try:
        import insights
        return set(insights.surveillance())
    except Exception:
        return set()


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
        ps_ = px[s].set_index("date") if s in px else None
        p = price_facts(ps_) if ps_ is not None else None
        if t and p:
            x = extra(T, now)
            pe_now = mc.get(s) / t["profit"] if mc.get(s) and t["profit"] > 0 else np.nan
            own = pe_own(x["profit_hist"], ps_, now, mc.get(s), pe_now) if "profit_hist" in x else np.nan
            rows.append(dict(symbol=s, mcap=mc.get(s), **t, **p, **{k: v for k, v in x.items() if k != "profit_hist"}, pe_own=own))
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
    for c in ("groups", "signals", "warnings"):
        d[c] = d[c].fillna("") if c in d else ""
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
