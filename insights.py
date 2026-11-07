"""Signals behind the visual panels — all local or free public data, no AI.

Early growth:  capacity_bridge (plants under construction -> potential sales), order_book (years of revenue locked
               in, from filings), margin_band (margin vs its own 3-year range), smart money (insider trades,
               ownership changes)
Traps:         trap_scores — a 7-axis safety profile (exchange surveillance, cash quality, receivables, dilution,
               insider selling, core profit, liquidity/operator risk). 100 = safe.
Valuation:     pe_history — P/E through time vs its own median, and PEG.
"""
import re
from datetime import date

import pandas as pd

from config import DATA, nse_session
from safeio import read_json, save_json

CACHE = DATA / "insights"


def _cached(name, days, fetch):
    """Tiny JSON cache: data/insights/<name>.json, refreshed after `days`."""
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{name}.json"
    if p.exists() and (date.today() - date.fromtimestamp(p.stat().st_mtime)).days < days:
        d = read_json(p)
        if d is not None:
            return d
    data = fetch()
    save_json(p, data, default=str)
    return data


def _row(T, table, name):
    df = T.get(table)
    return df.loc[name].dropna() if df is not None and name in df.index else pd.Series(dtype=float)


def _annual(s):
    return s[[c for c in s.index if str(c).startswith("Mar")]]


# ------------------------------------------------------------------ public data
def surveillance():
    """{symbol: description} for stocks on NSE's ASM / GSM surveillance lists (refreshed daily)."""
    def fetch():
        s, out = nse_session(), {}
        asm = s.get("https://www.nseindia.com/api/reportASM", timeout=30).json()
        for part in asm.values() if isinstance(asm, dict) else []:
            for x in (part.get("data", []) if isinstance(part, dict) else []):
                out[x["symbol"]] = x.get("survDesc") or x.get("asmSurvIndicator")
        for x in s.get("https://www.nseindia.com/api/reportGSM", timeout=30).json():
            out[x["symbol"]] = x.get("survDesc") or f"GSM {x.get('gsmStage')}"
        return out
    return _cached("surveillance", 1, fetch)


def insider_trades(symbol):
    """Promoter / director / employee trades (SEBI insider-trading disclosures), refreshed daily."""
    def fetch():
        j = nse_session().get(f"https://www.nseindia.com/api/corporates-pit?index=equities&symbol={symbol}", timeout=30).json()
        rows = []
        for x in j.get("data", []):
            side = x.get("tdpTransactionType")
            if side not in ("Buy", "Sell") or x.get("secType") != "Equity Shares":
                continue
            rows.append(dict(date=str(pd.to_datetime(x.get("acqfromDt"), format="%d-%b-%Y", errors="coerce").date()),
                             who=x.get("personCategory"), name=x.get("acqName"), side=side,
                             qty=float(x.get("secAcq") or 0), value_cr=float(x.get("secVal") or 0) / 1e7))
        return rows
    df = pd.DataFrame(_cached(f"pit_{symbol}", 1, fetch))
    if len(df):
        df["date"] = pd.to_datetime(df.date, errors="coerce")
        df = df.dropna(subset=["date"])
    return df


def order_book(symbol):
    """Order book (Rs crore) and order wins mentioned in the company's filings, refreshed weekly."""
    def fetch():
        import brief
        s = nse_session()
        ann = brief.announcements(s, symbol)
        found = None
        for row in brief.pick_filings(ann, days=200).itertuples():
            if row.desc not in ("Investor Presentation", "Analysts/Institutional Investor Meet/Con. Call Updates", "Press Release"):
                continue
            try:
                text = brief.pdf_text(s, row.attchmntFile).lower()
            except Exception:
                continue
            m = re.search(r"order ?book[^.]{0,80}?(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|billion|bn|million|mn)", text)
            if m:
                v = float(m.group(1).replace(",", ""))
                v *= {"billion": 100, "bn": 100, "million": .1, "mn": .1}.get(m.group(2), 1)
                found = dict(value_cr=round(v), date=f"{row.dt:%d %b %Y}", source=row.desc, url=row.attchmntFile)
                break
        wins = []
        if len(ann):
            o = ann[ann.desc.fillna("").str.contains("Bagging|order", case=False)]
            for r in o.head(20).itertuples():
                m = re.search(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)\s*(crore|cr|lakh)", str(r.attchmntText).lower())
                if m:
                    v = float(m.group(1).replace(",", "")) * (.01 if m.group(2) == "lakh" else 1)
                    wins.append(dict(date=f"{r.dt:%Y-%m-%d}", value_cr=v, text=str(r.attchmntText)[:140], url=r.attchmntFile))
        return dict(book=found, wins=wins)
    return _cached(f"orders_{symbol}", 7, fetch)


