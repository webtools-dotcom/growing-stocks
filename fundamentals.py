"""Full fundamentals for one stock (Screener.in) + a plain-English growth checklist.

Cached per symbol in data/fundamentals/ for 7 days, fetched on demand when a stock is opened.
Usage: python fundamentals.py SAMBHV
"""
import io
import json
import re
import sys
from datetime import date

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import DATA
from screener_refresh import H, table

CACHE = DATA / "fundamentals"
SECTIONS = {"quarters": "quarters", "annual": "profit-loss", "balance": "balance-sheet",
            "cashflow": "cash-flow", "ratios": "ratios"}


def _ranges(soup):
    """Compounded growth tables: {'Compounded Sales Growth': {'3 Years': 37.0, 'TTM': 42.0}, ...}"""
    out = {}
    for t in soup.select("#profit-loss table.ranges-table"):
        df = pd.read_html(io.StringIO(str(t)))[0]
        out[df.columns[0]] = {str(k).rstrip(":"): float(pd.to_numeric(str(v).rstrip("%"), errors="coerce"))
                              for k, v in zip(df.iloc[:, 0], df.iloc[:, 1])}
    return out


def _shareholding(soup):
    sec = soup.select_one("#quarterly-shp table")
    if sec is None:
        return None
    t = pd.read_html(io.StringIO(str(sec)))[0]
    t = t.set_index(t.columns[0])
    t.index = [re.split(r"\s{2,}", str(s).replace("\xa0", " "))[0].rstrip(" +").strip() for s in t.index]
    return t.apply(lambda c: pd.to_numeric(c.astype(str).str.replace(",", "").str.replace("%", ""), errors="coerce"))


def fetch(sym):
    for path in ("consolidated/", ""):
        r = requests.get(f"https://www.screener.in/company/{sym}/{path}", headers=H, timeout=20)
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        q = table(soup, "quarters")
        if q is None or "Sales" not in q.index or q.loc["Sales"].fillna(0).eq(0).all():
            continue
        data = {k: table(soup, v) for k, v in SECTIONS.items()}
        data["shareholding"] = _shareholding(soup)
        name = soup.select_one("h1")
        clean = lambda el: re.sub(r"\s*\[\d+\]", "", el.get_text(" ", strip=True)) if el else ""
        return dict(symbol=sym, name=name.get_text(strip=True) if name else sym, basis=path.rstrip("/") or "standalone",
                    about=clean(soup.select_one(".about"))[:600], key_points=clean(soup.select_one(".commentary"))[:1500],
                    fetched=date.today().isoformat(), growth=_ranges(soup),
                    tables={k: v.to_json(orient="split") for k, v in data.items() if v is not None})
    return None


def get(sym, max_age_days=7, refresh=False):
    """-> dict with DataFrames under 'tables', or None if Screener has no page."""
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{sym.upper()}.json"
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    stale = d is None or "about" not in d or (date.today() - date.fromisoformat(d["fetched"])).days > max_age_days
    if refresh or stale:
        fresh = fetch(sym.upper())
        if fresh:
            p.write_text(json.dumps(fresh), encoding="utf-8")
            d = fresh
    if d is None:
        return None
    d = dict(d)
    # convert_axes=False: keep column labels like "Mar 2024" / "TTM" as text
    d["tables"] = {k: pd.read_json(io.StringIO(v), orient="split", convert_axes=False, convert_dates=False)
                   for k, v in d["tables"].items()}
    return d


# ------------------------------------------------------------------ growth checklist
def _row(df, name):
    return df.loc[name].dropna() if df is not None and name in df.index else pd.Series(dtype=float)


def _annual(s):
    """Drop TTM / half-year columns: keep fiscal year-ends (Mar ...) for year-on-year comparisons."""
    return s[[c for c in s.index if str(c).startswith("Mar")]]


