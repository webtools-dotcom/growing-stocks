"""Your trades as a ledger: import a broker tradebook or add buys/sells by hand; positions, round trips, costs and
behaviour come from it, so a sold stock leaves your positions on its own. Everything stays in data/ on your machine.

The analytics answer what a broker statement doesn't: did your picks follow the system, did you respect your stop,
what happened after you sold, how long you hold winners vs losers, and how much charges eat out of small trades."""
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from config import DATA, open_db
from safeio import read_json, save_csv, save_json, save_text

LEDGER = DATA / "trades.csv"
NOTES = DATA / "notes.csv"              # symbol -> why you bought (thesis)
IGNORE = DATA / "portfolio_ignore.txt"  # symbols kept out of the portfolio (e.g. an IPO-quota buy)
ENTRY_CACHE = DATA / "entry_context.json"
CORP = DATA / "corp_actions.json"       # bonuses and splits per symbol, from NSE (refreshed daily for held stocks)
COLS = ["date", "time", "symbol", "side", "qty", "price", "exchange", "trade_id", "source"]
DP_CHARGE = 15.34  # depository charge per scrip per sell day (Zerodha: ₹13 + GST); edit if your broker differs


# ------------------------------------------------------------------ ledger
def ignored():
    return set(Path(IGNORE).read_text().split()) if Path(IGNORE).exists() else set()


def set_ignored(symbols):
    save_text(IGNORE, "\n".join(sorted({s.strip().upper() for s in symbols if s.strip()})))


def load(include_ignored=False, actions=True):
    """The ledger. actions=True also inserts bonuses and splits (side "adjust", qty = factor) at their ex-date, so
    positions come out in today's share count and cost; the saved ledger only ever holds real trades."""
    d = pd.read_csv(LEDGER, dtype={"trade_id": str, "time": str}) if Path(LEDGER).exists() else pd.DataFrame(columns=COLS)
    d = d.reindex(columns=COLS)
    d["date"] = pd.to_datetime(d.date)
    d["time"] = d.time.fillna("").astype(str)
    d["qty"], d["price"] = pd.to_numeric(d.qty), pd.to_numeric(d.price)
    if not include_ignored:
        d = d[~d.symbol.isin(ignored())]
    if actions and len(d):
        d = with_actions(d)
    return d.sort_values(["date", "time"], kind="stable").reset_index(drop=True)


def _factor(subject):
    """NSE corporate-action text -> (share multiplier, kind) for bonuses and splits, else (None, None).
    'Bonus 3:2' = 3 new for every 2 held -> x2.5; 'Split ... From Rs 10/- ... To Re 1/-' -> x10."""
    s = str(subject).lower()
    m = re.search(r"bonus\s*(\d+)\s*:\s*(\d+)", s)
    if m and int(m.group(2)):
        return (int(m.group(1)) + int(m.group(2))) / int(m.group(2)), "bonus"
    m = re.search(r"from\s*r[se]\.?\s*([\d.]+)\D+?to\s*r[se]\.?\s*([\d.]+)", s)
    if ("split" in s or "sub-division" in s) and m and float(m.group(2)):
        return float(m.group(1)) / float(m.group(2)), "split"
    return None, None


def refresh_actions(symbols, max_age_days=1):
    """Fetch bonuses/splits from NSE for `symbols` (cached in data/corp_actions.json; re-fetched after a day)."""
    cache, today, s = read_json(CORP, {}), pd.Timestamp.now().date(), None
    for sym in sorted(set(symbols)):
        e = cache.get(sym)
        if e and (today - pd.Timestamp(e["fetched"]).date()).days < max_age_days:
            continue
        try:
            if s is None:
                from config import nse_session
                s = nse_session()
            items = s.get(f"https://www.nseindia.com/api/corporates-corporateActions?index=equities&symbol={sym}", timeout=20).json()
        except Exception:
            continue  # offline / blocked: keep what we had
        acts = []
        for x in items if isinstance(items, list) else []:
            f, kind = _factor(x.get("subject", ""))
            ex = pd.to_datetime(x.get("exDate"), format="%d-%b-%Y", errors="coerce")
            if f and pd.notna(ex) and f != 1:
                acts.append(dict(ex=str(ex.date()), factor=f, kind=kind, subject=x.get("subject")))
        cache[sym] = dict(fetched=str(today), actions=acts)
    save_json(CORP, cache)
    return cache