# ------------------------------------------------------------------ computed from fundamentals (no network)
def capacity_bridge(f):
    """Plants under construction (CWIP) x how much sales each rupee of plant produces today = potential sales."""
    T = f["tables"]
    fa, cw = _row(T, "balance", "Fixed Assets"), _row(T, "balance", "CWIP")
    sales = _row(T, "annual", "Sales")
    if not len(fa) or not len(sales) or fa.iloc[-1] <= 0:
        return None
    ttm = sales.get("TTM", sales.iloc[-1])
    turnover = min(ttm / fa.iloc[-1], 6)  # cap: asset-light businesses would explode the estimate
    cwip = cw.iloc[-1] if len(cw) else 0
    return dict(ttm_sales=ttm, fixed_assets=fa.iloc[-1], cwip=cwip, turnover=turnover,
                potential_add=cwip * turnover, potential_pct=cwip * turnover / ttm if ttm else None)


def margin_band(f):
    """Quarterly operating margin with the min-max of the previous 12 quarters — a breakout above the band
    is the classic sign profits are about to grow faster than sales."""
    opm = _row(f["tables"], "quarters", "OPM %")
    if len(opm) < 6:
        return None
    df = pd.DataFrame({"opm": opm})
    df["lo"] = df.opm.shift(1).rolling(12, min_periods=4).min()
    df["hi"] = df.opm.shift(1).rolling(12, min_periods=4).max()
    last = df.iloc[-1]
    state = "breakout" if last.opm > last.hi else "breakdown" if last.opm < last.lo else "inside range"
    return df, state


def ownership_changes(f):
    sh = f["tables"].get("shareholding")
    if sh is None or len(sh.columns) < 2:
        return None
    rows = [k for k in ("Promoters", "FIIs", "DIIs", "Public") if k in sh.index]
    return sh.loc[rows].diff(axis=1).iloc[:, 1:]


def shares_cr(f):
    """Shares outstanding (crore) from annual EPS: profit / EPS."""
    np_, eps = _annual(_row(f["tables"], "annual", "Net Profit")), _annual(_row(f["tables"], "annual", "EPS in Rs"))
    s = (np_ / eps.replace(0, float("nan"))).dropna()
    return s[s > 0]


def pe_history(f, px):
    """Daily P/E using split-adjusted price and trailing-4-quarter profit (known ~45 days after quarter end)."""
    q = _row(f["tables"], "quarters", "Net Profit")
    sh = shares_cr(f)
    if len(q) < 5 or not len(sh) or px is None or px.empty:
        return None
    ttm = q.rolling(4).sum().dropna()
    ttm.index = pd.to_datetime(ttm.index, format="%b %Y") + pd.offsets.MonthEnd(0) + pd.Timedelta(days=45)
    eps = (ttm / sh.iloc[-1]).sort_index()
    s = px.set_index("trade_date").close
    e = eps.reindex(s.index.union(eps.index)).ffill().reindex(s.index)
    pe = (s / e).where(e > 0).dropna()
    return pe


