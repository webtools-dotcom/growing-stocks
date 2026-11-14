"""Pull latest quarterly results, capex signals (CWIP, fixed assets) and industry from Screener.in.

Results are cached in data/ and loaded into the market DB by data_sync.load_screener:
  data/screener_q.csv     long form: symbol, quarter_end, sales_cr, op_profit_cr, opm_pct, net_profit_cr
  data/screener_meta.csv  one row per symbol: industry, capex, P/E, ROCE, fetched date

Usage: python screener_refresh.py SYM1,SYM2,...
"""
import io
import re
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from safeio import save_csv
from bs4 import BeautifulSoup

DATA = Path(__file__).resolve().parent / "data"
Q_CACHE, META_CACHE = DATA / "screener_q.csv", DATA / "screener_meta.csv"
H = {"User-Agent": "Mozilla/5.0"}
ALIASES = {"Revenue": "Sales", "Financing Profit": "Operating Profit", "Financing Margin %": "OPM %"}


def table(soup, sec_id):
    sec = soup.find("section", id=sec_id)
    if not sec or not sec.find("table"):
        return None
    t = pd.read_html(io.StringIO(str(sec.find("table"))))[0]
    t = t.set_index(t.columns[0])
    # "Sales\xa0+" -> "Sales"; "CFO/EBITDA  Share of operating profit..." (tooltip text) -> "CFO/EBITDA"
    t.index = [re.split(r"\s{2,}", str(s).replace("\xa0", " "))[0].rstrip(" +").strip() for s in t.index]
    t = t.rename(index=ALIASES)
    return t.apply(lambda c: pd.to_numeric(c.astype(str).str.replace(",", "").str.replace("%", ""), errors="coerce"))


def ratio(soup, name):
    for li in soup.select("#top-ratios li"):
        if li.find("span", class_="name").get_text(strip=True) == name:
            v = li.find("span", class_="number")
            return float(v.get_text(strip=True).replace(",", "")) if v and v.get_text(strip=True) else None
    return None


def fetch(sym):
    """-> (meta dict, quarters DataFrame) or (None, None) if Screener has no usable page."""
    for path in ("consolidated/", ""):
        r = requests.get(f"https://www.screener.in/company/{sym}/{path}", headers=H, timeout=20)
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        q = table(soup, "quarters")
        if q is None or "Sales" not in q.index or q.loc["Sales"].fillna(0).eq(0).all():
            continue
        qe = pd.to_datetime(list(q.columns), format="%b %Y", errors="coerce") + pd.offsets.MonthEnd(0)
        quarters = pd.DataFrame({
            "symbol": sym, "quarter_end": qe,
            "sales_cr": q.loc["Sales"].values,
            "op_profit_cr": q.loc["Operating Profit"].values if "Operating Profit" in q.index else None,
            "opm_pct": q.loc["OPM %"].values if "OPM %" in q.index else None,
            "net_profit_cr": q.loc["Net Profit"].values,
        }).dropna(subset=["quarter_end", "sales_cr"])
        bs = table(soup, "balance-sheet")
        fa = list(bs.loc["Fixed Assets"].iloc[-3:]) if bs is not None and "Fixed Assets" in bs.index else []
        cw = list(bs.loc["CWIP"].iloc[-3:]) if bs is not None and "CWIP" in bs.index else []
        # capex signal: fixed assets up >30% over the shown years, or big capital-work-in-progress pipeline
        capex = bool((len(fa) >= 2 and fa[0] and fa[-1] / fa[0] > 1.3) or (cw and fa and fa[-1] and cw[-1] / fa[-1] > .25))
        sector = soup.find("a", href=re.compile(r"^/market/IN\d+/$"))
        industry = soup.find("a", title="Industry")
        meta = dict(symbol=sym, sector=sector.get_text(strip=True) if sector else None,
                    industry=industry.get_text(strip=True) if industry else None,
                    pe=ratio(soup, "Stock P/E"), roce=ratio(soup, "ROCE"), mcap=ratio(soup, "Market Cap"),
                    fixed_assets=fa, cwip=cw, capex=capex, fetched=date.today().isoformat())
        return meta, quarters
    return None, None


def _save(metas, qs):
    """Merge fetched companies into the caches (replacing their old rows); crash-safe file swap."""
    done = {m["symbol"] for m in metas}
    old_m = pd.read_csv(META_CACHE) if META_CACHE.exists() else pd.DataFrame()
    old_q = pd.read_csv(Q_CACHE, parse_dates=["quarter_end"]) if Q_CACHE.exists() else pd.DataFrame()
    if len(old_m):
        old_m = old_m[~old_m.symbol.isin(done)]
        old_q = old_q[~old_q.symbol.isin(done)]
    pd.concat([old_m, pd.DataFrame(metas)]).pipe(save_csv, META_CACHE, index=False)
    pd.concat([old_q, *qs]).pipe(save_csv, Q_CACHE, index=False)


def refresh(syms, pause=1.2, every=25, log=print):
    """Fetch each symbol and save progress every `every` companies, so a crash, sleep or closed laptop mid-run loses
    at most that many: the next update skips companies already fetched since their latest results."""
    DATA.mkdir(exist_ok=True)
    metas, qs, saved = [], [], 0
    for i, s in enumerate(syms, 1):
        try:
            m, q = fetch(s)
        except Exception as e:  # one bad page shouldn't kill the batch
            log(f"{s}: {e}")
            m, q = None, None
        if m:
            metas.append(m)
            qs.append(q)
        if i % every == 0 or i == len(syms):
            if metas:
                _save(metas, qs)
                saved += len(metas)
                metas, qs = [], []
            log(f"screener {i}/{len(syms)} · saved")
        time.sleep(pause)
    return saved


if __name__ == "__main__":
    print(refresh(sys.argv[1].split(",")), "symbols cached")
