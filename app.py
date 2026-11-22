"""Rising Stocks Terminal. Run:  streamlit run app.py"""
import html
import os
import subprocess
import sys

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import breakouts  # chart breakouts graded by the 20-year breakout-lab evidence
import longterm  # 4-5 year lens from the long-term lab
import brief  # filings-grounded LLM brief
import tips  # reads a pasted stock tip
import fundamentals  # full financials + growth checklist
import portfolio as pf  # trade ledger, positions, round trips, costs
import rising  # exit rule + alert logic (shared with backtests)
import settings  # API keys in the OS keychain
from config import DATA, HERE, open_db
from safeio import read_json, save_json, save_text
from data_sync import adjust_prices
import insights  # trap scores, capacity bridge, order book, valuation, smart money
from viz import C, PLOT, STATUS_C, chart_layout
import viz

WATCH, POS = DATA / "watchlist.txt", DATA / "positions.csv"  # positions.csv: pre-ledger, migrated once
DEFAULT_EXCLUDE = ["Oil Gas & Consumable Fuels", "Information Technology", "Energy"]
STATUSES = ["LEADER", "EMERGING", "WAIT", "HOT", "BROKE"]
CATALYST = ("Capacity addition", "Commencement of commercial production/operations",
            "Bagging/Receiving of orders/contracts", "Outcome of Board Meeting", "Investor Presentation",
            "Acquisition", "Press Release")

st.set_page_config("rising · terminal", layout="wide", initial_sidebar_state="collapsed")
st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap');
html, body, .stMarkdown, button, input, textarea, label, p, li, td, th, summary {{
  font-family: 'JetBrains Mono', ui-monospace, Consolas, monospace !important; }}
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stSidebarCollapsedControl"],
footer, #MainMenu {{ display: none !important; }}
.block-container {{ padding: 1.4rem 2.2rem 4rem; max-width: 1440px; }}
p, li {{ font-size: .86rem; line-height: 1.65; }}
h3 {{ font-size: .9rem !important; font-weight: 600; color: {C['amber']}; margin: 1.1rem 0 .3rem !important; }}
a {{ color: {C['blue']} !important; text-decoration: none; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 2rem; border-bottom: 1px solid {C['line']}; }}
.stTabs [data-baseweb="tab"] {{ padding: .45rem 0; color: {C['dim']}; }}
.stTabs [data-baseweb="tab"] p {{ font-size: .8rem; }}
.stTabs [aria-selected="true"] {{ color: {C['text']}; }}
.stTabs [data-baseweb="tab-highlight"] {{ background: {C['amber']}; height: 1px; }}
.stButton button {{ border: 1px solid {C['line']}; background: transparent; font-size: .78rem; padding: .2rem .9rem; }}
.stButton button:hover {{ border-color: {C['amber']}; color: {C['amber']}; }}
[data-testid="stExpander"] details {{ border: 1px solid {C['line']}; }}
[data-testid="stExpander"] summary p {{ font-size: .8rem; color: {C['dim']}; }}
label p {{ font-size: .74rem !important; color: {C['dim']} !important; }}
[data-baseweb="tag"], [data-tag] {{ background: transparent !important; border: 1px solid {C['line']}; }}
[data-baseweb="tag"] span, [data-tag] span, [data-tag] {{ color: {C['text']} !important; font-size: .74rem; }}
.bar {{ display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; flex-wrap: wrap; }}
.brand {{ font-weight: 600; letter-spacing: .14em; font-size: .95rem; }}
.meta {{ color: {C['dim']}; font-size: .8rem; }}
.meta b {{ font-weight: 500; }}
.legend {{ color: {C['dim']}; font-size: .74rem; margin: .5rem 0 0; padding-bottom: .8rem;
          border-bottom: 1px solid {C['line']}; }}
.sec {{ color: {C['dim']}; font-size: .72rem; letter-spacing: .16em; text-transform: uppercase;
       margin: 2rem 0 .55rem; padding-bottom: .35rem; border-bottom: 1px solid {C['line']};
       display: flex; justify-content: space-between; }}
.sec span {{ letter-spacing: 0; text-transform: none; }}
@media (max-width: 640px) {{ .sec {{ flex-direction: column; align-items: flex-start; gap: .2rem; }} .sec span {{ text-align: left; }} }}
.tw {{ overflow-x: auto; }}
.t {{ width: 100%; border-collapse: collapse; font-size: .82rem; border: none !important; }}
.t th, .t td {{ border-left: none !important; border-right: none !important; border-top: none !important; background: transparent; }}
.t th {{ color: {C['dim']}; font-weight: 400; text-align: left; padding: .3rem 1.2rem .35rem 0;
        border-bottom: 1px solid {C['line']}; white-space: nowrap; }}