def compare_profile(f):
    """Growth/quality profile on a common 0..100 scale (for the compare radar) + the raw numbers behind it."""
    T, g = f["tables"], f.get("growth", {})
    clip = lambda v, hi: max(0.0, min(100.0, (v or 0) / hi * 100)) if pd.notna(v) else 0.0
    sg = g.get("Compounded Sales Growth", {}).get("TTM")
    pg = g.get("Compounded Profit Growth", {}).get("TTM")
    opm = _row(T, "quarters", "OPM %")
    roce = _row(T, "ratios", "ROCE %")
    cfo, np_ = _annual(_row(T, "cashflow", "Cash from Operating Activity")), _annual(_row(T, "annual", "Net Profit"))
    yrs = [c for c in cfo.index if c in np_.index][-3:]
    cash = cfo[yrs].sum() / np_[yrs].sum() if yrs and np_[yrs].sum() > 0 else None
    debt = _row(T, "balance", "Borrowings")
    eq = _row(T, "balance", "Equity Capital").add(_row(T, "balance", "Reserves"), fill_value=0)
    de = debt.iloc[-1] / eq.iloc[-1] if len(debt) and len(eq) and eq.iloc[-1] > 0 else None
    cap = capacity_bridge(f)
    raw = {"sales growth": sg, "profit growth": pg, "margin": opm.iloc[-4:].mean() if len(opm) else None,
           "return on capital": roce.iloc[-1] if len(roce) else None, "cash backs profit": cash, "debt/equity": de,
           "capacity coming": cap["potential_pct"] if cap else None}
    score = {"sales growth": clip(sg, 60), "profit growth": clip(pg, 80), "margin": clip(raw["margin"], 30),
             "return on capital": clip(raw["return on capital"], 35), "cash backs profit": clip(cash, 1.2),
             "low debt": 100 - clip(de, 1.0) if de is not None else 50, "capacity coming": clip(raw["capacity coming"], .6)}
    return score, raw


def trap_scores(f, symbol, px=None):
    """-> list of (axis, score 0..100 where 100 = safe, detail). Missing data scores 60 (unknown, not safe)."""
    T, out = f["tables"], []

    surv = surveillance().get(symbol)
    out.append(("exchange watch", 0 if surv else 100, surv or "not on NSE's ASM/GSM surveillance lists"))

    cfo, np_ = _annual(_row(T, "cashflow", "Cash from Operating Activity")), _annual(_row(T, "annual", "Net Profit"))
    yrs = [c for c in cfo.index if c in np_.index][-3:]
    if yrs and np_[yrs].sum() > 0:
        r = cfo[yrs].sum() / np_[yrs].sum()
        out.append(("cash backs profit", int(min(100, max(5, r * 100))), f"operating cash = {r:.0%} of profit over {len(yrs)}y"))
    else:
        out.append(("cash backs profit", 60, "not enough cash-flow history"))

    dd = _annual(_row(T, "ratios", "Debtor Days"))
    if len(dd) >= 3:
        ch = dd.iloc[-1] - dd.iloc[-3]
        out.append(("customers pay", int(max(5, min(100, 100 - ch * 2))), f"debtor days {dd.iloc[-3]:.0f} → {dd.iloc[-1]:.0f}"))
    else:
        out.append(("customers pay", 60, "not enough history"))

    # money raised from new shareholders = growth in net worth minus profit earned meanwhile.
    # Immune to splits and bonuses (they only move money between capital and reserves).
    # ponytail: dividends paid make this undercount slightly; fine for a red-flag check
    sh = shares_cr(f)
    nw = _row(T, "balance", "Equity Capital").add(_row(T, "balance", "Reserves"), fill_value=0)
    qp = _row(T, "quarters", "Net Profit")
    mcap = sh.iloc[-1] * px.close.iloc[-1] if len(sh) and px is not None and len(px) else None
    when = lambda c: pd.to_datetime(c, format="%b %Y") + pd.offsets.MonthEnd(0)
    if len(nw):
        nw = nw[[c for c in nw.index if when(c) >= when(nw.index[-1]) - pd.DateOffset(years=3)]]  # last 3 years
    if len(nw) >= 2 and len(qp) and mcap:
        start, end = when(nw.index[0]), when(nw.index[-1])
        earned = sum(v for c, v in qp.items() if start < when(c) <= end)
        raised = nw.iloc[-1] - nw.iloc[0] - earned
        d = max(0.0, raised) / mcap
        out.append(("no dilution", int(max(5, min(100, 100 - d * 500))),
                    f"≈₹{max(0, raised):,.0f} Cr raised from new shares since {nw.index[0]} (~{d:.0%} of company value)"))
    else:
        out.append(("no dilution", 60, "not enough history"))

    pit = insider_trades(symbol)
    if len(pit):
        recent = pit[(pit.date >= pd.Timestamp.now() - pd.Timedelta(days=365)) & pit.who.str.contains("Promoter", na=False)]
        net = recent[recent.side == "Buy"].value_cr.sum() - recent[recent.side == "Sell"].value_cr.sum()
        mcap = (sh.iloc[-1] * px.close.iloc[-1]) if len(sh) and px is not None and len(px) else None
        share = net / mcap if mcap else 0
        out.append(("promoters not selling", int(max(5, min(100, 100 + share * 2000))),
                    "no promoter trades in 12 months" if recent.empty else
                    f"promoters net {'bought' if net >= 0 else 'sold'} ₹{abs(net):,.1f} Cr in 12 months"))
    else:
        out.append(("promoters not selling", 85, "no promoter trades disclosed"))

    oi, pbt = _row(T, "quarters", "Other Income"), _row(T, "quarters", "Profit before tax")
    if len(oi) >= 4 and len(pbt) >= 4 and pbt.iloc[-4:].sum() > 0:
        r = oi.iloc[-4:].sum() / pbt.iloc[-4:].sum()
        out.append(("profit from core", int(max(5, min(100, 100 - r * 200))), f"other income = {r:.0%} of pre-tax profit"))
    else:
        out.append(("profit from core", 60, "not enough data"))

    if px is not None and len(px) > 60:
        recent = px.tail(60)
        turn = (recent.close * recent.volume / 1e7).median()
        ret = recent.close.pct_change().abs()
        locked = int(((recent.high == recent.low) & (ret > .04)).sum()) if "high" in recent else 0
        score = 100 if turn >= 5 else 75 if turn >= 1 else 45 if turn >= .3 else 15
        out.append(("easy to trade", max(5, score - locked * 8),
                    f"median ₹{turn:,.1f} Cr traded/day" + (f", {locked} circuit-locked days" if locked else "")))
    return out