def with_actions(d):
    """Insert cached bonuses/splits that fall after a symbol's first trade as 'adjust' rows (no network)."""
    cache = read_json(CORP, {})
    first = d.groupby("symbol").date.min()
    rows = [dict(date=pd.Timestamp(a["ex"]), time="00:00:00", symbol=s, side="adjust", qty=a["factor"], price=np.nan,
                 exchange="", trade_id=None, source=a["kind"])
            for s, t0 in first.items() for a in cache.get(s, {}).get("actions", []) if pd.Timestamp(a["ex"]) > t0]
    return pd.concat([d, pd.DataFrame(rows, columns=COLS)], ignore_index=True) if rows else d


def save(d):
    d = d[d.side.isin(["buy", "sell"])].reindex(columns=COLS).copy()  # bonuses/splits are derived, never stored
    d["date"] = pd.to_datetime(d.date).dt.date
    save_csv(d.sort_values(["date", "time"], kind="stable"), LEDGER, index=False)


def parse_tradebook(src):
    """Broker tradebook (Zerodha xlsx/csv layout, header row found automatically) or a plain csv with
    symbol,date,side,qty,price columns -> ledger rows."""
    name = getattr(src, "name", str(src)).lower()
    raw = pd.read_excel(src, header=None) if name.endswith((".xlsx", ".xls")) else pd.read_csv(src, header=None)
    hdr = next(i for i, r in raw.iterrows() if {"symbol"} <= {str(x).strip().lower() for x in r.values})
    t = raw.iloc[hdr + 1:].copy()
    t.columns = [str(c).strip().lower() for c in raw.iloc[hdr]]
    t = t.dropna(how="all")
    col = lambda *names: next((t[n] for n in names if n in t), pd.Series(None, index=t.index))
    ts = pd.to_datetime(col("order execution time"), errors="coerce")
    out = pd.DataFrame({
        "date": pd.to_datetime(col("trade date", "date")).dt.date,
        "time": ts.dt.strftime("%H:%M:%S").fillna(""),
        "symbol": col("symbol").astype(str).str.strip().str.upper(),
        "side": col("trade type", "side").astype(str).str.strip().str.lower(),
        "qty": pd.to_numeric(col("quantity", "qty")), "price": pd.to_numeric(col("price")),
        "exchange": col("exchange").fillna("NSE").astype(str), "trade_id": col("trade id", "trade_id").astype(str),
        "source": "tradebook"})
    out = out[out.side.isin(["buy", "sell"]) & out.qty.gt(0) & out.price.gt(0)]
    out.loc[out.trade_id.isin(["nan", "None", ""]), "trade_id"] = None
    return out


def merge(new):
    """Add tradebook rows to the ledger without duplicates (by broker trade id, else by every field).
    Returns how many rows were new."""
    old = load(include_ignored=True, actions=False)

    def key(d):  # built row by row: an empty ledger's columns carry no string type
        parts = [d.trade_id.fillna("").astype(str), d.symbol.astype(str), d.side.astype(str),
                 pd.to_datetime(d.date).dt.strftime("%Y-%m-%d").astype(str), d.qty.astype(float).astype(str),
                 d.price.astype(float).round(4).astype(str)]
        return pd.Series(["|".join(x) for x in zip(*parts)], index=d.index, dtype=object)

    new = new.assign(date=pd.to_datetime(new.date))
    fresh = new[~key(new).isin(set(key(old)))]
    save(pd.concat([old, fresh], ignore_index=True))
    return len(fresh)


def add_trade(side, symbol, qty, price, date):
    d = pd.DataFrame([dict(date=pd.Timestamp(date), time=pd.Timestamp.now().strftime("%H:%M:%S"), symbol=symbol.strip().upper(),
                           side=side, qty=float(qty), price=float(price), exchange="NSE", trade_id=None, source="manual")])
    save(pd.concat([load(include_ignored=True, actions=False), d], ignore_index=True))


def notes():
    n = pd.read_csv(NOTES) if Path(NOTES).exists() else pd.DataFrame(columns=["symbol", "thesis"])
    return dict(zip(n.symbol, n.thesis.fillna("").astype(str)))


def save_notes(d):
    save_csv(pd.DataFrame({"symbol": list(d), "thesis": list(d.values())}), NOTES, index=False)