.t td {{ padding: .34rem 1.2rem .34rem 0; border-bottom: 1px solid #1b1c17; white-space: nowrap; }}
.t tr:hover td {{ background: #15160f; }}
.t .n {{ text-align: right; font-variant-numeric: tabular-nums; }}
.t td.wrap {{ white-space: normal; color: {C['dim']}; }}
.dim {{ color: {C['dim']}; }} .up {{ color: {C['green']}; }} .dn {{ color: {C['red']}; }} .am {{ color: {C['amber']}; }}
.empty {{ color: {C['dim']}; font-size: .8rem; padding: .4rem 0; }}
.note {{ color: {C['dim']}; font-size: .74rem; line-height: 1.6; margin-top: .5rem; }}
.flag {{ border-left: 2px solid {C['red']}; padding: .55rem .9rem; margin: .8rem 0; font-size: .82rem; background: #17120f; }}
.flag.warn {{ border-color: {C['amber']}; background: #17150e; }}
.kv {{ display: flex; flex-wrap: wrap; gap: .4rem 2.4rem; margin: .4rem 0 1rem; font-size: .9rem; }}
.kv > div {{ white-space: nowrap; }}
.kv > div > span:first-child {{ display: block; color: {C['dim']}; font-size: .7rem; }}
/* how-it-was-ranked: gate path */
.gates {{ display: flex; align-items: stretch; gap: .45rem; flex-wrap: wrap; margin: .5rem 0 .9rem; }}
.gate {{ flex: 1 1 230px; border: 1px solid {C['line']}; padding: .65rem .75rem .5rem; background: #12130e; }}
.gate.pass {{ border-top: 2px solid {C['green']}; }} .gate.fail {{ border-top: 2px solid {C['red']}; opacity: .95; }}
.gate.info {{ border-top: 2px solid {C['blue']}; }}
.gh {{ display: flex; justify-content: space-between; font-weight: 600; text-transform: uppercase; letter-spacing: .14em; font-size: .72rem; }}
.gnum {{ color: {C['dim']}; margin-right: .55rem; font-weight: 400; }}
.gm.pass {{ color: {C['green']}; }} .gm.fail {{ color: {C['red']}; }} .gm.info {{ color: {C['blue']}; }}
.gr {{ color: {C['dim']}; font-size: .66rem; margin: .15rem 0 .55rem; }}
.gt {{ margin: .45rem 0; }}
.gn {{ font-size: .7rem; color: {C['text']}; cursor: help; }}
.gb {{ position: relative; height: 6px; background: #1f201a; margin: .28rem 0 .2rem; }}
.gf {{ height: 100%; transition: width .6s ease; }} .gf.pass {{ background: {C['green']}; }} .gf.fail {{ background: {C['red']}; }}
.gf.info {{ background: {C['blue']}; }}
.gk {{ position: absolute; top: -4px; width: 2px; height: 14px; background: {C['text']}; }}
.gv {{ font-size: .7rem; color: {C['dim']}; }} .gv b {{ font-weight: 600; }}
.gv b.pass {{ color: {C['green']}; }} .gv b.fail {{ color: {C['red']}; }} .gv b.info {{ color: {C['blue']}; }}
.garrow {{ align-self: center; color: {C['dim']}; font-size: 1.1rem; }}
.gres {{ align-self: center; border: 1px solid; padding: .9rem 1.1rem; font-weight: 600; letter-spacing: .16em; font-size: .85rem; }}
.nx {{ display: flex; gap: .5rem; flex-wrap: wrap; margin: 0 0 .4rem; }}
.nxi {{ border: 1px solid {C['line']}; padding: .4rem .7rem; font-size: .76rem; background: #12130e; }}
.nx.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 420px), 1fr)); }}
.nxi span {{ display: block; color: {C['dim']}; font-size: .68rem; margin-top: .1rem; }}
.nxi.up {{ border-left: 2px solid {C['green']}; }} .nxi.down {{ border-left: 2px solid {C['red']}; }}
.nxi.watch {{ border-left: 2px solid {C['amber']}; }}
/* company snapshot: verdict meters */
.vc {{ display: grid; grid-template-columns: 9.5rem 7.5rem 1fr; gap: .45rem .9rem; align-items: center; font-size: .78rem; }}
.vc .vl {{ color: {C['dim']}; font-size: .72rem; text-transform: lowercase; }}
.vm {{ display: flex; gap: 2px; }} .vm i {{ flex: 1; height: 7px; background: #1f201a; }}
.vm i.on {{ outline: 1px solid {C['text']}; }}
.vr {{ color: {C['text']}; }} .vr b {{ font-weight: 600; margin-right: .4rem; }}
@media (max-width: 640px) {{ .vc {{ grid-template-columns: 1fr 7.5rem; }} .vc > div:nth-child(3n) {{ grid-column: 1 / -1; margin-bottom: .3rem; }} }}
.vc .vall {{ border-top: 1px solid {C['line']}; padding-top: .45rem; }}
.xr {{ display: block; color: {C['dim']}; font-size: .68rem; margin-top: .1rem; cursor: help; }}
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- data
def q(sql, params=None):
    """Short-lived read-only connection: never hold the lock the EOD sync needs."""
    try:
        con = open_db(wait=5)  # brief wait: the page must stay responsive while an update writes
        try:
            return con.execute(sql, params or []).df()
        finally:
            con.close()
    except duckdb.IOException:
        st.markdown('<div class="flag warn">database busy (daily sync running) — try again in a minute</div>', unsafe_allow_html=True)
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def load_seasons():
    return rising.season_data()


@st.cache_data(ttl=3600)
def base_backtest():
    return rising.backtest_rules(load_seasons())


@st.cache_data(ttl=3600)
def industry_table():
    return rising.industries()


@st.cache_data(ttl=300)
def universe():
    p = DATA / "universe.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=300)
def board():
    p = DATA / "rising_latest.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=300)
def history():
    p = DATA / "rising_history.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=300)
def prices(symbol):
    return q("""select d.trade_date, d.close, d.high, d.low, d.volume, d.delivery_pct from daily_market d join instruments i using(isin)
                where i.symbol = ? and d.trade_date >= current_date - 1100 order by d.trade_date""", [symbol])


@st.cache_data(ttl=300)
def quarters(symbol):
    db = q("""select r.quarter_end, r.sales_cr, r.op_profit_cr, r.opm_pct, r.net_profit_cr from quarterly_results r
              join instruments i using(isin) where i.symbol = ? order by quarter_end""", [symbol])
    p = DATA / "screener_q.csv"
    if p.exists():
        sc = pd.read_csv(p, parse_dates=["quarter_end"])
        sc = sc[sc.symbol == symbol].drop(columns="symbol")
        db["quarter_end"] = pd.to_datetime(db.quarter_end)
        db = pd.concat([db, sc]).drop_duplicates("quarter_end", keep="last").sort_values("quarter_end")
    return db.tail(12)


@st.cache_data(ttl=300)
def filings(days):
    return q(f"""select cast(filing_datetime as date) date, symbol, category, headline from corporate_filings
                 where filing_datetime >= current_date - {int(days)} and category in {CATALYST}
                 order by filing_datetime desc""")


def latest_q(symbol):
    """Latest quarter's YoY growth for a symbol."""
    qd = quarters(symbol)
    if len(qd) < 5:
        return None
    qd = qd.set_index(pd.to_datetime(qd.quarter_end))
    cur = qd.iloc[-1]
    prev = qd[qd.index == cur.name - pd.offsets.QuarterEnd(4)]
    if prev.empty:
        return None
    prev = prev.iloc[0]
    yoy = lambda c: (cur[c] - prev[c]) / abs(prev[c]) if prev[c] else float("nan")
    return dict(quarter_end=cur.name, sales_yoy=yoy("sales_cr"), pat_yoy=yoy("net_profit_cr"))


# ---------------------------------------------------------------- rendering helpers
def esc(x):
    return html.escape(str(x))


def pct(x, d=0):
    if pd.isna(x):
        return '<span class="dim">–</span>'
    if abs(x) == float("inf"):  # growth from a base of exactly zero: no percentage exists
        return '<span class="up" title="a year ago this was zero, so there is no growth percentage">from 0</span>'
    return f'<span class="{"up" if x > 0 else "dn" if x < 0 else "dim"}">{x * 100:+.{d}f}%</span>'


def num(x, d=1):
    return '<span class="dim">–</span>' if pd.isna(x) else f"{x:,.{d}f}"


def status(s):
    return f'<span style="color:{STATUS_C.get(s, C["dim"])}">{esc(s).lower()}</span>'


def table(rows, cols, numeric=()):
    """rows: list of dicts of pre-rendered HTML cells; cols: [(key, header)]."""
    if not rows:
        return '<div class="empty">none</div>'
    th = "".join(f'<th class="{"n" if k in numeric else ""}">{h}</th>' for k, h in cols)
    tr = "".join("<tr>" + "".join(f'<td class="{"n" if k in numeric else "wrap" if k == "_wrap" else ""}">{r.get(k, "")}</td>'
                                  for k, _ in cols) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="t"><tr>{th}</tr>{tr}</table></div>'


def sec(title, right=""):
    st.markdown(f'<div class="sec">{title}<span>{right}</span></div>', unsafe_allow_html=True)


def clip(t, n):
    """Shorten to n characters at a word boundary, with an ellipsis when cut."""
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[:t.rfind(" ", 0, n)].rstrip(",;:-") + "…"


def tidy_about(t):
    """Screener's description is stored cut at 600 characters: end it at the last full sentence, else a whole word."""
    t = (t or "").strip()
    if len(t) < 590 or t.endswith("."):
        return t
    cut = t.rfind(". ")
    return t[:cut + 1] if cut > 200 else t[:t.rfind(" ")].rstrip(",;:") + "…"


def show(htm):
    st.markdown(htm, unsafe_allow_html=True)


# ---------------------------------------------------------------- growth story (stock tab)
@st.cache_data(ttl=3600)
def all_symbols():
    d = q("select symbol from instruments order by symbol")
    return list(d.symbol) if len(d) else []


@st.cache_data(ttl=3600, show_spinner="fetching fundamentals…")
def load_fundamentals(sym):
    try:
        return fundamentals.get(sym)
    except Exception:
        return None


MARK = {"pass": ("✓", "up"), "watch": ("~", "am"), "fail": ("✗", "dn"), "n/a": ("·", "dim")}


def _r(df, name):
    return df.loc[name] if df is not None and name in df.index else None


def mini(title, fig, col, h=230):
    col.markdown(f'<div class="meta">{title}</div>', unsafe_allow_html=True)
    fig = chart_layout(fig, h)
    fig.update_xaxes(tickangle=0, nticks=5)
    col.plotly_chart(fig, width="stretch", config=PLOT)


RANGES = {"1M": 21, "3M": 63, "6M": 126, "1Y": 250, "2Y": 500, "3Y": 750}  # trading sessions, as the scan counts them


def price_chart(sym, r):
    """Price with a plain-English summary, range buttons, trend line, your buy/stop, and volume. Hover only, no
    drag-zoom: picking a range re-slices the data so the y-axis always fits what you see."""
    px_ = prices(sym)
    if px_.empty:
        return
    px_ = px_.assign(trade_date=pd.to_datetime(px_.trade_date), close=adjust_prices(px_.close)).dropna(subset=["close"])
    px_["dma200"] = px_.close.rolling(200, min_periods=150).mean()  # computed on full history, before slicing
    head = st.container()  # the heading needs the chosen range, but sits above the buttons
    rng = st.segmented_control("range", list(RANGES), default="1Y", key=f"rng_{sym}", label_visibility="collapsed") or "1Y"
    d = px_.tail(RANGES[rng] + 1)  # same windows as the scan: 1Y = today vs 250 sessions ago, so the numbers agree
    last, first = d.close.iloc[-1], d.close.iloc[0]
    chg = last / first - 1
    hi52 = px_.tail(251).close.max()
    dma = px_.dma200.iloc[-1]

    trend = ("" if pd.isna(dma) else
             f' · {pct(last / dma - 1)} vs 200-day avg — <span class="{"up" if last > dma else "dn"}">'
             f'{"uptrend" if last > dma else "downtrend"}</span>')
    with head:
        sec("price", f"₹{last:,.2f} · {pct(chg, 1)} over {rng}{trend} · {pct(last / hi52 - 1)} from 52-week high")

    held = pos[pos.symbol == sym]
    line_c = C["green"] if chg >= 0 else C["red"]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[.8, .2], vertical_spacing=.03)
    fig.add_trace(go.Scatter(x=d.trade_date, y=d.close, name="price", line=dict(color=line_c, width=1.6),
                             text=[f"{x:+.1%}" for x in d.close / first - 1],
                             hovertemplate="₹%{y:,.2f}  (%{text} in range)<extra>price</extra>"), 1, 1)
    if d.dma200.notna().any():
        fig.add_trace(go.Scatter(x=d.trade_date, y=d.dma200, name="200-day average (trend)",
                                 line=dict(color=C["dim"], width=1, dash="dot"),
                                 hovertemplate="₹%{y:,.0f}<extra>200-day avg</extra>"), 1, 1)

    # horizontal reference lines, labelled at the right edge
    refs = [(hi52, "52-week high", C["dim"], "dot")]
    if pd.notna(r.get("trigger")):
        refs.append((r.trigger, "buy trigger", C["blue"], "dash"))
    if len(held):
        bp = float(held.buy_price.iloc[0])
        refs += [(bp, "your buy", C["amber"], "solid"), (bp * (1 - rising.STOP_PCT), "your stop −20%", C["red"], "dash")]
    for y, label, color, dash in refs:
        fig.add_hline(y=y, row=1, col=1, line=dict(color=color, width=1, dash=dash), opacity=.8,
                      annotation_text=f"{label} ₹{y:,.0f}", annotation_position="top left",
                      annotation_font=dict(color=color, size=10))
    fig.add_trace(go.Scatter(x=[d.trade_date.iloc[-1]], y=[last], mode="markers+text", text=[f" ₹{last:,.1f}"],
                             textposition="middle right", textfont=dict(color=line_c, size=11),
                             marker=dict(color=line_c, size=6), showlegend=False, hoverinfo="skip"), 1, 1)

    # smart money: insider (promoter / director) buys ▲ and sells ▼ placed on the price line, sized by value
    try:
        pit = insights.insider_trades(sym)
    except Exception:
        pit = pd.DataFrame()
    if len(pit):
        pit = pit[(pit.date >= d.trade_date.iloc[0]) & (pit.value_cr >= .05)]
        on_line = d.set_index("trade_date").close
        for side, color, sym_ in (("Buy", C["green"], "triangle-up"), ("Sell", C["red"], "triangle-down")):
            s = pit[pit.side == side]
            if s.empty:
                continue
            y = [on_line.asof(t) for t in s.date]
            fig.add_trace(go.Scatter(
                x=s.date, y=y, mode="markers", name=f"insider {side.lower()}s",
                marker=dict(symbol=sym_, color=color, size=(8 + s.value_cr.clip(upper=200) ** .5 * 2).tolist(),
                            line=dict(color=C["bg"], width=1)),
                customdata=list(zip(s.who, s.name.str.title(), s.value_cr.round(2))),
                hovertemplate=f"{side.lower()}: %{{customdata[1]}} (%{{customdata[0]}})<br>₹%{{customdata[2]:,}} Cr<extra>insider</extra>"), 1, 1)

    fig.add_trace(go.Bar(x=d.trade_date, y=d.volume, name="volume", marker_color="#3b3d34", showlegend=False,
                         hovertemplate="%{y:,.0f} shares<extra>volume</extra>"), 2, 1)

    # fit the visible prices, plus any reference line within 25% of them (a far-away 52w high shouldn't squash the line)
    lo, top = d.close.min(), d.close.max()
    near = [y for y, *_ in refs if lo * .75 <= y <= top * 1.25]
    lo, top = min([lo] + near), max([top] + near)
    chart_layout(fig, 400).update_layout(
        dragmode=False, hovermode="x unified", margin=dict(l=0, r=60, t=24, b=0),
        hoverlabel=dict(bgcolor="#171813", bordercolor=C["line"], font=dict(family="JetBrains Mono, monospace", size=11)),
        legend=dict(orientation="h", y=1.07, x=0, font=dict(size=10)))
    fig.update_xaxes(fixedrange=True, showspikes=True, spikemode="across", spikethickness=1, spikecolor=C["dim"], spikedash="dot")
    fig.update_yaxes(fixedrange=True, tickprefix="₹", row=1, col=1, range=[lo * .97, top * 1.03])
    fig.update_yaxes(fixedrange=True, showticklabels=False, showgrid=False, row=2, col=1)
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False, "scrollZoom": False})
    show('<div class="note">adjusted for splits &amp; bonuses · hover to read any day · green/red = up/down over the chosen range · '
         'price above the dotted 200-day average = uptrend · ▲▼ = insiders buying / selling (bigger = more money)'
         + (' · amber = your buy, red dashed = your stop' if len(held) else '') + '</div>')


def growth_story(fd):
    T = fd["tables"]
    checks = fundamentals.checklist(fd)
    n_pass = sum(v == "pass" for v, *_ in checks)
    rated = sum(v != "n/a" for v, *_ in checks)

    left, right = st.columns([3, 2], gap="large")
    with left:
        sec("growth check", f"{n_pass} of {rated} pass · {fd['basis']} · screener {fd['fetched']}")
        show(table([dict(m=f'<span class="{MARK[v][1]}">{MARK[v][0]}</span>', t=t, _wrap=esc(d)) for v, t, d in checks],
                   [("m", ""), ("t", "check"), ("_wrap", "evidence")]))
    with right:
        g = fd.get("growth", {})
        sec("compounded growth", "per year")
        rows = []
        for label, key in [("sales", "Compounded Sales Growth"), ("profit", "Compounded Profit Growth"),
                           ("share price", "Stock Price CAGR"), ("return on equity", "Return on Equity")]:
            gv = g.get(key, {})
            vals = [gv.get(k) for k in ("10 Years", "5 Years", "3 Years")] + [gv.get("TTM", gv.get("1 Year", gv.get("Last Year")))]
            rows.append(dict(l=label, **{f"c{i}": (pct(x / 100) if pd.notna(x) else '<span class="dim">–</span>') for i, x in enumerate(vals)}))
        show(table(rows, [("l", ""), ("c0", "10y"), ("c1", "5y"), ("c2", "3y"), ("c3", "latest")], numeric={"c0", "c1", "c2", "c3"}))
        show('<div class="note">latest = last 12 months for sales/profit, last year for price and ROE</div>')

    sym = fd["symbol"]
    px_ = prices(sym)
    if len(px_):
        px_ = px_.assign(trade_date=pd.to_datetime(px_.trade_date), close=adjust_prices(px_.close)).dropna(subset=["close"])

    # ---- traps: one safety shape instead of a page of ratios
    with st.spinner("checking exchange watch-lists and insider trades…"):
        try:
            scores = insights.trap_scores(fd, sym, px_ if len(px_) else None)
        except Exception as e:
            scores = None
            sec("safety check")
            show(f'<div class="note">safety check unavailable right now ({esc(type(e).__name__)}: {esc(str(e)[:120])}) — '
                 'usually NSE being slow; reopen the stock in a minute.</div>')
    if scores:
        worst = sorted(scores, key=lambda s: s[1])
        sec("safety check", "full shape = safe · every dent is a specific risk · hover a point for the evidence")
        c1, c2 = st.columns([3, 2], gap="large")
        c1.plotly_chart(viz.trap_radar(scores), width="stretch", config=PLOT)
        with c2:
            show(table([dict(s=f'<span style="color:{viz.score_color(s)}">{s}</span>', a=esc(a), _wrap=esc(d)) for a, s, d in worst],
                       [("s", "score"), ("a", "check"), ("_wrap", "evidence")], numeric={"s"}))
            bad = [a for a, s, _ in worst if s < 40]
            show(f'<div class="note">{"⚠ weak spots: <b>" + ", ".join(bad) + "</b> — read these before buying" if bad else "no weak spots found"}</div>')

    # ---- future growth: capacity coming online + revenue already booked
    bridge = insights.capacity_bridge(fd)
    try:
        orders = insights.order_book(sym)
    except Exception:
        orders = dict(book=None, wins=[])
    has_cap = bridge and bridge["potential_add"] > 0.02 * bridge["ttm_sales"]
    if has_cap or orders["book"] or orders["wins"]:
        sec("future growth", "plants being built and orders already won — growth that hasn't shown up in sales yet")
        c1, c2 = st.columns([3, 2], gap="large")
        if has_cap:
            with c1:
                show('<div class="meta">capacity → sales: what the plants under construction could add</div>')
                st.plotly_chart(viz.capacity_waterfall(bridge), width="stretch", config=PLOT)
                show(f'<div class="note">₹{bridge["cwip"]:,.0f} Cr of plant under construction × ₹{bridge["turnover"]:.1f} of sales '
                     f'per ₹1 of plant today. A rough ceiling, reached only when the plants run full — check the AI brief '
                     f'or filings for commissioning dates.</div>')
        with c2 if has_cap else c1:
            if orders["book"] and bridge:
                b_ = orders["book"]
                show('<div class="meta">order book: years of sales already booked</div>')
                st.plotly_chart(viz.order_gauge(b_["value_cr"], bridge["ttm_sales"]), width="stretch", config=PLOT)
                show(f'<div class="note">₹{b_["value_cr"]:,} Cr order book (<a href="{b_["url"]}" target="_blank">'
                     f'{esc(b_["source"]).lower()}, {b_["date"]}</a>) vs ₹{bridge["ttm_sales"]:,.0f} Cr sales in the last 12 months</div>')
            if orders["wins"]:
                show('<div class="meta">order wins announced</div>')
                st.plotly_chart(viz.order_wins(orders["wins"]), width="stretch", config=PLOT)

    # revenue & profit with the yoy growth printed on every bar
    sec("revenue & profit", "₹ crore · label = growth vs a year earlier")
    mode = st.segmented_control("view", ["quarterly", "annual"], default="quarterly", key=f"rp_{fd['symbol']}",
                                label_visibility="collapsed") or "quarterly"
    src = T.get("quarters") if mode == "quarterly" else T.get("annual")
    sales, prof = _r(src, "Sales"), _r(src, "Net Profit")
    if sales is not None:
        cols = list(sales.index)[-12:] if mode == "quarterly" else list(sales.index)[-8:]
        lag = 4 if mode == "quarterly" else 1
        full = list(sales.index)
        # TTM vs last fiscal year isn't a year-on-year comparison, so it gets no label
        yoy = [sales[c] / sales[full[full.index(c) - lag]] - 1
               if c != "TTM" and full.index(c) >= lag and sales[full[full.index(c) - lag]] else None for c in cols]
        fig = go.Figure([
            go.Bar(x=cols, y=sales[cols], name="sales", marker_color="#3b3d34",
                   text=[f"{y:+.0%}" if y is not None else "" for y in yoy], textposition="outside",
                   textfont=dict(color=[C["green"] if (y or 0) > 0 else C["red"] for y in yoy], size=10),
                   hovertemplate="%{x}<br>sales ₹%{y:,.0f} Cr<extra></extra>"),
            go.Bar(x=cols, y=prof[cols] if prof is not None else None, name="net profit", marker_color=C["green"],
                   hovertemplate="%{x}<br>profit ₹%{y:,.0f} Cr<extra></extra>")])
        fig.update_layout(barmode="group", bargap=.3, bargroupgap=.06, hovermode="closest")
        st.plotly_chart(chart_layout(fig, 340), width="stretch", config=PLOT)

    sec("quality of growth", "is the growth profitable, cash-backed and efficient?")
    c1, c2, c3 = st.columns(3, gap="medium")
    band = insights.margin_band(fd)
    if band:
        df, state = band
        c1.markdown(f'<div class="meta">operating margin vs its usual range · <span class="'
                    f'{"up" if state == "breakout" else "dn" if state == "breakdown" else "dim"}">{state}</span></div>',
                    unsafe_allow_html=True)
        c1.plotly_chart(viz.margin_band_chart(df.tail(12), 230), width="stretch", config=PLOT)
    cfo, npa = _r(T.get("cashflow"), "Cash from Operating Activity"), _r(T.get("annual"), "Net Profit")
    if cfo is not None and npa is not None:
        yrs = [c for c in cfo.index if c in npa.index][-6:]
        fig = go.Figure([go.Bar(x=yrs, y=cfo[yrs], name="operating cash", marker_color=C["blue"],
                                hovertemplate="%{x}: ₹%{y:,.0f} Cr<extra></extra>"),
                         go.Bar(x=yrs, y=npa[yrs], name="net profit", marker_color=C["green"],
                                hovertemplate="%{x}: ₹%{y:,.0f} Cr<extra></extra>")])
        fig.update_layout(barmode="group", bargap=.35)
        mini("cash vs profit · yearly (cash ≥ profit is healthy)", fig, c2)
    roce = _r(T.get("ratios"), "ROCE %")
    if roce is not None:
        x = list(roce.dropna().index)[-8:]
        fig = go.Figure(go.Scatter(x=x, y=roce[x], mode="lines+markers", name="ROCE", line=dict(color=C["text"], width=1.5),
                                   marker=dict(size=4), hovertemplate="%{x}: %{y:.0f}%<extra></extra>"))
        fig.add_hline(y=15, line=dict(color=C["dim"], width=1, dash="dot"), annotation_text="15%", annotation_font_color=C["dim"])
        fig.update_yaxes(ticksuffix="%")
        mini("return on capital · yearly", fig, c3)

    # ---- valuation: is the growth already in the price?
    pe = insights.pe_history(fd, px_[["trade_date", "close"]]) if len(px_) else None
    if pe is not None and len(pe) > 60:
        g3 = fd.get("growth", {}).get("Compounded Profit Growth", {}).get("3 Years")
        peg = pe.iloc[-1] / g3 if g3 and g3 > 0 else None
        rel = pe.iloc[-1] / pe.median() - 1
        sec("is the growth already priced in?", "P/E = price ÷ yearly profit per share · lower vs its own history = cheaper")
        c1, c2 = st.columns([3, 2], gap="large")
        c1.plotly_chart(viz.pe_chart(pe), width="stretch", config=PLOT)
        with c2:
            show(table([dict(k="P/E today", v=f"{pe.iloc[-1]:.0f}"),
                        dict(k="its usual P/E", v=f"{pe.median():.0f}"),
                        dict(k="vs usual", v=(f'<span class="{"dn" if rel > .15 else "up" if rel < -.1 else "am"}">'
                                              f'{abs(rel):.0%} {"above" if rel > 0 else "below"}</span>') if pd.notna(rel) else "–"),
                        dict(k="profit growth (3y/yr)", v=f"{g3:.0f}%" if pd.notna(g3) and g3 else "–"),
                        dict(k="PEG (P/E ÷ growth)", v=f'<span class="{"up" if peg and peg < 1 else "am" if peg and peg < 2 else "dn"}">{peg:.1f}</span>' if pd.notna(peg) and peg else "–")],
                       [("k", ""), ("v", "")], numeric={"v"}))
            show('<div class="note">PEG under 1 = paying less than its growth rate; over 2 = a lot of future growth is already in the price. '
                 '"vs usual" is green when it\'s cheaper than its own history.</div>')

    sec("balance sheet & owners", "is it building capacity, and who is holding?")
    c1, c2, c3 = st.columns(3, gap="medium")
    bal = T.get("balance")
    fa, cw = _r(bal, "Fixed Assets"), _r(bal, "CWIP")
    if fa is not None:
        x = list(fa.index)[-6:]
        fig = go.Figure([go.Bar(x=x, y=fa[x], name="running plants", marker_color="#3b3d34",
                                hovertemplate="%{x}: ₹%{y:,.0f} Cr<extra></extra>"),
                         go.Bar(x=x, y=cw[x] if cw is not None else None, name="under construction", marker_color=C["amber"],
                                hovertemplate="%{x}: ₹%{y:,.0f} Cr<extra></extra>")])
        fig.update_layout(barmode="stack", bargap=.4)
        mini("capacity · fixed assets + work in progress", fig, c1)
    debt = _r(bal, "Borrowings")
    eq = _r(bal, "Equity Capital")
    res = _r(bal, "Reserves")
    if debt is not None and eq is not None:
        x = list(debt.index)[-6:]
        fig = go.Figure([go.Bar(x=x, y=debt[x], name="debt", marker_color=C["red"], opacity=.75,
                                hovertemplate="%{x}: debt ₹%{y:,.0f} Cr<extra></extra>"),
                         go.Scatter(x=x, y=(eq + (res if res is not None else 0))[x], name="shareholders' money",
                                    line=dict(color=C["text"], width=1.5), hovertemplate="%{x}: equity ₹%{y:,.0f} Cr<extra></extra>")])
        mini("debt vs shareholders' money", fig, c2)
    sh = T.get("shareholding")
    ch = insights.ownership_changes(fd)
    if ch is not None:
        now_ = " · ".join(f"{k.lower()} {sh.loc[k].iloc[-1]:.1f}%" for k in ("Promoters", "FIIs", "DIIs") if k in sh.index)
        c3.markdown(f'<div class="meta">smart money: change in ownership each quarter<br>{now_}</div>', unsafe_allow_html=True)
        c3.plotly_chart(viz.ownership_change_bars(ch, 230), width="stretch", config=PLOT)


# ---------------------------------------------------------------- guards + header
b = board()


UPDATE_LOG = DATA / "update.log"


def run_update():
    """Start the update as its own process writing to data/update.log. The page never waits on it: a small progress
    box refreshes itself (update_progress) while the rest of the app stays usable, and reloading or closing the page
    can't break the update."""
    if not rising.update_running():
        import time
        with open(UPDATE_LOG, "w", encoding="utf-8") as log:
            p = subprocess.Popen([sys.executable, str(HERE / "rising.py"), "update"], cwd=HERE, stdout=log, stderr=subprocess.STDOUT,
                                 env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        while p.poll() is None and not rising.update_running():  # wait for the update to mark itself running
            time.sleep(.2)
    st.session_state["upd_watch"] = True


@st.fragment(run_every="2s")
def update_progress():
    """Progress of a running update, refreshed on its own every 2 s; when it finishes, reload the app's data."""
    if rising.update_running():
        st.session_state["upd_watch"] = True
        lines = [ln for ln in (UPDATE_LOG.read_text(encoding="utf-8", errors="replace").splitlines() if UPDATE_LOG.exists() else [])
                 if ln.strip() and "litellm" not in ln.lower()]
        show(f'<div class="flag warn">updating — <b>{esc(lines[-1].strip() if lines else "starting…")}</b>'
             '<br><span class="dim">keep using the app; the data reloads by itself when the update finishes</span></div>')
        with st.expander("update log"):
            st.code("\n".join(lines[-60:]) or "starting…", language=None)
    elif st.session_state.pop("upd_watch", False):
        st.cache_data.clear()
        st.rerun(scope="app")


@st.dialog("settings", width="large")
def settings_dialog():
    st.markdown('<div class="meta">market data needs no key. an AI key is optional — it only powers the filing briefs.</div>',
                unsafe_allow_html=True)
    cur = settings.load()
    names = list(settings.PROVIDERS)
    prov = st.selectbox("AI provider", names, index=names.index(cur["provider"]))
    env_var, default_model, key_url = settings.PROVIDERS[prov]
    model = st.text_input("model", cur["model"] if prov == cur["provider"] else default_model,
                          disabled=settings.model_locked(),
                          help="any litellm model id, e.g. " + default_model + (" — pinned by DEFAULT_LLM_MODEL in .env"
                                                                                 if settings.model_locked() else ""))
    key = st.text_input(f"API key — {settings.masked(prov)}", type="password", placeholder="paste a new key to save or replace",
                        help=f"get one at {key_url}")
    show(f'<div class="note">keys are stored in your operating system\'s credential store (Windows Credential Manager / '
         f'macOS Keychain), never in a file or the repo, and never shown again. '
         f'{"" if settings.keychain_ok() else "<span class=dn>no credential store found on this machine — use .env instead.</span>"}</div>')
    c1, c2, c3 = st.columns(3)
    if c1.button("save", width="stretch"):
        settings.save(prov, model, key or None)
        st.rerun()
    if c2.button("test", width="stretch", help="sends one tiny request to your provider"):
        if key:
            settings.save(prov, model, key)
        try:
            from litellm import completion
            r = completion(model=os.environ["DEFAULT_LLM_MODEL"], messages=[{"role": "user", "content": "reply with: ok"}],
                           max_tokens=5, timeout=30)
            st.success(f"works — {os.environ['DEFAULT_LLM_MODEL']} replied “{r.choices[0].message.content.strip()}”")
        except Exception as e:
            st.error(f"failed: {str(e)[:300]}")
    if c3.button("remove key", width="stretch"):
        settings.save(prov, model, "")
        st.rerun()


left, pfb, mid, right = st.columns([5, 1, 1, 1])
in_pf = st.session_state.get("view") == "portfolio"
if pfb.button("← terminal" if in_pf else "portfolio", width="stretch", type="primary" if in_pf else "secondary"):
    st.session_state["view"] = "terminal" if in_pf else "portfolio"
    st.rerun()
if mid.button("settings", width="stretch"):
    settings_dialog()
if b.empty:
    left.markdown('<div class="brand">RISING<span class="dim"> // nse growth terminal</span></div>', unsafe_allow_html=True)
    if right.button("update data", width="stretch"):
        run_update()
    update_progress()
    show('<div class="flag warn">no data yet — click <b>update data</b>.<br><span class="dim">the first run downloads ~1.5 years of '
         'NSE prices and the results of every liquid company (~1,200) from public sources: about 30 minutes, once. '
         'after that a daily update takes about a minute. no API key needed.</span></div>')
    st.stop()

asof = pd.Timestamp(b["asof"].iloc[0])
cnt = b.status.value_counts()
counts = " · ".join(f'{status(s)} <b>{int(cnt.get(s, 0))}</b>' for s in STATUSES)
left.markdown(f'<div class="bar"><div class="brand">RISING<span class="dim"> // nse growth terminal</span></div>'
              f'<div class="meta">data {asof:%a %d %b %Y} &nbsp;·&nbsp; {counts}</div></div>', unsafe_allow_html=True)
if right.button("update data", width="stretch", help="prices + NSE filings + new results + rescan (≈1–10 min)",
                disabled=rising.update_running()):
    run_update()
update_progress()  # shows a running update (also one started elsewhere: scheduled task, another window)
if not in_pf:
    show('<div class="legend">leader = growing + already ran 80–150% · hot = ran &gt;150%, not a buy · emerging = growth accelerating, price confirmed · '
         'wait = watch only, buy above trigger · exit = −20% stop or 6-month review · research tool, not advice</div>')

# Freshness: NSE publishes a trading day's file ~6–8 pm IST. New data can exist once 7 pm has passed on the next
# weekday after the board's date. If an update already checked NSE after that moment and found nothing (holiday,
# late file), stay quiet until the following weekday — so the banner never nags after a successful update.
state_p = DATA / "sync_state.json"
state = read_json(state_p, {})
kind, when = rising.freshness(asof, state, pd.Timestamp.now())
if kind == "failed":
    show(f'<div class="flag">last update failed{f" ({when:%d %b %H:%M})" if when is not None else ""}: {esc(state.get("error", ""))}<br>'
         '<span class="dim">the board still shows older data. click <b>update data</b> to retry.</span></div>')
elif kind == "stale" and not rising.update_running():
    show(f'<div class="flag warn">newer market data may be out ({when:%a %d %b}) — click <b>update data</b></div>')

excl = st.session_state.get("excl", [i for i in DEFAULT_EXCLUDE if i in set(b.industry.dropna())])
watch = WATCH.read_text().split() if WATCH.exists() else []
pf.migrate(POS)
pos = pf.open_positions()  # only trades still open: anything sold has left on its own
pos["thesis"] = pos.symbol.map(pf.notes()).fillna("").astype(str)
THESIS = DATA / "thesis"


def snapshot_thesis(sym, thesis):
    """Freeze the growth check when you write why you bought, so it can be compared every quarter."""
    THESIS.mkdir(exist_ok=True)
    p_ = THESIS / f"{sym}.json"
    old = read_json(p_)
    if old and old.get("thesis") == thesis:
        return
    f = fundamentals.get(sym)
    checks = {t_: v_ for v_, t_, _ in fundamentals.checklist(f)} if f else {}
    save_json(p_, dict(thesis=thesis, date=str(pd.Timestamp.now().date()), checks=checks))
bs = b.set_index("symbol")


# ---------------------------------------------------------------- portfolio (its own page, button at the top)
def trade_insights(t, o, open_value):
    """Plain-English findings from your own trades — the things a broker statement doesn't tell you.
    -> list of (kind, headline, detail) for the insight cards; kind in up / down / watch."""
    out = []
    if len(t):
        w, l_ = t[t.pnl > 0], t[t.pnl <= 0]
        pay = (w.ret.mean() / -l_.ret.mean()) if len(w) and len(l_) and l_.ret.mean() < 0 else None
        out.append(("up" if t.pnl.sum() > 0 else "down",
                    f"{len(w)} of {len(t)} trades won · average win {w.ret.mean():+.1%} vs loss {l_.ret.mean():+.1%}"
                    if len(w) and len(l_) else f"{len(w)} of {len(t)} trades won",
                    (f"each win is {pay:.1f}× the size of each loss" if pay else "")
                    + f" · ₹{t.pnl.sum() / len(t):+,.0f} per trade on average"))
        g = t.groupby("group").agg(n=("ret", "size"), r=("ret", "mean"), pnl=("pnl", "sum"))
        if "system pick" in g.index and len(g) > 1:
            sp, rest = g.loc["system pick"], g.drop("system pick")
            rr = (t[t.group != "system pick"].ret.mean())
            few = " · too few system picks to judge yet" if sp.n < 5 else ""
            pl = lambda n: f"{int(n)} trade{'s' if n != 1 else ''}"
            out.append(("up" if sp.r > rr else "watch",
                        f"system picks {sp.r:+.1%} vs your own picks {rr:+.1%}",
                        f"{pl(sp.n)} bought while the scan said leader/emerging · {pl(rest.n.sum())} bought off the "
                        f"system (wait, hot, no label or too small to scan) · judged on the day you bought{few}"))
        elif "system pick" not in g.index:
            out.append(("watch", "none of your closed trades were system picks",
                        "on the day you bought, none was a leader or emerging name — the backtest edge doesn't apply to them"))
        crossed = t[t.crossed_stop]
        if len(crossed):
            held_on = crossed[crossed.ret < -rising.STOP_PCT]
            cost = float(((-rising.STOP_PCT - held_on.ret) * held_on.invested).sum()) if len(held_on) else 0
            rec = crossed[crossed.ret >= -rising.STOP_PCT]
            out.append(("down" if cost > 0 else "watch",
                        f"{len(crossed)} trade{'s' if len(crossed) > 1 else ''} went past the −{rising.STOP_PCT:.0%} stop",
                        (f"holding on cost ₹{cost:,.0f} more than selling at the stop" if cost > 0 else "")
                        + (f"{' · ' if cost > 0 else ''}{len(rec)} recovered above it before you sold" if len(rec) else "")))
        if len(w) >= 2 and len(l_) >= 2:
            dw, dl = w.days.median(), l_.days.median()
            if dl > dw * 1.5 and dl - dw >= 5:
                out.append(("down", f"losers held {dl:.0f} days, winners {dw:.0f}",
                            "the classic trap: cutting winners early and waiting for losers to come back"))
            else:
                out.append(("up", f"winners held {dw:.0f} days, losers {dl:.0f}", "you're not sitting on losers longer than winners"))
        aft = t.dropna(subset=["after"])
        if len(aft) >= 3:
            wa = aft[aft.pnl > 0].after.median() if (aft.pnl > 0).any() else None
            la = aft[aft.pnl <= 0].after.median() if (aft.pnl <= 0).any() else None
            bits = []
            if wa is not None:
                bits.append(f"winners you sold moved {wa:+.1%} since")
            if la is not None:
                bits.append(f"losers you sold moved {la:+.1%} since")
            out.append(("watch" if (wa or 0) > .1 else "up", "after you sold: " + " · ".join(bits),
                        "a big positive number on winners means you sell too early; on losers it means you sold the bottom"))
        gross = float(t.pnl.sum())
        fees = pf.costs()
        med = float((t.qty * t.avg_buy).median())
        be = (pf.DP_CHARGE + .0023 * med) / med if med else 0
        out.append(("down" if gross > 0 and fees > .25 * gross else "watch",
                    f"charges ≈ ₹{fees:,.0f}" + (f" — {fees / gross:.0%} of what you made" if gross > 0 else ""),
                    f"your typical trade is ₹{med:,.0f}: it needs a {be:.1%} move just to cover taxes and the "
                    f"₹{pf.DP_CHARGE} DP charge on the sale · fewer, larger trades keep more of the gain"))
    if len(o):
        o_ = o.dropna(subset=["last"])
        at_risk = float((o_.qty * (o_.last - o_.buy_price * (1 - rising.STOP_PCT))).clip(lower=0).sum())
        past = o_[o_.last <= o_.buy_price * (1 - rising.STOP_PCT)]
        top = o_.assign(v=o_.qty * o_.last).sort_values("v", ascending=False).iloc[0] if len(o_) else None
        out.append(("down" if len(past) else "watch",
                    f"if every stop hits you lose ₹{at_risk:,.0f} more" + (f" · {len(past)} already past the stop" if len(past) else ""),
                    (f"{', '.join(past.symbol)} {'is' if len(past) == 1 else 'are'} below −{rising.STOP_PCT:.0%}: the rule says sell. "
                     if len(past) else "")
                    + (f"largest position {top.symbol} = {top.qty * top.last / open_value:.0%} of what you hold" if top is not None and open_value else "")))
    return out


def portfolio_page():
    pf.refresh_actions(set(pf.load(actions=False).symbol))  # bonuses/splits from NSE, once a day per stock
    led = pf.load()
    o = pf.open_positions(led)
    nts = pf.notes()
    t = pf.round_trips(led)
    syms = sorted(set(t.symbol) | set(o.symbol))
    since = min([led.date.min()] if len(led) else [pd.Timestamp.now()]) - pd.Timedelta(days=5)
    px_all = pf.price_paths(syms, since) if syms else pd.DataFrame()
    o["last"] = [px_all[s].dropna().iloc[-1] if s in px_all and px_all[s].notna().any() else np.nan for s in o.symbol]
    # adjusted prices are in today's units: the latest close is the real last price
    ctx = pf.entry_context(list(zip(t.symbol, t.entry)) + list(zip(o.symbol, o.buy_date)))
    lab = lambda s, d: ctx.get(f"{s}|{pd.Timestamp(d).date()}", {}).get("label", "–")
    if len(t):
        t["label"] = [lab(s, d) for s, d in zip(t.symbol, t.entry)]
        t["group"] = t.label.map(viz.pick_group)
    t, o = pf.behaviour(t, o, px_all) if len(t) or len(o) else (t, o)
    o["value"], o["cost"] = o.qty * o["last"], o.qty * o.buy_price

    # ---- summary
    open_value, open_cost = float(o.value.sum()), float(o.cost.sum())
    realised, fees = float(t.pnl.sum()) if len(t) else 0.0, pf.costs(led)
    wins = int((t.pnl > 0).sum()) if len(t) else 0
    kv = [("holding", f"₹{open_value:,.0f}"), ("unrealised", pct(open_value / open_cost - 1, 1) if open_cost else "–"),
          ("realised", f'<span class="{"up" if realised >= 0 else "dn"}">₹{realised:+,.0f}</span>'),
          ("charges (est.)", f"₹{fees:,.0f}"), ("net realised", f'<span class="{"up" if realised - fees >= 0 else "dn"}">₹{realised - fees:+,.0f}</span>'),
          ("closed trades", f"{len(t)}"), ("win rate", f"{wins / len(t):.0%}" if len(t) else "–"),
          ("open", f"{len(o)}")]
    show('<div class="kv">' + "".join(f"<div><span>{k}</span>{v}</div>" for k, v in kv) + "</div>")
    if led.empty:
        show('<div class="flag warn">no trades yet — import your broker tradebook or add a trade below.</div>')

    # ---- open positions
    if len(o):
        sec("open positions", f"₹{open_value:,.0f} held · ₹{open_cost:,.0f} invested · stop −{rising.STOP_PCT:.0%} · review at 6 months")
        rows = []
        for r in o.sort_values("value", ascending=False).itertuples():
            stop_px = r.buy_price * (1 - rising.STOP_PCT)
            now_st = bs.status.get(r.symbol, None)
            then = lab(r.symbol, r.buy_date)
            al = rising.position_alerts(r.buy_price, r.buy_date, r.last, asof, now_st or "–", latest_q(r.symbol)) if pd.notna(r.last) else []
            sig = '<span class="dim">hold</span>'
            if al:
                lvl, msg = al[0]
                sig = f'<span class="{"dn" if lvl == "EXIT" else "am"}">{lvl.lower()}</span> <span class="dim">{esc(msg)}</span>'
            rows.append(dict(sym=f"<b>{esc(r.symbol)}</b>", qty=f"{r.qty:g}", buy=num(r.buy_price, 2), last=num(r.last, 2),
                             pnl=pct(r.last / r.buy_price - 1, 1), rs=f"₹{r.qty * (r.last - r.buy_price):+,.0f}",
                             gap=('<span class="dn">past stop</span>' if r.last <= stop_px else pct(stop_px / r.last - 1, 1))
                             if pd.notna(r.last) else "–",
                             worst=pct(r.worst, 1), days=f"{(pd.Timestamp(asof) - pd.Timestamp(r.buy_date)).days}",
                             then=status(then) if then in STATUS_C else f'<span class="dim">{esc(then)}</span>',
                             now=status(now_st) if now_st in STATUS_C else '<span class="dim">off board</span>', sig=sig))
        show(table(rows, [("sym", "symbol"), ("qty", "qty"), ("buy", "avg cost"), ("last", "last"), ("pnl", "p&amp;l"), ("rs", "₹"),
                          ("gap", "to stop"), ("worst", "worst so far"), ("days", "days"), ("then", "label at buy"),
                          ("now", "label now"), ("sig", "signal")], numeric={"qty", "buy", "last", "pnl", "rs", "gap", "worst", "days"}))
        show('<div class="note">to stop = how far the price can fall before the −20% rule says sell · worst so far = the deepest '
             'it has been below your cost · label at buy = what the scan said the day you bought (same rules, that day\'s data)</div>')

    # ---- insights
    cards = trade_insights(t, o, open_value)
    if cards:
        sec("what your trades say", "measured from your own ledger · the parts a broker statement leaves out")
        show('<div class="nx grid">' + "".join(f'<div class="nxi {k}">{esc(h)}<span>{esc(d)}</span></div>' for k, h, d in cards) + "</div>")

    # ---- charts + history
    if len(t):
        sec("every closed trade", "colour = what the scan said the day you bought · hover for details")
        c1, c2 = st.columns([3, 2], gap="large")
        c1.plotly_chart(viz.trade_results(t), width="stretch", config=PLOT)
        with c2:
            show('<div class="meta">days held vs result · bubble size = money in the trade</div>')
            st.plotly_chart(viz.hold_vs_return(t, rising.STOP_PCT), width="stretch", config=PLOT)
        rows = [dict(sym=f"<b>{esc(r.symbol)}</b>", b=f"{r.entry:%d %b}", s=f"{r.exit:%d %b}", d=f"{r.days}",
                     ab=num(r.avg_buy, 2), as_=num(r.avg_sell, 2), ret=pct(r.ret, 1), pnl=f"₹{r.pnl:+,.0f}",
                     lab=status(r.label) if r.label in STATUS_C else f'<span class="dim">{esc(r.label)}</span>',
                     w=pct(r.worst, 1), a=pct(r.after, 1)) for r in t.sort_values("exit", ascending=False).itertuples()]
        show(table(rows, [("sym", "symbol"), ("b", "bought"), ("s", "sold"), ("d", "days"), ("ab", "avg buy"), ("as_", "avg sell"),
                          ("ret", "return"), ("pnl", "₹"), ("lab", "label at buy"), ("w", "worst while held"), ("a", "since sold")],
                   numeric={"d", "ab", "as_", "ret", "pnl", "w", "a"}))

    # ---- manage
    sec("trades", "everything above is computed from this ledger · sold stocks leave your positions on their own")
    a1, a2 = st.columns(2, gap="large")
    with a1.form(f"add_trade_{st.session_state.get('form_n', 0)}"):  # new key after a save = empty form; errors keep input
        show('<div class="meta">add a buy or a sell</div>')
        f1, f2 = st.columns(2)
        side = f1.segmented_control("side", ["buy", "sell"], default="buy", key="pf_side") or "buy"
        when = f2.date_input("date", value=pd.Timestamp.now().date(), max_value=pd.Timestamp.now().date())
        sym_opts = list(o.symbol) + [s for s in all_symbols() if s not in set(o.symbol)]
        sym = st.selectbox("symbol", sym_opts, index=None, placeholder="type a symbol")
        f3, f4 = st.columns(2)
        qty = f3.number_input("quantity", min_value=1, step=1, value=1)
        price = f4.number_input("price ₹", min_value=0.01, step=0.01, value=None, placeholder="fill price")
        note = st.text_input("why you're buying (optional, buys only)")
        if st.form_submit_button("save trade", width="stretch"):
            held = dict(zip(o.symbol, o.qty))
            if not sym or not price:
                st.error("pick a symbol and enter the price")
            elif side == "sell" and qty > held.get(sym, 0):
                st.error(f"you hold {held.get(sym, 0):g} {sym} — can't sell {qty}")
            else:
                pf.add_trade(side, sym, qty, price, when)
                st.session_state["form_n"] = st.session_state.get("form_n", 0) + 1
                if side == "buy" and note.strip():
                    pf.save_notes({**nts, sym: note.strip()})
                    snapshot_thesis(sym, note.strip())
                st.rerun()
    with a2:
        lg = pf.load(include_ignored=True, actions=False)
        show('<div class="meta">import your broker tradebook (Zerodha xlsx/csv, or any csv with symbol, date, side, qty, '
             'price). any date range works: just download the last few weeks — trades you already have are skipped, '
             'nothing in your ledger is deleted, and you see what changes before anything is saved.<br>'
             + (f'your ledger: <b>{len(lg)}</b> trades · {lg.date.min():%d %b %Y} – {lg.date.max():%d %b %Y}'
                if len(lg) else "your ledger is empty") + '</div>')
        if flash := st.session_state.pop("pf_flash", None):
            st.success(flash)
        ups = st.file_uploader("tradebook", type=["xlsx", "xls", "csv"], accept_multiple_files=True,
                               label_visibility="collapsed", key=f"pf_up_{st.session_state.get('up_n', 0)}")
        if ups:
            parts, bad = [], []
            for u in ups:
                try:
                    parts.append(pf.parse_tradebook(u))
                except Exception as e:
                    bad.append(f"{u.name}: {str(e)[:160]}")
            for b in bad:
                st.error(f"couldn't read {b}")
            if parts:
                plan = pf.plan_import(pd.concat(parts, ignore_index=True))
                add, rep = plan["add"], plan["replaced"]
                n_new = len(add) - len(rep)
                show(f'<div class="note">in {"these files" if len(parts) > 1 else "this file"}: <b>{plan["known"] + len(add)}</b> trades · '
                     f'<b>{plan["known"]}</b> already in your ledger (skipped) · <b class="up">{n_new}</b> new'
                     + (f' · <b class="am">{len(rep)}</b> you typed in by hand, replaced by the broker\'s exact record' if len(rep) else "")
                     + '</div>')
                if len(add):
                    ign = pf.ignored()
                    show(table([dict(d=f"{r.date:%d %b %Y}", sym=f"<b>{esc(r.symbol)}</b>"
                                     + (' <span class="dim">(kept out)</span>' if r.symbol in ign else ""),
                                     s=f'<span class="{"up" if r.side == "buy" else "dn"}">{r.side}</span>',
                                     q=f"{r.qty:g}", p=num(r.price, 2), v=f"₹{r.qty * r.price:,.0f}")
                                for r in add.sort_values(["date", "time"]).itertuples()],
                               [("d", "date"), ("sym", "symbol"), ("s", "side"), ("q", "qty"), ("p", "price"), ("v", "value")],
                               numeric={"q", "p", "v"}))
                if len(plan["oversold"]):
                    o_ = plan["oversold"].groupby("symbol").qty.sum()
                    st.warning("these sells have no matching buy in your ledger, so their profit can't be worked out: "
                               + ", ".join(f"{s} ({q:g} shares)" for s, q in o_.items())
                               + " — import an older tradebook that covers when you bought them")
                if len(add) and st.button(f"add {len(add)} trade{'s' if len(add) != 1 else ''} to my ledger",
                                          type="primary", width="stretch"):
                    pf.save(plan["ledger"])
                    st.session_state["pf_flash"] = (f"added {n_new} new trade{'s' if n_new != 1 else ''}"
                                                    + (f", replaced {len(rep)} hand-entered" if len(rep) else ""))
                    st.session_state["up_n"] = st.session_state.get("up_n", 0) + 1  # clears the file picker
                    st.rerun()
                elif not len(add):
                    st.info("nothing new — every trade in this file is already in your ledger")
        ign = st.text_input("keep out of the portfolio (symbols)", " ".join(sorted(pf.ignored())),
                            help="e.g. a stock bought only for an IPO shareholder quota")
        if " ".join(sorted(ign.upper().split())) != " ".join(sorted(pf.ignored())):
            pf.set_ignored(ign.split())
            st.rerun()
    if len(o):
        with st.expander("why I bought — notes for open positions (checked every quarter on the today tab)"):
            ed = st.data_editor(pd.DataFrame({"symbol": list(o.symbol), "why I bought": [nts.get(s, "") for s in o.symbol]}),
                                disabled=["symbol"], hide_index=True, width="stretch", key="pf_notes")
            if st.button("save notes"):
                new = {**nts, **{r["symbol"]: str(r["why I bought"] or "").strip() for _, r in ed.iterrows()}}
                pf.save_notes(new)
                for s_, th in new.items():
                    if th and s_ in set(o.symbol):
                        snapshot_thesis(s_, th)
                st.rerun()
    with st.expander(f"ledger — {len(led)} trades (fix or delete a row here)"):
        full = pf.load(include_ignored=True, actions=False)
        ed = st.data_editor(full.assign(date=full.date.dt.date), num_rows="dynamic", hide_index=True, width="stretch", key="pf_ledger",
                            column_config={"side": st.column_config.SelectboxColumn("side", options=["buy", "sell"]),
                                           "date": st.column_config.DateColumn("date")})
        if st.button("save ledger"):
            pf.save(ed.dropna(subset=["symbol", "qty", "price"]).assign(symbol=lambda d: d.symbol.str.upper().str.strip()))
            st.rerun()


if in_pf:
    portfolio_page()
    st.stop()

t_today, t_board, t_bo, t_lt, t_stock, t_cmp, t_sec, t_fil, t_watch, t_hist = st.tabs(
    ["today", "board", "breakouts", "long-term", "stock", "compare", "themes", "filings", "watchlist", "scoreboard"])

# ---------------------------------------------------------------- today
with t_today:
    rows, exits, invested, value = [], [], 0.0, 0.0
    for p in pos.itertuples():
        px_ = prices(p.symbol)
        if px_.empty:
            continue
        last = float(px_.close.iloc[-1])
        stt = bs.status.get(p.symbol, "–")
        alerts = rising.position_alerts(p.buy_price, p.buy_date, last, asof, stt, latest_q(p.symbol))
        sig = '<span class="dim">hold</span>'
        if alerts:
            lvl, msg = alerts[0]
            sig = f'<span class="{"dn" if lvl == "EXIT" else "am"}">{lvl.lower()}</span> <span class="dim">{esc(msg)}</span>'
            exits += [p.symbol] if lvl == "EXIT" else []
        invested += p.buy_price * p.qty
        value += last * p.qty
        rows.append(dict(sym=f"<b>{esc(p.symbol)}</b>", qty=p.qty, buy=num(p.buy_price, 2), last=num(last, 2),
                         pnl=pct(last / p.buy_price - 1, 1), stop=num(p.buy_price * (1 - rising.STOP_PCT), 2),
                         review=f'{pd.Timestamp(p.buy_date) + pd.Timedelta(days=rising.REVIEW_DAYS):%d %b %y}',
                         st=status(stt) if stt != "–" else '<span class="dim">off board</span>', sig=sig))
    sec("open positions", f"value ₹{value:,.0f} · invested ₹{invested:,.0f} · {pct(value / invested - 1, 1) if invested else ''}")
    if exits:
        show(f'<div class="flag">exit rule hit: <b>{", ".join(exits)}</b> — sell next session</div>')
    show(table(rows, [("sym", "symbol"), ("qty", "qty"), ("buy", "buy"), ("last", "last"), ("pnl", "p&amp;l"),
                      ("stop", "stop −20%"), ("review", "6m review"), ("st", "status"), ("sig", "signal")],
               numeric={"qty", "buy", "last", "pnl", "stop"}))
    show('<div class="note">open trades only · add, sell or import trades and see your full record in <b>portfolio</b> (top right)</div>')

    # thesis health: the growth check when you bought vs now
    rows_t = []
    for r_ in pos.itertuples():
        p_ = THESIS / f"{r_.symbol}.json"
        if not r_.thesis.strip() or not p_.exists():
            continue
        snap = read_json(p_)
        if not snap:
            continue
        f_ = load_fundamentals(r_.symbol)
        now_c = {t_: v_ for v_, t_, _ in fundamentals.checklist(f_)} if f_ else {}
        dot = lambda v_: f'<span class="{MARK.get(v_, ("·", "dim"))[1]}">●</span>'
        worse = [t_ for t_, v_ in snap["checks"].items() if v_ == "pass" and now_c.get(t_) in ("fail", "watch")]
        rows_t.append(dict(sym=f"<b>{esc(r_.symbol)}</b>", th=f'<span class="dim">{esc(r_.thesis)}</span>',
                           then="".join(dot(v_) for v_ in snap["checks"].values()),
                           now="".join(dot(now_c.get(t_, "n/a")) for t_ in snap["checks"]),
                           v=('<span class="dn">weakened: ' + esc(", ".join(worse)) + "</span>") if worse else '<span class="up">intact</span>'))
    if rows_t:
        sec("thesis health", "growth check when you bought vs now · each dot is one check (green pass · amber watch · red fail)")
        show(table(rows_t, [("sym", "symbol"), ("th", "why you bought"), ("then", "then"), ("now", "now"), ("v", "verdict")]))
    elif len(pos):
        show('<div class="note">tip: add "why I bought" in <b>portfolio</b> — the app will tell you each quarter whether that reason still holds</div>')
    for s in pos.symbol:
        c = brief.latest_cached(s)
        if c and (pd.Timestamp.now().date() - c[0]).days <= 7:
            with st.expander(f"new brief — {s} — written {c[0]:%d %b}"):
                st.markdown(c[1])

    al_path = DATA / "alerts.json"
    al = read_json(al_path)
    if al:
        sec("news & red flags", f"holdings + watchlist · last {al['days']} days · checked {al['checked']} · no AI")
        rows = [dict(sym=f"<b>{esc(s)}</b>" + (' <span class="am">holding</span>' if s in set(pos.symbol) else ""),
                     d=esc(i["date"]), k=f'<span class="{"dn" if i["kind"] == "news" else "am"}">{i["kind"]}</span>',
                     _wrap=f'<a href="{i["url"]}" target="_blank">{esc(i["text"][:150])}</a>')
                for s, items in sorted(al["items"].items(), key=lambda kv: kv[0] not in set(pos.symbol)) for i in items]
        show(table(rows, [("sym", "symbol"), ("d", "date"), ("k", "type"), ("_wrap", "what")]) if rows
             else '<div class="empty">nothing worrying found</div>')

    up = b[b.status.isin(["LEADER", "EMERGING"]) & ~b.industry.isin(excl)]
    changes = [("new on board", up[up.new.astype(bool) & (up.prev_status != "WAIT")]),
               ("wait → triggered", up[up.prev_status == "WAIT"]),
               ("dropped off", b[(b.status == "BROKE") & b.prev_status.isin(["LEADER", "EMERGING"])])]
    sec("changes since last scan", "excluded sectors hidden")
    cols = st.columns(3)
    for col, (title, d) in zip(cols, changes):
        col.markdown(f'<div class="meta">{title} · {len(d)}</div>', unsafe_allow_html=True)
        col.markdown(table([dict(sym=f"<b>{esc(r.symbol)}</b>", st=status(r.status), s=pct(r.sales_yoy), p=pct(r.pat_yoy))
                            for r in d.head(12).itertuples()],
                           [("sym", "symbol"), ("st", "status"), ("s", "sales"), ("p", "profit")], numeric={"s", "p"}),
                     unsafe_allow_html=True)

    cal_path = DATA / "results_calendar.csv"
    if cal_path.exists():
        cal = pd.read_csv(cal_path, parse_dates=["date"])
        mine = set(watch) | set(pos.symbol) | set(up.symbol)
        cal = cal[cal.symbol.isin(mine) & (cal.date <= asof + pd.Timedelta(days=12))].sort_values("date")
        why = lambda s: "holding" if s in set(pos.symbol) else "watchlist" if s in watch else bs.status.get(s, "")
        sec("results coming up", "don't buy into a result — wait for the reaction")
        show(table([dict(d=f"{r.date:%a %d %b}", sym=f"<b>{esc(r.symbol)}</b>",
                         w=status(why(r.symbol)) if why(r.symbol) in STATUS_C else f'<span class="am">{why(r.symbol)}</span>')
                    for r in cal.itertuples()], [("d", "date"), ("sym", "symbol"), ("w", "why")]))

    near = b[b.symbol.isin(watch) & (b.status == "WAIT")].assign(gap=lambda d: d.trigger / d.close - 1)
    near = near[near.gap < .05].sort_values("gap")
    sec("watchlist near trigger", "within 5%")
    show(table([dict(sym=f"<b>{esc(r.symbol)}</b>", c=num(r.close), t=num(r.trigger), g=pct(r.gap, 1), s=pct(r.sales_yoy), p=pct(r.pat_yoy))
                for r in near.itertuples()],
               [("sym", "symbol"), ("c", "close"), ("t", "trigger"), ("g", "to go"), ("s", "sales"), ("p", "profit")],
               numeric={"c", "t", "g", "s", "p"}))

# ---------------------------------------------------------------- board
with t_board:
    fn_p = DATA / "funnel.json"
    fn = read_json(fn_p)
    if fn:
        R = rising.RULES
        with st.expander(f"how {fn['listed']:,} listed companies become this list — the full method", expanded=False):
            c1, c2 = st.columns([3, 2], gap="large")
            c1.plotly_chart(viz.universe_funnel(fn), width="stretch", config=PLOT)
            with c2:
                show(table([
                    dict(g="1 growth", _wrap=f"{R['streak']}+ of last 3 quarters with sales and profit up &gt;15% · latest sales "
                                         f"&gt;{R['sales_yoy']:.0%} and profit &gt;{R['pat_yoy']:.0%} vs a year ago · sales up "
                                         f"quarter-on-quarter in {R['seq_ups']}+ of last 3"),
                    dict(g="2 acceleration", _wrap="sales growth faster than 2 quarters ago, or operating margin up vs a year ago"),
                    dict(g="3 trend", _wrap=f"price above its 200-day average and within {1 - R['near_high']:.0%} of its 52-week high"),
                    dict(g="4 run-up", _wrap=f"up &gt;{R['run_up']:.0%} in a year → <span style='color:{C['amber']}'>leader</span>, "
                                       f"otherwise <span class='up'>emerging</span> · fails trend but still above "
                                       f"{R['wait_floor']:.0%} of its high → <span style='color:{C['blue']}'>wait</span>"),
                    dict(g="order", _wrap="within a label: rank of sales growth + profit growth + ½ margin change + ½ 3-month momentum "
                                      "− 1 for one-off quarters")],
                    [("g", "gate"), ("_wrap", "rule")]))
                show('<div class="note">prices adjusted for splits and bad prints · results are public ~45–60 days after the quarter · '
                     'thresholds were chosen by judgment and checked on a 2-year backtest (see <b>scoreboard</b>) — '
                     'transparent, not magic. open any stock to see every test applied to it.</div>')
    f1, f2, f3, f4 = st.columns([2, 3, 1, 1])
    statuses = f1.multiselect("status", STATUSES, ["LEADER", "EMERGING"],
                              help="wait had no edge in the backtest (median −10%) — a watch list, not a buy list")
    inds = sorted(b.industry.dropna().unique())
    f2.multiselect("exclude industries", inds, [i for i in DEFAULT_EXCLUDE if i in inds], key="excl",
                   help="war / oil / IT-sensitive sectors excluded by default")
    max_px = f3.number_input("max price ₹", 0, 100000, 0, 100, help="0 = no limit; ~2,500 suits a ₹10k account")
    hide_lumpy = f4.checkbox("hide lumpy", True, help="one-off / order-driven quarters")
    excl = st.session_state.get("excl", excl)
    v = b[b.status.isin(statuses) & ~b.industry.isin(excl) & ((b.market_cap_cr.fillna(0) >= 300) | (b.status == "BROKE"))]
    if max_px:
        v = v[v.close <= max_px]
    if hide_lumpy and "lumpy" in v:
        v = v[~v.lumpy.fillna(False).astype(bool)]
    try:
        surv = insights.surveillance()
    except Exception:
        surv = {}
    n_surv = int(v.symbol.isin(surv).sum())
    sec("board", f"{len(v)} of {len(b)} · sorted by status, then steadiness"
        + (f" · {n_surv} on NSE's surveillance list (⚠ = extra risk: price limits, operator activity)" if n_surv else ""))
    show(table([dict(st=status(r.status) + (' <span class="am">new</span>' if r.new and r.status != "BROKE" else ""),
                     sym=f"<b>{esc(r.symbol)}</b>" + (' <span class="dn" title="on NSE surveillance list">⚠ watch-list</span>'
                                                      if r.symbol in surv else ""), ind=f'<span class="dim">{esc(r.industry) if pd.notna(r.industry) else "–"}</span>',
                     c=num(r.close), d=pct(r.chg1d, 1), q=f'<span class="dim">{pd.Timestamp(r.qtr):%b %y}</span>' if pd.notna(r.qtr) else "",
                     s=pct(r.sales_yoy), p=pct(r.pat_yoy), cap="yes" if r.capex is True or r.capex == "True" else '<span class="dim">–</span>',
                     pe=num(r.pe, 0), y=pct(r.ret1y), t=num(r.trigger), m=num(r.market_cap_cr, 0),
                     sc=num(r.score, 0) if pd.notna(getattr(r, "score", None)) else "–")
                for r in v.itertuples()],
               [("st", "status"), ("sym", "symbol"), ("ind", "industry"), ("c", "close"), ("d", "1d"), ("q", "qtr"),
                ("s", "sales yoy"), ("p", "profit yoy"), ("cap", "capex"), ("pe", "p/e"), ("y", "1y"), ("t", "trigger"), ("m", "mcap cr"), ("sc", "steady")],
               numeric={"c", "d", "s", "p", "pe", "y", "t", "m", "sc"}))

# ---------------------------------------------------------------- breakouts
@st.cache_data(ttl=300)
def breakout_scan():
    p = DATA / "breakouts.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=300)
def breakout_daily(sym):
    d = breakouts.daily([sym]).get(sym)
    return d.set_index("date") if d is not None else pd.DataFrame()


GRADE_TXT = {"A": ("up", "grade A — box breakout + a growing company: the only setup that beat the typical stock in 20 years "
                         "(86% made money, 9% hit the stop; 22 cases, a small sample)"),
             "B": ("am", "grade B — held up better than most (a box, or a base tested 4+ times) but still trailed the typical stock"),
             "C": ("dn", "grade C — this kind of breakout lost to simply holding the typical stock in the 20-year test")}
TF = {"base": "monthly", "trendline": "weekly", "swing": "daily", "box": "weekly"}


def breakout_card(r):
    ev = breakouts.evidence()["patterns"].get(r["pattern"], {})
    cls, gtxt = GRADE_TXT[r["grade"]]
    rec = (f'past {r["pattern"]} breakouts: <b>{ev["events"]:,}</b> · median {pct(ev["median"], 1)} · {ev["won"]:.0%} made money · '
           f'{ev["stopped"]:.0%} hit the stop · vs the typical stock {pct(ev["vs_market"], 1)}') if ev else ""
    gl = r.get("growth_label")
    show(f'<div class="note" style="border-left:2px solid {C["amber"]};padding:.6rem .9rem;background:#12130e;color:{C["text"]};font-size:.8rem">'
         f'<b>{esc(breakouts.SETUP[r["setup"]])}</b> · <span class="{cls}">{gtxt}</span><br>'
         f'close {num(r["close"], 2)} · trigger: close above <b>{num(r["trigger"], 2)}</b> '
         f'({f"already {-r["gap"]:.1%} above" if r["gap"] < 0 else f"{r["gap"]:.1%} to go"}) · '
         f'stop {num(r["stop"], 2)} on a closing basis (risk {r["risk"]:.0%})<br>'
         f'targets {num(r["target1"], 2)} / {num(r["target2"], 2)} (typical / good outcome of past breakouts of this kind) · '
         f'reward÷risk {num(r["reward_risk"], 1)}'
         f'{" · growth: <b>" + esc(str(gl).lower()) + "</b>" if isinstance(gl, str) else ""}<br>'
         f'<span class="dim">{rec}</span></div>')


with t_bo:
    bo = breakout_scan()
    if bo.empty:
        show('<div class="note">no breakout scan yet — click <b>update data</b> (or run <b>python breakouts.py</b>)</div>')
    else:
        show('<div class="note">chart breakouts on every liquid stock, graded by how that kind of setup did across 112,311 past '
             'breakouts (2006–2026, breakout lab). on their own, breakouts trailed the typical stock; a box breakout by a growing '
             'company was the exception. research tool, not advice.</div>')
        f1, f2, f3 = st.columns([1, 3, 1])
        grades = f1.multiselect("grade", ["A", "B", "C"], default=["A", "B"])
        setups = f2.multiselect("setup", list(breakouts.SETUP), default=["box_break", "box_near", "base_near", "base_retest"],
                                format_func=lambda s: breakouts.SETUP[s])
        fresh = f3.checkbox("my watchlist + holdings only", False)
        v = bo[bo.grade.isin(grades) & bo.setup.isin(setups)]
        if fresh:
            v = v[v.symbol.isin(set(watch) | set(pos.symbol))]
        c1, c2 = st.columns([2, 3])
        pick = c1.selectbox("chart a setup", list(v.symbol.unique()), index=None, placeholder="pick a symbol from the list")
        if pick:
            rows = v[v.symbol == pick]
            ch = c2.selectbox("setup", list(rows.setup), format_func=lambda s: breakouts.SETUP[s], key=f"bo_set_{pick}")
            r = rows[rows.setup == ch].iloc[0].to_dict()
            breakout_card(r)
            d = breakout_daily(pick)
            if len(d):
                st.plotly_chart(viz.breakout_chart(d, TF[r["pattern"]], r), width="stretch", config=PLOT)

        n_show = len(v) if st.toggle(f"show all {len(v)} setups", False) else min(30, len(v))
        sec("live setups", f"{len(v)} of {len(bo)} · gap = how far the price must rise to the trigger, or how far above it already is")
        gcls = {"A": "up", "B": "am", "C": "dn"}
        show(table([dict(g=f'<span class="{gcls[r.grade]}">{r.grade}</span>', sym=f"<b>{esc(r.symbol)}</b>",
                         s=esc(breakouts.SETUP[r.setup]), c=num(r.close, 2), t=num(r.trigger, 2),
                         gap=(f'<span class="dim">{-r.gap:.1%} above</span>' if r.gap < 0 else f"{r.gap:.1%} to go"), stp=num(r.stop, 2),
                         rk=f"{r.risk:.0%}", t1=num(r.target1, 2), rr=num(r.reward_risk, 1), vol=num(r.vol_ratio, 1),
                         gl=status(r.growth_label) if isinstance(r.growth_label, str) else '<span class="dim">–</span>')
                    for r in v.head(n_show).itertuples()],
                   [("g", "grade"), ("sym", "symbol"), ("s", "setup"), ("c", "close"), ("t", "trigger"), ("gap", "gap"), ("stp", "stop"),
                    ("rk", "risk"), ("t1", "target 1"), ("rr", "r÷r"), ("vol", "vol ×"), ("gl", "growth")],
                   numeric={"c", "t", "gap", "stp", "rk", "t1", "rr", "vol"}))
        sec("check a tip", "paste a stock tip from a group or a newsletter")
        txt = st.text_area("tip", height=140, label_visibility="collapsed",
                           placeholder="*RAYMOND - CMP 1130*\n*SUPPORT - 1060 (DCB)*\n*UPSIDE - 1185/1230+*")
        if txt.strip():
            names = dict(q("select symbol, company_name from instruments").values.tolist())
            tp = tips.parse(txt, set(names), names)
            if not tp["symbol"]:
                st.warning("couldn't find a stock in that text — include its NSE symbol (e.g. RAYMOND)")
            else:
                sym = tp["symbol"]
                d = breakout_daily(sym)
                last = float(d.close.iloc[-1]) if len(d) else np.nan
                ref = tp["cmp"] or last
                show('<div class="kv">' + "".join(f"<div><span>{k}</span>{v_}</div>" for k, v_ in [
                    ("stock", f"<b>{esc(sym)}</b>"), ("tip CMP", num(tp["cmp"], 2)), ("close now", num(last, 2)),
                    ("buy range", f'{num(tp["buy_low"], 2)}–{num(tp["buy_high"], 2)}' if tp["buy_low"] else "–"),
                    ("tip stop", f'{num(tp["stop"], 2)} {pct(tp["stop"] / ref - 1)}' if tp["stop"] else "–"),
                    ("tip targets", " / ".join(f"{num(x, 2)} {pct(x / ref - 1)}" for x in tp["targets"]) or "–")]) + "</div>")
                mine = bo[bo.symbol == sym]
                if len(mine):
                    r = mine.iloc[int(mine.gap.abs().to_numpy().argmin())].to_dict()  # the setup whose level is nearest the price
                    breakout_card(r)
                    if tp["targets"]:
                        show('<div class="note">how often similar setups got there before the stop or the time limit: ' + " · ".join(
                            f"{num(x, 2)} ({pct(x / ref - 1)}): <b>{breakouts.odds(r['pattern'], x / ref - 1):.0%}</b>"
                            for x in tp["targets"]) + f" of past {r['pattern']} breakouts</div>")
                    fig = viz.breakout_chart(d, TF[r["pattern"]], r, tp) if len(d) else None
                else:
                    show('<div class="note">no chart setup on this stock right now (no box, base or falling line near the price) — '
                         'the tip may rest on something else: results, news or the sector</div>')
                    fig = viz.breakout_chart(d, "daily", None, tp) if len(d) else None
                if fig is not None:
                    st.plotly_chart(fig, width="stretch", config=PLOT)

# ---------------------------------------------------------------- long-term
@st.cache_data(ttl=300)
def longterm_scores():
    return longterm.load()


@st.cache_data(ttl=3600)
def longterm_check(sym):
    r = longterm.check(sym, longterm_scores())
    return None if r is None else r.to_dict()


def lt_p(x, signed=False):
    return '<span class="dim">–</span>' if x is None or pd.isna(x) else (f"{x:+.0%}" if signed else f"{x:.0%}")


def lt_vs(x, good_if_up=True):
    """'+8%' coloured green when it's the good direction."""
    if x is None or pd.isna(x):
        return ""
    if abs(x) < .005:
        return '<span class="dim">±0%</span>'
    c = "" if abs(x) < .015 else ("up" if (x > 0) == good_if_up else "dn")
    return f'<span class="{c}">{x:+.0%}</span>'


def lt_ps(rank):
    """Where a stock's price-to-sales sits among today's stocks: 'cheap · 19 of 100' (1 = cheapest)."""
    if rank is None or pd.isna(rank):
        return '<span class="dim">–</span>'
    n = max(1, round(rank * 100))  # the word follows the number shown, so "70 of 100" always reads the same
    word = "cheap" if n <= 40 else "middle" if n <= 70 else "expensive"
    c = {"cheap": "up", "middle": "", "expensive": "dn"}[word]
    return f'<span class="{c}">{word}</span> <span class="dim">· {n} of 100</span>'


def lt_gname(g):
    return g.replace("4 of 6 core checks", "4+ of 6 good-company checks")


def longterm_tab():
    """The long-term tab (a function, so its names can't clash with the other tabs')."""
    LE = longterm.evidence()["5"]
    LG = LE["groups"]
    lt_all, lt_cheap = LG["every stock"], LG["cheap for its sales (cheapest 40% by P/S)"]
    lt_q, lt_qc = LG["4 of 6 core checks (quality alone)"], LG["cheap for its sales + 4 of 6 core checks"]
    show(f'<div class="note">for money you will leave alone for 4–5 years. {LE["stocks"]:,} NSE companies were judged only on what '
         f'was public on 30 Sep 2017, 2018, 2019, 2020 and 2021, then followed for 5 years (long-term lab). '
         f'research tool, not advice.</div>')
    lv = st.segmented_control("view", ["list", "check a stock", "my long-term stocks", "what the test found"],
                              default="list", key="lt_view", label_visibility="collapsed") or "list"

    if lv == "list":
        LS = longterm_scores()
        if LS.empty:
            show('<div class="note">no long-term scores yet: run <b>python longterm.py fetch</b> once (about an hour), '
                 'then each <b>update data</b> keeps them current.</div>')
            return
        T, R2 = longterm.TIERS, longterm.evidence()["round2"]
        f1, f2, f3, f4 = st.columns([3, 1, 1, 1])
        pick_t = f1.multiselect("groups", [t[0] for t in T], default=[t[0] for t in T[:2]], key="lt_tiers2")
        max_px = f2.number_input("max price ₹", 0, 100000, 0, 100, key="lt_maxpx", help="0 = no limit")
        clean = f3.checkbox("hide stocks with warnings", True, key="lt_clean",
                            help="value traps and red flags (see the warnings column): in the test they lost money more often")
        liquid = f4.checkbox("only stocks like the ones tested", True, key="lt_liquid",
                             help="₹25 lakh+ traded a day, and no banks or lenders: the odds were measured on these")
        d = LS[LS.studied] if liquid else LS
        d = d.merge(q("select symbol, company_name, sector from instruments"), on="symbol", how="left")
        sec("the groups, best past odds first", "each stock sits in the best group it qualifies for · ± = vs every stock "
                                                 "bought the same day · ×5 = equal money in 5 such stocks")
        rows = []
        for i, (name, meaning, ev, _) in enumerate(T, 1):
            g = R2["tiers"][ev]
            b = g.get("basket5") or {}
            n_all, n_clean = int((d.tier == i).sum()), int(((d.tier == i) & (d.warnings == "")).sum())
            rows.append(dict(t=f"<b>{i}. {esc(name)}</b>", _wrap=esc(meaning), n=f"{n_clean:,} <span class='dim'>/ {n_all:,}</span>",
                             dd=f'{lt_p(g["doubled"])} {lt_vs(g["doubled_vs"])}', l=f'{lt_p(g["lost"])} {lt_vs(g["lost_vs"], False)}',
                             bd=lt_p(b.get("doubled")), bl=lt_p(b.get("lost"))))
        show(table(rows, [("t", "group"), ("_wrap", "what it means"), ("n", "clean / all now"), ("dd", "doubled"),
                          ("l", "lost money"), ("bd", "×5 doubled"), ("bl", "×5 lost")], numeric={"n", "dd", "l", "bd", "bl"}))
        v = d[d.tier.isin([i for i, t in enumerate(T, 1) if t[0] in pick_t])]
        if clean:
            v = v[v.warnings == ""]
        if max_px:
            v = v[v.price <= max_px]
        v = v.sort_values(["tier", "n_signals", "ps_rank"], ascending=[True, False, True])
        sec("stocks", f"{len(v):,} · within a group: most improving signs first, then cheapest for its sales · "
                      "open any name in <b>check a stock</b>")
        n_show = len(v) if st.toggle(f"show all {len(v):,}", False, key="lt_all") else min(30, len(v))
        show(table([dict(t=esc(T[int(x.tier) - 1][0]), s=f"<b>{esc(x.symbol)}</b>", co=esc(clip(x.company_name, 32)),
                         sec_=esc(clip(x.sector, 18)) if isinstance(x.sector, str) else "–", p=num(x.price, 2), ps=lt_ps(x.ps_rank),
                         f=f"{x.fscore:.0f}/9" if pd.notna(x.fscore) else "–", sg=f"{int(x.n_signals)}/5",
                         e=f"{x.pe:.0f}" if pd.notna(x.pe) else '<span class="dn">loss</span>', y=pct(x.past_1y),
                         _wrap=f'<span class="dn">{esc(x.warnings)}</span>' if x.warnings else '<span class="dim">–</span>')
                    for x in v.head(n_show).itertuples()],
                   [("t", "group"), ("s", "symbol"), ("co", "company"), ("sec_", "sector"), ("p", "price"), ("ps", "price for sales"),
                    ("f", "F-score"), ("sg", "improving"), ("e", "P/E"), ("y", "1 year"), ("_wrap", "warnings")],
                   numeric={"p", "ps", "f", "sg", "e", "y"}))
        show('<div class="note"><b>F-score</b> = 9 yes/no checks that the business got healthier over the last year (profit, cash, '
             'debt, margins, no new shares…). <b>improving</b> = how many of 5 signs: growth speeding up, profit up 15%+, price above '
             'its 200-day average, a strong 12-month trend, cheaper than its usual P/E. <b>warnings</b> = weak F-score, can barely pay '
             'interest, falling knife, price crashed 50%+, many new shares, tiny or penny, loss-making — plus, live only, NSE\'s '
             'watch lists and serious filings (insolvency, forensic audit, SEBI orders…). this is a list of groups with better past '
             'odds, not a buy list: spread money over 5+ stocks, and read what each company does. research tool, not advice.</div>')

    elif lv == "what the test found":
        show('<div class="kv">' + "".join(f"<div><span>{a}</span>{b}</div>" for a, b in [
            ("one stock: doubled in 5 yrs", f"<b>{lt_p(lt_all['doubled'])}</b>"),
            ("one stock: lost money", f'<b class="dn">{lt_p(lt_all["lost"])}</b>'),
            ("5 stocks, equal money: doubled", f'<b>{lt_p(lt_all["basket5"]["doubled"])}</b>'),
            ("5 stocks: lost money", f'<b class="up">{lt_p(lt_all["basket5"]["lost"])}</b>'),
            ("5 cheap-for-sales stocks: doubled", f'<b class="up">{lt_p(lt_cheap["basket5"]["doubled"])}</b>')]) + "</div>")
        show(f'<div class="note" style="border-left:2px solid {C["amber"]};padding:.6rem .9rem;background:#12130e;'
             f'color:{C["text"]};font-size:.8rem;line-height:1.7">'
             '<b>1. spreading your money mattered more than picking.</b> one stock on its own lost money about 1 time in 5; '
             'five stocks with equal money about 1 time in 25 — and still doubled more often, because one big winner carries the rest.<br>'
             '<b>2. the price you pay mattered most.</b> stocks cheap for their sales (cheapest 40% by price-to-sales that day) '
             'doubled more often than other stocks bought the same day, in <b>every one</b> of the 5 buy years.<br>'
             '<b>3. "good company" checks alone did not raise the odds of doubling.</b> steady sales, high return on capital and '
             'low debt were usually already expensive. they did make a 50% fall a little less likely.<br>'
             f'<b>4. loss-making companies</b> lost money far more often ({lt_p(LG["loss-making when bought"]["lost"])} vs '
             f'{lt_p(lt_all["lost"])}).</div>')
        R2 = longterm.evidence()["round2"]
        sec("round 2: ₹1,000 a month, Oct 2017 – Sep 2022, held to today",
            "each month's ₹1,000 split over 5 random stocks from the group · 500 simulated investors")
        show(table([dict(g=esc(k), t=f"₹{v['typical']:,.0f}", y=f"{v['xirr_typical']:+.1%}", w=f"₹{v['worst10']:,.0f}",
                         b=f"₹{v['best10']:,.0f}", n=f"~{v['stocks_per_month']:.0f}")
                    for k, v in R2["sip"].items()],
                   [("g", "group"), ("t", "typical value of ₹60,000"), ("y", "a year"), ("w", "unlucky 1 in 10"),
                    ("b", "lucky 1 in 10"), ("n", "stocks to pick from")], numeric={"t", "y", "w", "b", "n"}))
        show('<div class="note">every group beat buying at random. the healthy-cheap groups made each <i>single</i> pick safer '
             '(fewer losses and 50% crashes, in the training years and the hidden 2021–22 years), but over 2017–22, when even troubled '
             'cheap stocks rallied, plain "cheap" still ended slightly ahead for a monthly buyer.</div>')
        sec("warnings: what happened to stocks that carried them", "vs every stock bought the same day")
        show(table([dict(w=esc(k), n=f'{v["n"]:,}', l=lt_vs(v["lost_vs"], False), c=lt_vs(v["crash_vs"], False), d=lt_vs(v["doubled_vs"]))
                    for k, v in R2["warnings"].items()],
                   [("w", "warning"), ("n", "cases"), ("l", "chance of losing money"), ("c", "chance of a 50% fall"),
                    ("d", "chance of doubling")], numeric={"n", "l", "c", "d"}))
        show('<div class="note">warnings mostly raise the chance of losing; a few of these stocks became big winners, which is why '
             'they are shown, not silently removed. NSE watch lists and serious filings are live warnings with no history to test.</div>')
        sec("doubled in 5 years, by the year you bought", "each bar: the share of that group's stocks that doubled")
        yrs = list(lt_all["by_year"])
        fig = go.Figure()
        for name, g, col in [("every stock", lt_all, C["muted"]), ("good-company checks only", lt_q, C["blue"]),
                             ("cheap for its sales", lt_cheap, C["amber"]), ("cheap + good-company checks", lt_qc, C["green"])]:
            v = [g["by_year"].get(y, {}).get("doubled") for y in yrs]
            fig.add_bar(name=name, x=[f"Sep {y}" for y in yrs], y=v, marker_color=col, text=[f"{x:.0%}" for x in v],
                        textposition="outside", hovertemplate="%{x}: %{y:.0%} doubled<extra>" + name + "</extra>")
        viz.chart_layout(fig, 320)
        fig.update_layout(barmode="group", hovermode="closest", yaxis=dict(tickformat=".0%", range=[0, 1.08]))
        st.plotly_chart(fig, width="stretch", config=PLOT)
        by = lt_all["by_year"]
        show(f'<div class="note">the year you buy changes everything: from Sep 2017 {lt_p(by["2017"]["doubled"])} of stocks doubled, '
             f'from Sep 2019 {lt_p(by["2019"]["doubled"])}. that is why every comparison below is against stocks bought the '
             f'<b>same day</b>.</div>')

        sec("groups", "± = vs every stock bought the same day · ×5 = equal money in 5 random picks from the group · "
                      "better in = buy years it doubled more often")
        rows = []
        for name, g in LG.items():
            b = g.get("basket5") or {}
            rows.append(dict(g=esc(lt_gname(name)), n=f'{g["n"]:,}', d=f'{lt_p(g["doubled"])} {lt_vs(g["doubled_vs"])}',
                             l=f'{lt_p(g["lost"])} {lt_vs(g["lost_vs"], False)}', c=f'{lt_p(g["crash"])} {lt_vs(g["crash_vs"], False)}',
                             y=g.get("years_better", "") if name != "every stock" else "", bd=lt_p(b.get("doubled")), bl=lt_p(b.get("lost"))))
        show(table(rows, [("g", "group"), ("n", "cases"), ("d", "doubled"), ("l", "lost money"), ("c", "fell 50%+"),
                          ("y", "better in"), ("bd", "×5 doubled"), ("bl", "×5 lost")],
                   numeric={"n", "d", "l", "c", "y", "bd", "bl"}))
        sec("each check on its own", "written down before looking at any result · change in the chance of each outcome for "
                                     "stocks that passed, vs every stock bought the same day")
        rows = [dict(c=esc(n), k=f'{c["passed"]["n"]:,}', d=lt_vs(c["passed"]["doubled_vs"]), l=lt_vs(c["passed"]["lost_vs"], False),
                     f=lt_vs(c["passed"]["crash_vs"], False), y=c["passed"].get("years_better", ""))
                for n, c in LE["checks"].items()]
        show(table(rows, [("c", "check"), ("k", "passed"), ("d", "doubling"), ("l", "losing money"),
                          ("f", "a 50% fall"), ("y", "better in")], numeric={"k", "d", "l", "f", "y"}))
        show('<div class="note"><b>be careful with these numbers.</b> only companies still listed today are in the data — ones that '
             'went bust are missing, and cheap companies go bust more often, so the "cheap" result is flattered. 2017–2026 favoured '
             'cheap, old-economy and smaller companies; in the 2010s expensive "quality" companies led, and 12 years of results '
             "can't test that era. prices only: dividends are not counted. the combined groups were formed after looking at the "
             'single checks, so treat them as a hint.</div>')

    elif lv == "check a stock":
        LS = longterm_scores()
        opts = sorted(set(LS.symbol) | set(all_symbols())) if len(LS) else all_symbols()
        pick = st.selectbox("stock", opts, index=None, placeholder="type a symbol, e.g. TITAN", key="lt_pick")
        if pick:
            with st.spinner("reading its yearly results…"):
                r = longterm_check(pick)
            if r is None:
                st.warning("couldn't score it: no Screener page, or under ~10 months of prices")
            else:
                T = longterm.TIERS
                tname = T[int(r["tier"]) - 1][0] if int(r["tier"]) <= len(T) else "none of the tested groups"
                show('<div class="kv">' + "".join(f"<div><span>{a}</span>{b}</div>" for a, b in [
                    ("group", f"<b>{esc(tname)}</b>"),
                    ("F-score (getting healthier)", f"<b>{r['fscore']:.0f} of 9</b>" if pd.notna(r["fscore"]) else "–"),
                    ("good-company checks", f"<b>{int(r['core'])} of 6</b>"),
                    ("price for its sales (1 = cheapest of 100)", f"<b>{lt_ps(r['ps_rank'])}</b>"),
                    ("profit (latest year)", f'<b class="up">profitable</b> <span class="dim">· P/E {r["pe"]:.0f}</span>'
                     if pd.notna(r["pe"]) else '<b class="dn">loss-making</b>'),
                    ("last 1 year", f"<b>{pct(r['past_1y'])}</b>"),
                    ("results up to", f"<b>{pd.Timestamp(r['fy']):%b %Y}</b>")]) + "</div>")
                if not r["studied"]:
                    show('<div class="flag warn">outside what the test covered (a bank or lender, or under ₹25 lakh traded a day): '
                         'the odds below may not apply.</div>')
                warns = [w for w in str(r.get("warnings") or "").split("; ") if w and w != "nan"]
                sigs = [g_ for g_ in str(r.get("signals") or "").split("; ") if g_ and g_ != "nan"]
                if warns:
                    show('<div class="flag warn"><b>warnings:</b> ' + esc(", ".join(warns)) + ' — in the test, stocks with '
                         'warnings like these lost money more often. find out why before buying.</div>')
                show(f'<div class="note"><b>improving signs ({len(sigs)} of 5):</b> '
                     + (esc(", ".join(sigs)) if sigs else "none") + '</div>')
                CORE = longterm.CORE
                why = {
                    CORE[0]: "–" if pd.isna(r["sales_up"]) else f"sales went up in {int(r['sales_up'])} of the last 3 years",
                    CORE[1]: f"{lt_p(r['sales_g3'])} a year",
                    CORE[2]: "no loss in the last 4 years" if r["loss_years"] == 0 else f"a loss in {int(r['loss_years'])} of the last 4 years",
                    CORE[3]: f"{r['roce_avg3']:.0f}% a year" if pd.notna(r["roce_avg3"]) else "not enough history",
                    CORE[4]: f"debt = {r['de']:.2f}× its own money" if pd.notna(r["de"]) else "–",
                    CORE[5]: f"{r['cash_conv']:.0%} of profit came in as cash (3 yrs)" if pd.notna(r["cash_conv"]) else "–"}
                sec("the six good-company checks", "these did not raise the odds of doubling on their own; they help avoid accidents")
                show('<div style="display:grid;grid-template-columns:2.6rem 1fr auto;gap:.25rem .8rem;font-size:.84rem">' + "".join(
                    f'<span class="{"up" if r[c] else "dn"}">{"yes" if r[c] else "no"}</span><span>{esc(q)}</span>'
                    f'<span class="dim" style="text-align:right;font-size:.75rem">{esc(why[c])}</span>' for c, q in longterm.PLAIN.items())
                     + "</div>")
                sec("stocks like this, in the past", "5 years after each test date · one stock, and ×5 = equal money in 5 such stocks")
                rows = []
                for gn in ["every stock"] + [g for g in str(r["groups"]).split("; ") if g]:
                    g, b = LG[gn], LG[gn].get("basket5") or {}
                    label = f"<b>{esc(lt_gname(gn))}</b>" if gn != "every stock" else '<span class="dim">every stock (for comparison)</span>'
                    rows.append(dict(_wrap=label, d=lt_p(g["doubled"]), l=lt_p(g["lost"]), c=lt_p(g["crash"]),
                                     bd=lt_p(b.get("doubled")), bl=lt_p(b.get("lost"))))
                show(table(rows, [("_wrap", "group this stock is in"), ("d", "doubled"), ("l", "lost money"),
                                  ("c", "fell 50%+"), ("bd", "×5 doubled"), ("bl", "×5 lost")],
                           numeric={"d", "l", "c", "bd", "bl"}))
                show('<div class="note">odds for a <i>group</i> of past stocks, not a forecast for this one. no group came close to a '
                     'sure thing: the best single-stock odds of doubling were about 6 in 10.</div>')

    else:
        LS = longterm_scores()
        if pos.empty:
            show('<div class="note">no open positions — add trades in <b>portfolio</b> (top right).</div>')
        elif LS.empty:
            show('<div class="note">no long-term scores yet — run <b>python longterm.py fetch</b> once (reads the '
                 'yearly results of every company, about an hour), then each <b>update data</b> keeps them current.</div>')
        else:
            m = pos.merge(LS, on="symbol", how="left")
            m["value"] = m.qty * m.price
            tot = m.value.sum()
            rows = []
            for x in m.sort_values("value", ascending=False).itertuples():
                gs = [g for g in str(x.groups).split("; ") if g and g != "nan"]
                tn = longterm.TIERS[int(x.tier) - 1][0] if pd.notna(x.tier) and int(x.tier) <= len(longterm.TIERS) else "–"
                wn = str(x.warnings) if isinstance(x.warnings, str) else ""
                rows.append(dict(s=f"<b>{esc(x.symbol)}</b>", w=lt_p(x.value / tot) if tot and pd.notna(x.value) else "–",
                                 t=esc(tn), wr=f'<span class="dn">{esc(wn)}</span>' if wn else '<span class="dim">–</span>',
                                 c=f"{int(x.core)}/6" if pd.notna(x.core) else '<span class="dim">–</span>', p=lt_ps(x.ps_rank),
                                 e=f"{x.pe:.0f}" if pd.notna(x.pe) else ('<span class="dn">loss</span>' if pd.notna(x.core) else "–"),
                                 _wrap=", ".join(esc(lt_gname(g)) for g in gs) or '<span class="dim">none of the tested groups</span>'))
            sec("your open positions, through the long-term lens", f"{len(m)} stocks · price for sales: 1 = cheapest of 100")
            show(table(rows, [("s", "symbol"), ("w", "share of money"), ("t", "group"), ("c", "checks"),
                              ("p", "price for sales"), ("e", "P/E"), ("wr", "warnings")],
                       numeric={"w", "c", "p", "e"}))
            top = m.value.max() / tot if tot else 0
            show(f'<div class="note">you hold <b>{len(m)}</b> stocks; the largest is <b>{lt_p(top)}</b> of your money. in the test, '
                 f'one stock on its own lost money {lt_p(lt_all["lost"])} of the time over 5 years; five stocks with equal money '
                 f'{lt_p(lt_all["basket5"]["lost"])}. spreading new money so no single stock carries the plan was the most '
                 f'reliable thing the test found.</div>')


with t_lt:
    longterm_tab()


# ---------------------------------------------------------------- stock
with t_stock:
    # board names first, then every listed company — research any stock, not just the board
    syms = list(dict.fromkeys(list(v.symbol) + sorted(set(b.symbol)) + all_symbols()))
    sym = st.selectbox("symbol — type any NSE symbol", syms, index=0 if syms else None)
    if sym:
        fd = load_fundamentals(sym)
        r = bs.loc[sym] if sym in bs.index else pd.Series(dict(status="–", company_name=fd["name"] if fd else ""))
        show(f'<div class="bar" style="margin-top:.6rem"><div class="brand">{esc(sym)} '
             f'<span class="dim" style="letter-spacing:0;font-weight:400">{esc(r.get("company_name", ""))}</span></div>'
             f'<div class="meta">{esc(r.get("industry") if pd.notna(r.get("industry")) else "")}'
             f'{" / " + esc(r.get("sub_industry")) if pd.notna(r.get("sub_industry")) else ""} · '
             f'<a href="https://www.screener.in/company/{sym}/consolidated/" target="_blank">screener</a> · '
             f'<a href="https://www.nseindia.com/get-quotes/equity?symbol={sym}" target="_blank">nse</a></div></div>')
        last_px = r.get("close") if pd.notna(r.get("close")) else (prices(sym).close.iloc[-1] if len(prices(sym)) else None)
        kv = [("status", status(r.status) if r.status in STATUS_C else '<span class="dim">off board</span>'),
              ("close", f"₹{num(last_px)} {pct(r.get('chg1d'), 1)}"),
              ("sales yoy", pct(r.get("sales_yoy"))), ("profit yoy", pct(r.get("pat_yoy"))), ("quarter", f"{pd.Timestamp(r.qtr):%b %Y}" if pd.notna(r.get("qtr")) else "–"),
              ("p/e · roce", f"{num(r.get('pe'), 0)} · {num(r.get('roce'), 0)}%"), ("1y", pct(r.get("ret1y"))),
              ("capex", "yes" if str(r.get("capex")) == "True" else "–"), ("trigger", num(r.get("trigger")))]
        show('<div class="kv">' + "".join(f"<div><span>{k}</span>{v_}</div>" for k, v_ in kv) + "</div>")

        # ---- company snapshot: what it does + verdicts, all from public data and our own scan (no AI, no tokens)
        if fd:
            pxs = prices(sym)
            if len(pxs):
                pxs = pxs.assign(trade_date=pd.to_datetime(pxs.trade_date), close=adjust_prices(pxs.close)).dropna(subset=["close"])
            imap = industry_table()
            ind_ = r.get("industry") if pd.notna(r.get("industry")) else imap.industry.get(sym)
            sub_ = r.get("sub_industry") if pd.notna(r.get("sub_industry")) else (imap.sub_industry.get(sym) if "sub_industry" in imap else None)
            try:
                traps_ = insights.trap_scores(fd, sym, pxs if len(pxs) else None)
            except Exception:
                traps_ = None
            vd = insights.free_snapshot(fd, sym, pxs if len(pxs) else None, universe(), ind_, sub_, traps_)
            vb = brief.latest_cached(sym)
            ai = vb[4] if vb and vb[4] else None
            sec("company snapshot", ("verdicts by the AI brief, grounded in filings + measured public data; rule-based verdicts "
                                     "beside them as a cross-check" if ai else "rule-based verdicts from public data")
                + " · research ratings, not buy/sell calls")
            c1, c2 = st.columns([2, 3], gap="large")
            with c1:
                show(f'<div class="meta">{esc(vd["sector"])}</div>')
                show(f'<p style="margin:.2rem 0 .5rem">{esc(tidy_about(fd.get("about")) or "no description on Screener")}</p>')
                ol = vd["oil_link"]
                show(f'<div class="nxi {"down" if ol == "high" else "watch" if ol == "medium" else "up"}" style="display:inline-block">'
                     f'oil-price link: <b>{ol}</b><span>how much crude prices move its costs or demand</span></div>')
                if vd.get("panic"):
                    show('<div class="meta" style="margin-top:.7rem">on the market’s worst days since 2023</div>')
                    st.plotly_chart(viz.panic_bars(vd["panic"]), width="stretch", config=PLOT)
                if fd.get("key_points"):
                    with st.expander("business details (Screener)"):
                        st.markdown(esc(fd["key_points"]))
            with c2:
                show(viz.verdict_card_html(ai, rules=vd) if ai else viz.verdict_card_html(vd))
                show('<div class="note">rule-based: business = the growth check · sector = industry peers in our scan · war &amp; oil = '
                     'industry oil map + the company’s own description + how it fell on panic days · before buying = the '
                     'safety radar. ' + ("the AI read these plus the filings and news; ≠ marks where it disagrees — hover "
                     "“rules” for the rule-based reason." if ai else "generate the AI brief below for verdicts that also "
                     "read the filings and news (one cached call).") + '</div>')

        # ---- how it was ranked: the exact rules (rising.RULES / classify) applied to this stock
        U = universe()
        urow = U[U.symbol == sym] if len(U) else U
        if len(urow):
            ur = urow.iloc[0].to_dict()
            sb = pb = None
            qq = fd["tables"].get("quarters") if fd else None
            if qq is not None and "Sales" in qq.index and qq.shape[1] >= 4:  # next quarter is compared with this one
                sb = qq.loc["Sales"].iloc[-4]
                pb = qq.loc["Net Profit"].iloc[-4] if "Net Profit" in qq.index else None
            e = rising.explain(ur, sb, pb)
            sec("how it was ranked", "the same rules and thresholds that build the board · hover a test name for what it means")
            show(viz.gate_path_html(e))
            if e["next"]:
                show('<div class="meta" style="margin-bottom:.3rem">what would change it</div><div class="nx">' + "".join(
                    f'<div class="nxi {k}">{"▲" if k == "up" else "▼" if k == "down" else "◎"} {esc(a)}<span>{esc(w)}</span></div>'
                    for k, a, w in e["next"]) + "</div>")
            c1, c2 = st.columns([2, 3], gap="large")
            with c1:
                on_board = sym in bs.index and pd.notna(bs.loc[sym].get("s_calm"))
                if on_board:
                    lab = b[b.status == r.status].reset_index(drop=True)
                    rank = int(lab.index[lab.symbol == sym][0]) + 1
                    show(f'<div class="meta">score breakdown · #{rank} of {len(lab)} {r.status.lower()} · steadiness · decides the order on the board</div>')
                    st.plotly_chart(viz.score_waterfall(bs.loc[sym], rank, len(lab)), width="stretch", config=PLOT)
                    show('<div class="note">each part is a rank from 0 to 25 among all board stocks (25 = best). '
                         'the label comes from the gates; the score only orders stocks within a label. in the 3-year '
                         'backtest the 15 steadiest growers each month hit the −20% stop far less often than the fastest-growing.</div>')
                hh = history()
                hh = hh[hh.symbol == sym] if len(hh) else hh
                if len(hh):
                    show('<div class="meta">label history</div>')
                    st.plotly_chart(viz.label_timeline(hh), width="stretch", config=PLOT)
                bt_p = DATA / "backtest.json"
                if e["label"] in ("LEADER", "EMERGING", "WAIT", "HOT") and read_json(bt_p):
                    bt_ = pd.DataFrame(read_json(bt_p))
                    mine, rest = bt_[bt_.status == e["label"]], bt_[bt_.status == "everything else"]
                    if len(mine):
                        show(f'<div class="note">track record of this label in the backtest: median '
                             f'{pct(mine["median"].median())} over 6 months, {mine.hit.mean():.0%} of picks up '
                             f'(every other stock: {pct(rest["median"].median())}) · {len(mine)} seasons, small sample</div>')
            with c2:
                show(f'<div class="meta">where it stands among all {len(U):,} scanned stocks · dotted line = the threshold the rule uses</div>')
                st.plotly_chart(viz.crowd_strips(U, ur), width="stretch", config=PLOT)
        else:
            sec("how it was ranked", "not scanned — it fails an entry condition before the gates")
            # the exact numbers the scan used (data/entry.csv, written by rising.scan)
            ent_p = DATA / "entry.csv"
            ent = pd.read_csv(ent_p) if ent_p.exists() else pd.DataFrame(columns=["symbol"])
            er = ent[ent.symbol == sym]
            er = er.iloc[0] if len(er) else {}
            last_q, sales_q, np_q, turn = (er.get(k) if len(er) else None for k in ("qtr", "sales", "pat", "turn_cr"))
            has_q = last_q is not None and pd.notna(last_q)
            fresh = has_q and pd.Timestamp(last_q) >= pd.Timestamp.now() - pd.Timedelta(days=200)
            conds = [("results in the last 6 months", fresh, f"latest: {pd.Timestamp(last_q):%b %Y}" if has_q else "none"),
                     ("profitable last quarter", np_q is not None and np_q > 0, f"net profit ₹{np_q:,.0f} Cr" if np_q is not None else "–"),
                     ("₹25 Cr+ quarterly sales", sales_q is not None and sales_q >= 25,
                      f"₹{sales_q:,.0f} Cr" if sales_q is not None else "–"),
                     ("₹1 Cr+ traded a day (60-day median)", turn is not None and turn >= 1, f"₹{turn:,.1f} Cr" if turn is not None else "–")]
            show(table([dict(m=f'<span class="{"up" if ok else "dn"}">{"✓" if ok else "✗"}</span>', c=c, v=esc(v)) for c, ok, v in conds],
                       [("m", ""), ("c", "entry condition"), ("v", "this stock")]))
            show('<div class="note">these are the numbers the scan itself used (database + Screener cache). the scan skips tiny, '
                 'loss-making, stale or illiquid companies — their numbers are too noisy to rank fairly. if a figure here '
                 'differs from the charts below, the data sources disagree (common for finance companies); '
                 'the next update re-fetches it.</div>')

        price_chart(sym, r)
        if fd:
            growth_story(fd)
        else:
            show('<div class="note">no fundamentals page found for this symbol</div>')

        cached = brief.latest_cached(sym)
        sec("ai brief — what these charts mean", "explains, never decides · numbers & filings pre-digested locally to save tokens")
        if st.button("update brief" if cached else "generate brief", key=f"brief_{sym}", disabled=not settings.ai_ready(),
                     help="re-reads filings, results and news; the AI is only called if something changed"):
            with st.spinner("reading results, filings and news — 20–60 s"):
                try:
                    brief.brief(sym, status=r.get("status", ""))
                    cached = brief.latest_cached(sym)
                except Exception as e:
                    show(f'<div class="flag">brief failed: {esc(e)}</div>')
        if cached:
            gen, md, srcs, usage, _ = cached
            st.markdown(md)
            link = lambda x, label: f'<a href="{x["url"]}" target="_blank">{label}</a>'
            groups = [("filings", [link(x, f'{x["id"]} {esc(x["type"]).lower()} {x["date"]}') for x in srcs if x["id"][0] == "D"]),
                      ("red flags", [link(x, f'{x["id"]} {x["date"]}') for x in srcs if x["id"][0] == "R"]),
                      ("news", [('<span class="dn">' if x["type"] == "negative news" else "<span>")
                                + link(x, f'{x["id"]} {esc(x.get("title", ""))[:70]}') + "</span>" for x in srcs if x["id"][0] == "N"])]
            cost = f' · {usage["input"]:,} + {usage["output"]:,} tokens' if usage.get("input") else ""
            show(f'<div class="note">written {gen:%d %b}{cost} · sources: [F] = the numbers in the charts above<br>'
                 + "<br>".join(f"{g}: " + " · ".join(items) for g, items in groups if items) + "</div>")
        elif not settings.ai_ready():
            show('<div class="note">add an AI key in <b>settings</b> (top right) to enable briefs</div>')

        fl = q("""select cast(filing_datetime as date) date, category, headline from corporate_filings
                  where symbol = ? order by filing_datetime desc limit 20""", [sym])
        sec("recent filings")
        show(table([dict(d=f"{pd.Timestamp(x.date):%d %b}", c=esc(x.category).lower(), _wrap=esc(clip(x.headline, 140)))
                    for x in fl.itertuples()], [("d", "date"), ("c", "type"), ("_wrap", "headline")]))

# ---------------------------------------------------------------- compare
with t_cmp:
    default = [s for s in pos.symbol if s in set(syms)][:3] or list(v.symbol[:2])
    picks = st.multiselect("compare up to 3 stocks", syms, default=default, max_selections=3)
    data = {s: load_fundamentals(s) for s in picks}
    data = {s: f for s, f in data.items() if f}
    if len(data) >= 2:
        prof = {s: insights.compare_profile(f) for s, f in data.items()}
        sec("profile", "bigger shape = faster growth, better quality, more capacity coming · hover for the numbers")
        c1, c2 = st.columns([3, 2], gap="large")
        c1.plotly_chart(viz.compare_radar({s: p[0] for s, p in prof.items()}), width="stretch", config=PLOT)
        fmt_raw = {"sales growth": lambda x: f"{x:+.0f}%", "profit growth": lambda x: f"{x:+.0f}%", "margin": lambda x: f"{x:.1f}%",
                   "return on capital": lambda x: f"{x:.0f}%", "cash backs profit": lambda x: f"{x:.0%}",
                   "debt/equity": lambda x: f"{x:.2f}", "capacity coming": lambda x: f"{x:+.0%}"}
        with c2:
            show(table([dict(k=k, **{s: (fmt_raw[k](p[1][k]) if p[1][k] is not None and pd.notna(p[1][k]) else "–")
                                     for s, p in prof.items()}) for k in fmt_raw],
                       [("k", "")] + [(s, f"<b>{s}</b>") for s in prof], numeric=set(prof)))
            show('<div class="note">sales/profit growth = last 12 months vs the 12 before · margin = last 4 quarters · '
                 'capacity coming = potential sales from plants under construction</div>')

        sec("growth race", "quarterly sales, indexed to 100 at the start — who is compounding faster?")
        series = {}
        for s, f in data.items():
            q_ = f["tables"].get("quarters")
            if q_ is not None and "Sales" in q_.index:
                ss = q_.loc["Sales"].dropna().tail(12)
                series[s] = ss / ss.iloc[0] * 100
        c1, c2 = st.columns(2, gap="large")
        if series:
            c1.plotly_chart(viz.compare_lines(series), width="stretch", config=PLOT)
        margins = {s: f["tables"]["quarters"].loc["OPM %"].dropna().tail(12) for s, f in data.items()
                   if "OPM %" in f["tables"]["quarters"].index}
        if margins:
            c2.markdown('<div class="meta">operating margin % · quarterly</div>', unsafe_allow_html=True)
            c2.plotly_chart(viz.compare_lines(margins, suffix="%"), width="stretch", config=PLOT)
    else:
        show('<div class="empty">pick at least two stocks</div>')

# ---------------------------------------------------------------- sectors
with t_sec:
    s = b[b.status != "BROKE"].copy()
    s["industry"] = s.industry.fillna("unclassified")
    g = s.groupby("industry").agg(n=("symbol", "count"), l=("status", lambda x: (x == "LEADER").sum()),
                                  e=("status", lambda x: (x == "EMERGING").sum()), w=("status", lambda x: (x == "WAIT").sum()),
                                  h=("status", lambda x: (x == "HOT").sum()),
                                  m3=("ret3m", "median"), sy=("sales_yoy", "median"),
                                  names=("symbol", lambda x: ", ".join(x[:8]))).sort_values("n", ascending=False)
    sec("themes", "box size = how many rising companies an industry holds · colour = 3-month price move · click to zoom in")
    st.plotly_chart(viz.themes_treemap(b), width="stretch", config=PLOT)
    show('<div class="note">many rising names in one industry = a theme with a tailwind (like telecom for STLTECH / HFCL). '
         'green boxes are already moving; dark ones are growing but not yet noticed.</div>')
    sec("by industry")
    show(table([dict(i=esc(i), n=r.n, l=f'<span style="color:{C["amber"]}">{r.l}</span>', e=f'<span class="up">{r.e}</span>',
                     w=f'<span style="color:{C["blue"]}">{r.w}</span>', h=f'<span style="color:{STATUS_C["HOT"]}">{r.h}</span>',
                     m3=pct(r.m3), sy=pct(r.sy), _wrap=esc(r.names))
                for i, r in g.iterrows()],
               [("i", "industry"), ("n", "names"), ("l", "leader"), ("e", "emerging"), ("w", "wait"), ("h", "hot"),
                ("m3", "3m median"), ("sy", "sales yoy"), ("_wrap", "names")], numeric={"n", "l", "e", "w", "h", "m3", "sy"}))

# ---------------------------------------------------------------- filings
with t_fil:
    a1, a2, _ = st.columns([1, 1, 4])
    days = a1.selectbox("last", [3, 7, 14, 30], index=1, format_func=lambda d: f"{d} days")
    only = a2.checkbox("board names only", True)
    fl = filings(days)
    if len(fl):
        fl["stt"] = fl.symbol.map(bs.status)
        fl = fl[fl.stt.notna()] if only else fl
    sec("catalyst filings", "capacity · orders · results · presentations")
    show(table([dict(d=f"{pd.Timestamp(x.date):%d %b}", sym=f"<b>{esc(x.symbol)}</b>",
                     st=status(x.stt) if isinstance(x.stt, str) else "", c=esc(x.category).lower(), _wrap=esc(clip(x.headline, 130)))
                for x in fl.head(300).itertuples()], [("d", "date"), ("sym", "symbol"), ("st", "status"), ("c", "type"), ("_wrap", "headline")]))

# ---------------------------------------------------------------- watchlist
with t_watch:
    txt = st.text_area("symbols, space or newline separated", " ".join(watch), height=80)
    if st.button("save watchlist"):
        save_text(WATCH, "\n".join(txt.upper().split()))
        st.rerun()
    rows = []
    for s_ in watch:
        if s_ in bs.index:
            r = bs.loc[s_]
            sig = ""
            if r.prev_status == "WAIT" and r.status in ("EMERGING", "LEADER"):
                sig = '<span class="up">triggered</span>'
            elif r.status == "BROKE":
                sig = '<span class="dn">dropped off</span>'
            elif r.status == "HOT":
                sig = '<span class="am">ran too far</span>'
            rows.append(dict(sym=f"<b>{esc(s_)}</b>", st=status(r.status), c=num(r.close), t=num(r.trigger),
                             g=pct(r.trigger / r.close - 1, 1) if pd.notna(r.trigger) else '<span class="dim">–</span>',
                             s=pct(r.sales_yoy), p=pct(r.pat_yoy), sig=sig))
        else:  # not labelled: show what the full scan knows, so the row isn't blank
            uu = universe()
            r = uu.set_index("symbol").loc[s_] if len(uu) and s_ in set(uu.symbol) else None
            dash = '<span class="dim">–</span>'
            rows.append(dict(sym=f"<b>{esc(s_)}</b>", st='<span class="dim">off board</span>', t=dash, g=dash,
                             c=num(r.close) if r is not None else dash, s=pct(r.sales_yoy) if r is not None else dash,
                             p=pct(r.pat_yoy) if r is not None else dash,
                             sig="" if r is not None else '<span class="dim">not scanned</span>'))
    sec("watchlist", "trigger = price to close above before buying a wait name")
    show(table(rows, [("sym", "symbol"), ("st", "status"), ("c", "close"), ("t", "trigger"), ("g", "to go"),
                      ("s", "sales"), ("p", "profit"), ("sig", "")], numeric={"c", "t", "g", "s", "p"}))

# ---------------------------------------------------------------- history
with t_hist:
    bt = DATA / "backtest.json"
    rows = read_json(bt)
    if rows:
        sec("does it work? backtest", "median 6-month return of each label vs every other stock · bought at each results season")
        st.plotly_chart(viz.scoreboard(rows), width="stretch", config=PLOT)
        show('<div class="note">tall amber/green bars above the grey "everything else" bar = the labels picked better than the market. '
             '3 years (Jun 2023 – May 2026): a rally, the 2024–25 fall and the recovery. still one cycle — the live record below is the real test.</div>')

    # ---- rules sandbox: change any threshold, see today's board and the backtest re-run instantly
    if rising.SEASONS_FILE.exists():
        sec("rules sandbox", "move a threshold → today's board and the backtest re-run with your rules · nothing is saved")
        R = rising.RULES
        k = "sbx_"
        if st.button("reset to current rules"):
            for key in [x for x in st.session_state if x.startswith(k)]:
                del st.session_state[key]
        a1, a2, a3, a4, a5, a6, a7 = st.columns(7)
        mine = dict(
            sales_yoy=a1.slider("sales growth >", 0, 80, int(R["sales_yoy"] * 100), 5, format="%d%%", key=k + "s") / 100,
            pat_yoy=a2.slider("profit growth >", 0, 100, int(R["pat_yoy"] * 100), 5, format="%d%%", key=k + "p") / 100,
            streak=a3.slider("strong quarters of 3 ≥", 1, 3, R["streak"], key=k + "q"),
            seq_ups=a4.slider("sales up QoQ of 3 ≥", 0, 3, R["seq_ups"], key=k + "u"),
            near_high=1 - a5.slider("within x% of 52w high", 5, 40, int(round((1 - R["near_high"]) * 100)), 5,
                                    format="%d%%", key=k + "h") / 100,
            run_up=a6.slider("leader if 1y run >", 30, 200, int(R["run_up"] * 100), 10, format="%d%%", key=k + "r") / 100,
            too_hot=a7.slider("too hot if 1y run >", 100, 500, int(R["too_hot"] * 100), 10, format="%d%%", key=k + "t") / 100)
        changed = {kk: vv for kk, vv in mine.items() if abs(vv - R[kk]) > 1e-9}
        U = universe()
        if len(U):
            now_lab = rising.classify(U).fillna("–")
            new_lab = rising.classify(U, mine).fillna("–")
            cnt = lambda s: " · ".join(f'{status(x)} <b>{int((s == x).sum())}</b>' for x in ("LEADER", "EMERGING", "WAIT", "HOT"))
            joined = U.symbol[new_lab.isin(["LEADER", "EMERGING"]) & ~now_lab.isin(["LEADER", "EMERGING"])]
            left_ = U.symbol[now_lab.isin(["LEADER", "EMERGING"]) & ~new_lab.isin(["LEADER", "EMERGING"])]
            show(f'<div class="meta">today · current rules: {cnt(now_lab)} &nbsp;→&nbsp; your rules: {cnt(new_lab)}</div>'
                 + (f'<div class="note"><span class="up">+ joins the buy-list:</span> {esc(", ".join(joined[:25]))}'
                    f'{" …" if len(joined) > 25 else ""}<br><span class="dn">− leaves it:</span> {esc(", ".join(left_[:25]))}'
                    f'{" …" if len(left_) > 25 else ""}</div>' if changed else ""))
        seasons = load_seasons()
        base_bt = base_backtest()
        mine_bt = rising.backtest_rules(seasons, mine) if changed else base_bt
        c1, c2 = st.columns([3, 2], gap="large")
        c1.plotly_chart(viz.sandbox_compare(base_bt, mine_bt), width="stretch", config=PLOT)
        with c2:
            agg = mine_bt.groupby("status").agg(n=("n", "mean"), med=("median", "mean"), hit=("hit", "mean"), p10=("p10", "mean"))
            show(table([dict(l=status(l) if l in STATUS_C else '<span class="dim">everything else</span>',
                             n=f"{r.n:.0f}", m=pct(r.med), h=f"{r.hit:.0%}", w=pct(r.p10))
                        for l, r in agg.reindex(["LEADER", "EMERGING", "WAIT", "HOT", "everything else"]).dropna().iterrows()],
                       [("l", "your rules"), ("n", "stocks/season"), ("m", "median 6m"), ("h", "% up"), ("w", "worst 10%")],
                       numeric={"n", "m", "h", "w"}))
            small = agg.loc[[x for x in ("LEADER", "EMERGING") if x in agg.index], "n"].min() if len(agg) else 0
            show(f'<div class="note">{"<span class=dn>⚠ fewer than 15 stocks a season: these numbers are mostly luck.</span> " if small < 15 else ""}'
                 'beware over-fitting: with only a few past seasons, tight rules always look great in hindsight. '
                 'prefer rules that keep 20+ stocks a season and still beat "everything else".</div>')
    h = history()
    sec("live track record", "how each name did after it first appeared on the board")
    if len(h):
        d = h[h.status != "BROKE"].sort_values("asof").groupby("symbol").agg(
            first=("asof", "min"), scans=("asof", "nunique"), c0=("close", "first"), then=("status", "first"))
        uu = universe()
        now_px = bs.close.combine_first(uu.set_index("symbol").close) if len(uu) else bs.close  # left the board: still priced
        now_st = bs.status.reindex(d.index).fillna("off board")
        d = d.join(now_px.rename("close")).assign(ret=lambda x: x.close / x.c0 - 1, now=now_st)
        d = d.sort_values(["first", "ret"], ascending=[False, False])
        show(table([dict(sym=f"<b>{esc(i)}</b>", f=f"{pd.Timestamp(r['first']):%d %b}", n=r.scans, st=status(r["then"]),
                         sn=status(r["now"]) if r["now"] in STATUS_C else '<span class="dim">off board</span>',
                         c0=num(r.c0), c=num(r.close), ret=pct(r.ret, 1)) for i, r in d.iterrows()],
                   [("sym", "symbol"), ("f", "first seen"), ("n", "scans"), ("st", "label then"), ("sn", "label now"),
                    ("c0", "price then"), ("c", "price now"), ("ret", "since")], numeric={"n", "c0", "c", "ret"}))