# ------------------------------------------------------------------ company snapshot verdicts — rule-based, free, no AI
VERDICTS = ["Best", "Good", "Average", "Bad", "Worse", "Critical"]

# industries whose costs or demand move with crude oil (raw material, fuel or freight), and war beneficiaries
OIL_HIGH = r"oil|petro|refin|gas|chemical|paint|plastic|polymer|tyre|tire|rubber|aviation|airline|shipping|logistic|" \
           r"fertili|lubricant|packaging|synthetic|textile|pvc|asphalt|bitumen|dye|pigment|agrochem|pesticide|carbon black"
OIL_MED = r"cement|steel|iron|metal|alumin|power|auto|fmcg|consumer|ceramic|glass|paper|transport|construction|pharma|drug"
OIL_WORDS = r"crude|naphtha|petroleum|polymer|resin|pvc|lng|natural gas|freight|bitumen|furnace oil|diesel"
WAR_PLUS = r"defen[cs]e|aerospace|ammunition|missile|drone|shipbuild|security"
EXPORT_WORDS = r"export|overseas|international|global markets?|europe|usa|united states|middle east|gulf|russia|ukraine"


def _verdict(score):
    """0..100 (100 = best) -> verdict on the Best..Critical scale."""
    return VERDICTS[0 if score >= 85 else 1 if score >= 70 else 2 if score >= 55 else 3 if score >= 40 else 4 if score >= 25 else 5]


def panic_sensitivity(px, n_days=12):
    """How the stock moved on the market's worst days since 2023 (election day, small-cap crash, tariff crash…).
    -> dict(ratio = stock fall / market fall, stock_avg, market_avg, days) or None."""
    p = DATA / "market_days.csv"
    if px is None or px.empty or not p.exists():
        return None
    m = pd.read_csv(p, index_col=0, parse_dates=True).market
    r = px.set_index("trade_date").close.pct_change()
    both = pd.concat([m, r.rename("stock")], axis=1).dropna()
    if len(both) < 120:
        return None
    worst = both.nsmallest(n_days, "market")
    return dict(ratio=worst.stock.mean() / worst.market.mean(), stock_avg=worst.stock.mean(), market_avg=worst.market.mean(),
                days=len(worst), worst_day=worst.stock.min())