def migrate(old_positions):
    """One-time move from the old positions.csv: its "why I bought" notes become notes, and if there is no ledger yet
    its rows become manual buys (import your broker tradebook afterwards for the full history)."""
    old_positions = Path(old_positions)
    if not old_positions.exists():
        return
    o = pd.read_csv(old_positions)
    if not Path(NOTES).exists() and "thesis" in o:
        save_notes({s: t for s, t in zip(o.symbol, o.thesis.fillna("").astype(str)) if t.strip()})
    if not Path(LEDGER).exists() and len(o):
        save(pd.DataFrame(dict(date=pd.to_datetime(o.buy_date), time="", symbol=o.symbol, side="buy", qty=o.qty,
                               price=o.buy_price, exchange="NSE", trade_id=None, source="positions.csv")))


# ------------------------------------------------------------------ positions and round trips (FIFO)
def fifo(d):
    """-> (open lots, closed pieces, oversold). Open lots: symbol, date, qty, price. Closed pieces: one row per buy lot
    (or part) matched to a sell: symbol, buy_date, sell_date, qty, buy, sell."""
    lots, closed, over = {}, [], []
    for r in d.itertuples():
        q = lots.setdefault(r.symbol, [])
        if r.side == "adjust":  # bonus/split: more shares, same total cost; a bonus only credits whole shares
            held = sum(x[1] for x in q)
            for x in q:
                x[1], x[2] = x[1] * r.qty, x[2] / r.qty
            if r.source == "bonus" and q:  # the fraction is paid out in cash: keep total cost over the whole shares
                cost = sum(x[1] * x[2] for x in q)
                q[-1][1] -= held * r.qty - math.floor(held * r.qty + 1e-9)
                rest = sum(x[1] * x[2] for x in q[:-1])
                q[-1][2] = (cost - rest) / q[-1][1] if q[-1][1] > 1e-9 else q[-1][2]
            continue
        if r.side == "buy":
            q.append([r.date, r.qty, r.price])
            continue
        left = r.qty
        while left > 1e-9 and q:
            take = min(left, q[0][1])
            closed.append(dict(symbol=r.symbol, buy_date=q[0][0], sell_date=r.date, qty=take, buy=q[0][2], sell=r.price))
            q[0][1] -= take
            left -= take
            if q[0][1] <= 1e-9:
                q.pop(0)
        if left > 1e-9:
            over.append(dict(symbol=r.symbol, date=r.date, qty=left))  # sold shares bought before the ledger starts
    open_ = pd.DataFrame([dict(symbol=s, date=a, qty=b, price=c) for s, q in lots.items() for a, b, c in q],
                         columns=["symbol", "date", "qty", "price"])
    return open_, pd.DataFrame(closed, columns=["symbol", "buy_date", "sell_date", "qty", "buy", "sell"]), pd.DataFrame(over)


def open_positions(d=None):
    """Same shape the app always used: symbol, buy_date (first open lot), buy_price (average cost), qty."""
    lots = fifo(load() if d is None else d)[0]
    if lots.empty:
        return pd.DataFrame(columns=["symbol", "buy_date", "buy_price", "qty"])
    g = lots.assign(v=lots.qty * lots.price).groupby("symbol")
    return pd.DataFrame({"buy_date": g.date.min().dt.date, "buy_price": (g.v.sum() / g.qty.sum()).round(2),
                         "qty": g.qty.sum()}).reset_index()


def round_trips(d=None):
    """One row per trade from flat to flat: first buy, last sell, average prices, P&L, days held. Tracks the cost of
    the shares actually held, so a bonus or split during the trade keeps the P&L right."""
    d = load() if d is None else d
    rows, state = [], {}
    for r in d.itertuples():
        s = state.setdefault(r.symbol, dict(pos=0.0, cost=0.0, bv=0.0, rc=0.0, sq=0.0, sv=0.0, entry=None, buys=set()))
        if r.side == "adjust":
            if s["entry"] is not None:
                s["pos"] = math.floor(s["pos"] * r.qty + 1e-9) if r.source == "bonus" else s["pos"] * r.qty
            continue
        if r.side == "buy":
            if s["pos"] <= 1e-9:
                s.update(pos=0.0, cost=0.0, bv=0.0, rc=0.0, sq=0.0, sv=0.0, entry=r.date, buys=set())
            s["pos"] += r.qty
            s["cost"] += r.qty * r.price
            s["bv"] += r.qty * r.price
            s["buys"].add(r.date)  # one order filled in pieces is one buy; another day is an add
            continue
        if s["entry"] is None:
            continue
        q = min(r.qty, s["pos"])
        avg = s["cost"] / s["pos"]
        s["rc"] += q * avg
        s["cost"] -= q * avg
        s["pos"] -= q
        s["sq"] += q
        s["sv"] += q * r.price
        if s["pos"] <= 1e-9:
            rows.append(dict(symbol=r.symbol, entry=s["entry"], exit=r.date, qty=s["sq"], avg_buy=s["rc"] / s["sq"],
                             avg_sell=s["sv"] / s["sq"], invested=s["bv"], pnl=s["sv"] - s["rc"], ret=s["sv"] / s["rc"] - 1,
                             days=(r.date - s["entry"]).days, adds=len(s["buys"]) - 1))
            s["entry"] = None
    return pd.DataFrame(rows, columns=["symbol", "entry", "exit", "qty", "avg_buy", "avg_sell", "invested", "pnl", "ret", "days", "adds"])