def checklist(f):
    """-> list of (verdict, title, detail). verdict: 'pass' | 'watch' | 'fail' | 'n/a'."""
    T = f["tables"]
    q, a, bal, cf, rat, sh = (T.get(k) for k in ("quarters", "annual", "balance", "cashflow", "ratios", "shareholding"))
    out = []

    sales_q = _row(q, "Sales")
    if len(sales_q) >= 8:
        prev = sales_q.iloc[-8:-4].values
        yoy = sales_q.iloc[-4:].values / np.where(prev > 0, prev, np.nan) - 1  # no sales a year ago: no growth figure
        n = int((yoy > .10).sum())
        out.append(("pass" if n == 4 else "watch" if n == 3 else "fail", "Sales growing every quarter",
                    f"{n} of last 4 quarters up >10% vs a year ago ("
                    + ", ".join("n/a" if pd.isna(x) else f"{x:+.0%}" for x in yoy) + ")"))

    g = f.get("growth", {})
    s_ttm = g.get("Compounded Sales Growth", {}).get("TTM")
    p_ttm = g.get("Compounded Profit Growth", {}).get("TTM")
    if pd.notna(s_ttm) and pd.notna(p_ttm):
        out.append(("pass" if p_ttm > s_ttm and p_ttm > 0 else "watch" if p_ttm > 0 else "fail", "Profit growing faster than sales",
                    f"last 12 months: profit {p_ttm:+.0f}% vs sales {s_ttm:+.0f}% — {'operating leverage' if p_ttm > s_ttm else 'margins not expanding'}"))

    opm = _row(q, "OPM %")
    if len(opm) >= 8:
        now_, before = opm.iloc[-4:].mean(), opm.iloc[-8:-4].mean()
        out.append(("pass" if now_ > before + .5 else "watch" if now_ >= before - 1 else "fail", "Margins expanding",
                    f"operating margin {before:.1f}% → {now_:.1f}% (avg of last 4 quarters vs the 4 before)"))

    oi, pbt = _row(q, "Other Income"), _row(q, "Profit before tax")
    if len(oi) >= 4 and len(pbt) >= 4 and pbt.iloc[-4:].sum() > 0:
        share = oi.iloc[-4:].sum() / pbt.iloc[-4:].sum()
        out.append(("pass" if share < .2 else "watch" if share < .4 else "fail", "Profit from the core business",
                    f"other income is {share:.0%} of pre-tax profit (last 4 quarters) — high means profit isn't from operations"))

    cfo, np_ = _annual(_row(cf, "Cash from Operating Activity")), _annual(_row(a, "Net Profit"))
    yrs = sorted(set(cfo.index) & set(np_.index), key=lambda c: pd.to_datetime(c, format="%b %Y"))[-3:]
    if yrs and np_[yrs].sum() > 0:
        ratio = cfo[yrs].sum() / np_[yrs].sum()
        out.append(("pass" if ratio >= .8 else "watch" if ratio >= .5 else "fail", "Cash backs the profit",
                    f"₹{cfo[yrs].sum():,.0f} Cr operating cash vs ₹{np_[yrs].sum():,.0f} Cr profit over {len(yrs)} years ({ratio:.0%})"))

    roce = _row(rat, "ROCE %")
    if len(roce):
        r0, r_prev = roce.iloc[-1], roce.iloc[-4] if len(roce) >= 4 else roce.iloc[0]
        out.append(("pass" if r0 >= 15 and r0 >= r_prev else "watch" if r0 >= 12 else "fail", "Good return on capital",
                    f"ROCE {r0:.0f}% (was {r_prev:.0f}%) — above 15% means growth creates value"))

    debt, eq = _row(bal, "Borrowings"), _row(bal, "Equity Capital").add(_row(bal, "Reserves"), fill_value=0)
    if len(debt) and len(eq) and eq.iloc[-1] > 0:
        de = debt.iloc[-1] / eq.iloc[-1]
        trend = f", was {debt.iloc[-3] / eq.iloc[-3]:.2f}" if len(debt) >= 3 and len(eq) >= 3 and eq.iloc[-3] > 0 else ""
        out.append(("pass" if de < .5 else "watch" if de < 1 else "fail", "Debt under control",
                    f"debt/equity {de:.2f}{trend} (₹{debt.iloc[-1]:,.0f} Cr borrowings)"))

    fa, cw = _row(bal, "Fixed Assets"), _row(bal, "CWIP")
    if len(fa) >= 3:
        cap = fa.add(cw, fill_value=0)
        grow = cap.iloc[-1] / cap.iloc[-3] - 1 if cap.iloc[-3] else float("nan")
        pipe = cw.iloc[-1] / fa.iloc[-1] if len(cw) and fa.iloc[-1] else 0
        out.append(("pass" if grow > .3 or pipe > .25 else "watch" if grow > .1 else "n/a", "Building capacity",
                    f"plant & machinery + work-in-progress {grow:+.0%} over 2 periods; "
                    f"₹{cw.iloc[-1] if len(cw) else 0:,.0f} Cr still under construction"))

    promo = _row(sh, "Promoters")
    if len(promo) >= 2:
        chg = promo.iloc[-1] - promo.iloc[max(0, len(promo) - 5)]
        out.append(("pass" if chg >= -.5 else "watch" if chg >= -3 else "fail", "Promoters holding on",
                    f"promoter stake {promo.iloc[-1]:.1f}% ({chg:+.1f} pts over the last year)"))

    dd = _annual(_row(rat, "Debtor Days"))
    if len(dd) >= 3:
        out.append(("pass" if dd.iloc[-1] <= dd.iloc[-3] + 10 else "watch" if dd.iloc[-1] <= dd.iloc[-3] + 30 else "fail",
                    "Customers paying on time", f"debtor days {dd.iloc[-3]:.0f} → {dd.iloc[-1]:.0f} — rising fast can mean sales on credit"))
    return out


if __name__ == "__main__":
    f = get(sys.argv[1], refresh="--refresh" in sys.argv)
    if not f:
        sys.exit("no Screener page")
    print(f["name"], f["basis"], f["fetched"], list(f["tables"]))
    print(json.dumps(f["growth"]))
    for v, t, d in checklist(f):
        print(f"{v:6} {t:32} {d}")