def free_snapshot(f, symbol, px, universe, industry, sub_industry, traps):
    """Verdicts for the company snapshot from public data + our own scan. Same keys the card expects:
    business / sector_outlook / war_risk / checks / overall -> [verdict, reason], plus oil_link, sector, sources."""
    import fundamentals
    text = " ".join(filter(None, [f.get("about", ""), f.get("key_points", "")])).lower()
    ind = " ".join(filter(None, [industry or "", sub_industry or ""])).lower()
    out = dict(sector=" / ".join(filter(None, [industry, sub_industry])) or "unclassified")

    # business: the 10-point growth check
    checks = fundamentals.checklist(f)
    rated = [v for v, *_ in checks if v != "n/a"]
    if rated:
        score = (sum(v == "pass" for v in rated) + .5 * sum(v == "watch" for v in rated)) / len(rated) * 100
        fails = [t.lower() for v, t, _ in checks if v == "fail"]
        out["business"] = [_verdict(score), f"{sum(v == 'pass' for v in rated)} of {len(rated)} growth checks pass"
                           + (f"; weak: {', '.join(fails[:2])}" if fails else "")]

    # sector outlook: how the company's industry peers are doing in our own scan
    if industry and universe is not None and len(universe):
        im = universe.assign(industry=universe.symbol.map(industries_map()))
        peers = im[im.industry == industry]
        if len(peers) >= 5:
            g = peers.sales_yoy.median()
            m3 = peers.ret3m.median()
            rising = peers.status.isin(["LEADER", "EMERGING"]).mean()
            score = 50 + min(25, max(-25, g * 100)) + min(15, max(-15, m3 * 60)) + min(10, rising * 50)
            out["sector_outlook"] = [_verdict(score), f"{len(peers)} peers: median sales {g:+.0%}, 3-month price {m3:+.0%}, "
                                     f"{rising:.0%} on the buy-list"]

    # war & oil risk: industry sensitivity + the company's own words + measured panic behaviour
    oil = "high" if re.search(OIL_HIGH, ind) else "medium" if re.search(OIL_MED, ind) else "low"
    hits = sorted(set(re.findall(OIL_WORDS, text)))
    if hits and oil == "low":
        oil = "medium"
    exports = bool(re.search(EXPORT_WORDS, text))
    defence = bool(re.search(WAR_PLUS, ind + " " + text))
    panic = panic_sensitivity(px)
    score = {"low": 85, "medium": 65, "high": 40}[oil] - (10 if exports else 0) + (10 if defence else 0)
    why = [f"oil link {oil}" + (f" ({', '.join(hits[:3])} in its business)" if hits else " (by industry)")]
    if exports:
        why.append("export / global exposure")
    if defence:
        why.append("defence-linked: conflicts can raise demand")
    if panic:
        score -= max(0, (panic["ratio"] - 1)) * 25
        why.append(f"fell {panic['ratio']:.1f}× the market on its {panic['days']} worst days")
    out["war_risk"] = [_verdict(max(0, min(100, score))), "; ".join(why)]
    out["oil_link"], out["panic"] = oil, panic

    # before buying: the safety radar
    if traps:
        s = [v for _, v, _ in traps]
        weak = [a for a, v, _ in traps if v < 40]
        out["checks"] = [_verdict(.6 * sum(s) / len(s) + .4 * min(s)), f"safety radar avg {sum(s) / len(s):.0f}/100"
                         + (f"; weak: {', '.join(weak)}" if weak else "; no weak spots")]

    # overall: average of the four, the worst one named
    parts = [k for k in ("business", "sector_outlook", "war_risk", "checks") if k in out]
    if parts:
        idx = [VERDICTS.index(out[k][0]) for k in parts]
        worst = max(parts, key=lambda k: VERDICTS.index(out[k][0]))
        out["overall"] = [VERDICTS[round(sum(idx) / len(idx))],
                          f"average of {len(parts)} verdicts; weakest: {worst.replace('_', ' ')} ({out[worst][0].lower()})"]
    return out


def industries_map():
    import rising
    m = rising.industries()
    return m.industry.to_dict() if "industry" in m else {}