def costs(d=None, dp=DP_CHARGE):
    """Estimated charges for Indian equity delivery (intraday where bought and sold the same day): STT, exchange
    and SEBI fees, stamp duty, GST, plus a DP charge per scrip per sell day. Zero brokerage (discount broker)."""
    d = load() if d is None else d
    d = d[d.side.isin(["buy", "sell"])]
    if d.empty:
        return 0.0
    v = d.qty * d.price
    exch = v * np.where(d.exchange.astype(str).str.upper().eq("BSE"), .0000375, .0000297)
    sebi = v * .000001
    _, closed, _ = fifo(d)
    intraday_sell_value = (closed.qty * closed.sell)[closed.buy_date == closed.sell_date].sum() if len(closed) else 0
    stt = (v[d.side == "buy"].sum() + v[d.side == "sell"].sum() - intraday_sell_value) * .001 + intraday_sell_value * .00025
    stamp = v[d.side == "buy"].sum() * .00015
    gst = .18 * (exch.sum() + sebi.sum())
    delivery = closed[closed.buy_date < closed.sell_date] if len(closed) else closed
    dp_days = set(zip(delivery.symbol, delivery.sell_date))  # one DP charge per scrip per day delivery shares leave
    return float(stt + exch.sum() + sebi.sum() + stamp + gst + dp * len(dp_days))


# ------------------------------------------------------------------ what the system said when you bought
def entry_context(trips_and_open):
    """For each (symbol, entry date): the label the scan would have given that day, the 1-year run and distance from
    the 52-week high — point-in-time (results public 60 days after quarter end, prices up to that day). Cached."""
    cache = read_json(ENTRY_CACHE, {})
    need = {(s, pd.Timestamp(e).date()) for s, e in trips_and_open if f"{s}|{pd.Timestamp(e).date()}" not in cache}
    if need:
        import growth_screen as g
        from rising import classify
        isin = g.inst.drop_duplicates("symbol", keep="last").set_index("symbol")["isin"]
        for day in sorted({e for _, e in need}):
            on = pd.Timestamp(day)
            q = (on - pd.Timedelta(days=g.LAG_DAYS)) - pd.offsets.QuarterEnd(0)
            if q + pd.Timedelta(days=g.LAG_DAYS) > on:
                q -= pd.offsets.QuarterEnd(1)
            try:
                f = g.features(q, on)
                f = f.assign(label=classify(f))
            except Exception:
                f = pd.DataFrame()
            for s in [s for s, e in need if e == day]:
                i = isin.get(s)
                if i is not None and i in f.index:
                    r = f.loc[i]
                    cache[f"{s}|{day}"] = dict(label=r.label if isinstance(r.label, str) else "no label",
                                               ret1y=None if pd.isna(r.ret1y) else float(r.ret1y),
                                               off_high=None if pd.isna(r.off_high) else float(r.off_high))
                else:
                    cache[f"{s}|{day}"] = dict(label="not scanned", ret1y=None, off_high=None)
        save_json(ENTRY_CACHE, cache)
    return cache


def price_paths(symbols, since):
    """Split-adjusted daily closes per symbol from `since` (DataFrame: index date, one column per symbol)."""
    from data_sync import adjust_prices
    if not symbols:
        return pd.DataFrame()
    c = open_db(wait=10)
    try:
        px = c.execute(f"""select i.symbol, d.trade_date, d.close from daily_market d join instruments i using(isin)
                           where i.symbol in ({",".join("?" * len(symbols))}) and d.trade_date >= ?""",
                       [*symbols, pd.Timestamp(since).date()]).df()
    finally:
        c.close()
    if px.empty:
        return pd.DataFrame()
    p = px.pivot_table(index="trade_date", columns="symbol", values="close").sort_index()
    p.index = pd.to_datetime(p.index)
    return p.apply(adjust_prices)


def behaviour(trips, open_pos, prices, stop=.20):
    """Per closed trade: worst point while held (vs average buy), whether it crossed the stop, and the move since
    you sold. Per open position: worst point so far and distance to the stop."""
    t = trips.copy()
    worst, after = [], []
    for r in t.itertuples():
        p = prices.get(r.symbol)
        if p is None or p.dropna().empty:
            worst.append(np.nan)
            after.append(np.nan)
            continue
        held = p[(p.index >= r.entry) & (p.index <= r.exit)].dropna()
        scale = r.avg_sell / held.iloc[-1] if len(held) else np.nan  # adjusted -> your price units at exit
        worst.append(held.min() * scale / r.avg_buy - 1 if len(held) else np.nan)
        now = p.dropna().iloc[-1] * scale
        after.append(now / r.avg_sell - 1 if p.dropna().index[-1] > r.exit else np.nan)
    t["worst"], t["after"] = worst, after
    t["crossed_stop"] = t.worst <= -stop
    o = open_pos.copy()
    ow = []
    for r in o.itertuples():
        p = prices.get(r.symbol)
        held = p[p.index >= pd.Timestamp(r.buy_date)].dropna() if p is not None else pd.Series(dtype=float)
        scale = r.last / held.iloc[-1] if len(held) and pd.notna(getattr(r, "last", np.nan)) else np.nan
        ow.append(held.min() * scale / r.buy_price - 1 if len(held) else np.nan)
    o["worst"] = ow
    return t, o


if __name__ == "__main__":  # self-check on a synthetic ledger
    T = lambda d, tm, s, side, q, p: dict(date=pd.Timestamp(d), time=tm, symbol=s, side=side, qty=q, price=p,
                                          exchange="NSE", trade_id=None, source="t")
    led = pd.DataFrame([T("2026-01-01", "10:00", "A", "buy", 10, 100), T("2026-01-05", "10:00", "A", "buy", 10, 120),
                        T("2026-02-01", "10:00", "A", "sell", 15, 130), T("2026-02-02", "10:00", "B", "buy", 5, 50),
                        T("2026-02-02", "11:00", "B", "sell", 5, 52), T("2026-03-01", "10:00", "A", "sell", 5, 90)])
    op = open_positions(led)
    assert op.empty, op
    rt = round_trips(led)
    a = rt[rt.symbol == "A"].iloc[0]
    assert a.qty == 20 and abs(a.avg_buy - 110) < 1e-9 and abs(a.pnl - (15 * 130 + 5 * 90 - 20 * 110)) < 1e-6, a
    assert len(rt) == 2 and rt[rt.symbol == "B"].days.iloc[0] == 0
    lots, closed, over = fifo(led)
    assert closed[(closed.symbol == "A") & (closed.sell_date == pd.Timestamp("2026-02-01"))].qty.sum() == 15
    led2 = pd.concat([led, pd.DataFrame([T("2026-04-01", "10:00", "C", "buy", 3, 200)])], ignore_index=True)
    assert open_positions(led2).set_index("symbol").qty.to_dict() == {"C": 3}
    c = costs(led)
    assert 15.34 * 2 < c < 15.34 * 2 + 15, c   # two delivery sell days of A (B was intraday: no DP charge)
    bon = pd.DataFrame([T("2026-11-03", "15:26", "P", "buy", 7, 148.76),
                        dict(date=pd.Timestamp("2026-11-13"), time="00:00:00", symbol="P", side="adjust", qty=2.5, price=np.nan,
                             exchange="", trade_id=None, source="bonus")])
    pb = open_positions(bon).iloc[0]
    assert pb.qty == 17 and abs(pb.buy_price - 7 * 148.76 / 17) < .01, pb   # 7 + 10 bonus (half share paid in cash)
    sp = pd.concat([bon, pd.DataFrame([T("2026-11-20", "10:00", "P", "sell", 17, 70)])], ignore_index=True)
    assert abs(round_trips(sp).pnl.iloc[0] - (17 * 70 - 7 * 148.76)) < .01
    assert _factor("Bonus 3:2") == (2.5, "bonus") and _factor("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share") == (10, "split")
    print("portfolio ok")
