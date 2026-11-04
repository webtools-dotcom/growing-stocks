"""Rising Stocks Terminal. Run:  streamlit run app.py"""
import html
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

import duckdb
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import brief  # filings-grounded LLM brief
import fundamentals  # full financials + growth checklist
import rising  # exit rule + alert logic (shared with backtests)
import settings  # API keys in the OS keychain
from config import DATA, DB, HERE
from data_sync import adjust_prices
import insights  # trap scores, capacity bridge, order book, valuation, smart money
from viz import C, PLOT, STATUS_C, chart_layout
import viz

WATCH, POS = DATA / "watchlist.txt", DATA / "positions.csv"
DEFAULT_EXCLUDE = ["Oil Gas & Consumable Fuels", "Information Technology", "Energy"]
STATUSES = ["LEADER", "EMERGING", "WAIT", "BROKE"]
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
[data-baseweb="tag"] {{ background: transparent !important; border: 1px solid {C['line']}; }}
[data-baseweb="tag"] span {{ color: {C['text']}; font-size: .74rem; }}
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
.nxi span {{ display: block; color: {C['dim']}; font-size: .68rem; margin-top: .1rem; }}
.nxi.up {{ border-left: 2px solid {C['green']}; }} .nxi.down {{ border-left: 2px solid {C['red']}; }}
.nxi.watch {{ border-left: 2px solid {C['amber']}; }}
/* company snapshot: verdict meters */
.vc {{ display: grid; grid-template-columns: 9.5rem 7.5rem 1fr; gap: .45rem .9rem; align-items: center; font-size: .78rem; }}
.vc .vl {{ color: {C['dim']}; font-size: .72rem; text-transform: lowercase; }}
.vm {{ display: flex; gap: 2px; }} .vm i {{ flex: 1; height: 7px; background: #1f201a; }}
.vm i.on {{ outline: 1px solid {C['text']}; }}
.vr {{ color: {C['text']}; }} .vr b {{ font-weight: 600; margin-right: .4rem; }}
.vc .vall {{ border-top: 1px solid {C['line']}; padding-top: .45rem; }}
.xr {{ display: block; color: {C['dim']}; font-size: .68rem; margin-top: .1rem; cursor: help; }}
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- data
def q(sql, params=None):
    """Short-lived read-only connection: never hold the lock the EOD sync needs."""
    try:
        con = duckdb.connect(str(DB), read_only=True)
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
    return f'<table class="t"><tr>{th}</tr>{tr}</table>'


def sec(title, right=""):
    st.markdown(f'<div class="sec">{title}<span>{right}</span></div>', unsafe_allow_html=True)


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


RANGES = {"1M": 31, "3M": 92, "6M": 183, "1Y": 366, "2Y": 731, "3Y": 1100}


def price_chart(sym, r):
    """Price with a plain-English summary, range buttons, trend line, your buy/stop, and volume. Hover only, no
    drag-zoom: picking a range re-slices the data so the y-axis always fits what you see."""
    px_ = prices(sym)
    if px_.empty:
        return
    px_ = px_.assign(trade_date=pd.to_datetime(px_.trade_date), close=adjust_prices(px_.close)).dropna(subset=["close"])
    px_["dma200"] = px_.close.rolling(200, min_periods=150).mean()  # computed on full history, before slicing
    h1, h2 = st.columns([3, 2])
    with h2:
        rng = st.segmented_control("range", list(RANGES), default="1Y", key=f"rng_{sym}", label_visibility="collapsed") or "1Y"
    d = px_[px_.trade_date >= px_.trade_date.iloc[-1] - pd.Timedelta(days=RANGES[rng])]
    last, first = d.close.iloc[-1], d.close.iloc[0]
    chg = last / first - 1
    hi52 = px_[px_.trade_date >= px_.trade_date.iloc[-1] - pd.Timedelta(days=365)].close.max()
    dma = px_.dma200.iloc[-1]

    trend = ("" if pd.isna(dma) else
             f' · {pct(last / dma - 1)} vs 200-day avg — <span class="{"up" if last > dma else "dn"}">'
             f'{"uptrend" if last > dma else "downtrend"}</span>')
    with h1:
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
            scores, err = None, e
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
                show(f'<div class="meta">order book: years of sales already booked</div>')
                st.plotly_chart(viz.order_gauge(b_["value_cr"], bridge["ttm_sales"]), width="stretch", config=PLOT)
                show(f'<div class="note">₹{b_["value_cr"]:,} Cr order book (<a href="{b_["url"]}" target="_blank">'
                     f'{esc(b_["source"]).lower()}, {b_["date"]}</a>) vs ₹{bridge["ttm_sales"]:,.0f} Cr sales in the last 12 months</div>')
            if orders["wins"]:
                show('<div class="meta">order wins announced</div>')
                st.plotly_chart(viz.order_wins(orders["wins"]), width="stretch", config=PLOT)

    # revenue & profit with the yoy growth printed on every bar
    h1, h2 = st.columns([4, 1])
    with h2:
        mode = st.segmented_control("view", ["quarterly", "annual"], default="quarterly", key=f"rp_{fd['symbol']}",
                                    label_visibility="collapsed") or "quarterly"
    with h1:
        sec("revenue & profit", "₹ crore · label = growth vs a year earlier")
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
                        dict(k="profit growth (3y/yr)", v=f"{g3:.0f}%" if g3 else "–"),
                        dict(k="PEG (P/E ÷ growth)", v=f'<span class="{"up" if peg and peg < 1 else "am" if peg and peg < 2 else "dn"}">{peg:.1f}</span>' if peg else "–")],
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


def run_update():
    with st.status("updating — prices, filings, new results, scan", expanded=True) as s:
        p = subprocess.Popen([sys.executable, str(HERE / "rising.py"), "update"], cwd=HERE,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                             env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        for line in p.stdout:
            if "litellm" not in line.lower():
                s.write(line.rstrip())
        s.update(label="done" if p.wait() == 0 else "update failed — see log above", state="complete")
    st.cache_data.clear()
    st.rerun()


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


left, mid, right = st.columns([6, 1, 1])
if mid.button("settings", width="stretch"):
    settings_dialog()
if b.empty:
    left.markdown('<div class="brand">RISING<span class="dim"> // nse growth terminal</span></div>', unsafe_allow_html=True)
    if right.button("update data", width="stretch"):
        run_update()
    show('<div class="flag warn">no data yet — click <b>update data</b>.<br><span class="dim">the first run downloads ~1.5 years of '
         'NSE prices and the results of every liquid company (~1,200) from public sources: about 30 minutes, once. '
         'after that a daily update takes about a minute. no API key needed.</span></div>')
    st.stop()

asof = pd.Timestamp(b["asof"].iloc[0])
cnt = b.status.value_counts()
counts = " · ".join(f'{status(s)} <b>{int(cnt.get(s, 0))}</b>' for s in STATUSES)
left.markdown(f'<div class="bar"><div class="brand">RISING<span class="dim"> // nse growth terminal</span></div>'
              f'<div class="meta">data {asof:%a %d %b %Y} &nbsp;·&nbsp; {counts}</div></div>', unsafe_allow_html=True)
if right.button("update data", width="stretch", help="prices + NSE filings + new results + rescan (≈1–10 min)"):
    run_update()
show('<div class="legend">leader = growing + already ran &gt;80% · emerging = growth accelerating, price confirmed · '
     'wait = watch only, buy above trigger · exit = −20% stop or 6-month review · research tool, not advice</div>')

# NSE publishes end-of-day data ~6 pm IST; expect the last weekday's data after 7 pm.
# ponytail: ignores exchange holidays, so on a holiday this nags once — harmless.
now = datetime.now()
expected = now.date() if now.hour >= 19 else now.date() - timedelta(days=1)
while expected.weekday() >= 5:
    expected -= timedelta(days=1)
if asof.date() < expected:
    show(f'<div class="flag warn">newer market data is out ({expected:%a %d %b}) — click <b>update data</b></div>')

excl = st.session_state.get("excl", [i for i in DEFAULT_EXCLUDE if i in set(b.industry.dropna())])
watch = WATCH.read_text().split() if WATCH.exists() else []
pos = pd.read_csv(POS) if POS.exists() else pd.DataFrame(columns=["symbol", "buy_date", "buy_price", "qty"])
if "thesis" not in pos:
    pos["thesis"] = ""
pos["thesis"] = pos.thesis.fillna("").astype(str)
THESIS = DATA / "thesis"


def snapshot_thesis(sym, thesis):
    """Freeze the growth check when you write why you bought, so it can be compared every quarter."""
    THESIS.mkdir(exist_ok=True)
    p_ = THESIS / f"{sym}.json"
    old = json.loads(p_.read_text(encoding="utf-8")) if p_.exists() else None
    if old and old.get("thesis") == thesis:
        return
    f = fundamentals.get(sym)
    checks = {t_: v_ for v_, t_, _ in fundamentals.checklist(f)} if f else {}
    p_.write_text(json.dumps(dict(thesis=thesis, date=str(pd.Timestamp.now().date()), checks=checks)), encoding="utf-8")
bs = b.set_index("symbol")

t_today, t_board, t_stock, t_cmp, t_sec, t_fil, t_watch, t_hist = st.tabs(
    ["today", "board", "stock", "compare", "themes", "filings", "watchlist", "scoreboard"])

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
    sec("positions", f"value ₹{value:,.0f} · invested ₹{invested:,.0f} · {pct(value / invested - 1, 1) if invested else ''}")
    if exits:
        show(f'<div class="flag">exit rule hit: <b>{", ".join(exits)}</b> — sell next session</div>')
    show(table(rows, [("sym", "symbol"), ("qty", "qty"), ("buy", "buy"), ("last", "last"), ("pnl", "p&amp;l"),
                      ("stop", "stop −20%"), ("review", "6m review"), ("st", "status"), ("sig", "signal")],
               numeric={"qty", "buy", "last", "pnl", "stop"}))
    with st.expander("edit positions"):
        edited = st.data_editor(pos, num_rows="dynamic", width="stretch", key="pos_editor",
                                column_config={"buy_date": st.column_config.TextColumn("buy_date (YYYY-MM-DD)"),
                                               "thesis": st.column_config.TextColumn("why I bought (one line)", width="large")})
        if st.button("save positions"):
            edited = edited.dropna(subset=["symbol"]).assign(symbol=lambda d: d.symbol.str.upper().str.strip(),
                                                             thesis=lambda d: d.thesis.fillna("").astype(str))
            edited.to_csv(POS, index=False)
            for r_ in edited.itertuples():
                if r_.thesis.strip():
                    snapshot_thesis(r_.symbol, r_.thesis.strip())
            st.rerun()

    # thesis health: the growth check when you bought vs now
    rows_t = []
    for r_ in pos.itertuples():
        p_ = THESIS / f"{r_.symbol}.json"
        if not r_.thesis.strip() or not p_.exists():
            continue
        snap = json.loads(p_.read_text(encoding="utf-8"))
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
        show('<div class="note">tip: add "why I bought" in <b>edit positions</b> — the app will tell you each quarter whether that reason still holds</div>')
    for s in pos.symbol:
        c = brief.latest_cached(s)
        if c and (pd.Timestamp.now().date() - c[0]).days <= 7:
            with st.expander(f"new brief — {s} — written {c[0]:%d %b}"):
                st.markdown(c[1])

    al_path = DATA / "alerts.json"
    if al_path.exists():
        al = json.loads(al_path.read_text(encoding="utf-8"))
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
    if fn_p.exists():
        fn = json.loads(fn_p.read_text(encoding="utf-8"))
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
    sec("board", f"{len(v)} of {len(b)} · sorted by status, then score"
        + (f" · {n_surv} on NSE's surveillance list (⚠ = extra risk: price limits, operator activity)" if n_surv else ""))
    show(table([dict(st=status(r.status) + (' <span class="am">new</span>' if r.new and r.status != "BROKE" else ""),
                     sym=f"<b>{esc(r.symbol)}</b>" + (' <span class="dn" title="on NSE surveillance list">⚠ watch-list</span>'
                                                      if r.symbol in surv else ""), ind=f'<span class="dim">{esc(r.industry) if pd.notna(r.industry) else "–"}</span>',
                     c=num(r.close), d=pct(r.chg1d, 1), q=f'<span class="dim">{pd.Timestamp(r.qtr):%b %y}</span>' if pd.notna(r.qtr) else "",
                     s=pct(r.sales_yoy), p=pct(r.pat_yoy), cap="yes" if r.capex is True or r.capex == "True" else '<span class="dim">–</span>',
                     pe=num(r.pe, 0), y=pct(r.ret1y), t=num(r.trigger), m=num(r.market_cap_cr, 0))
                for r in v.itertuples()],
               [("st", "status"), ("sym", "symbol"), ("ind", "industry"), ("c", "close"), ("d", "1d"), ("q", "qtr"),
                ("s", "sales yoy"), ("p", "profit yoy"), ("cap", "capex"), ("pe", "p/e"), ("y", "1y"), ("t", "trigger"), ("m", "mcap cr")],
               numeric={"c", "d", "s", "p", "pe", "y", "t", "m"}))

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
                show(f'<p style="margin:.2rem 0 .5rem">{esc(fd.get("about") or "no description on Screener")}</p>')
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
                on_board = sym in bs.index and pd.notna(bs.loc[sym].get("s_sales"))
                if on_board:
                    lab = b[b.status == r.status].reset_index(drop=True)
                    rank = int(lab.index[lab.symbol == sym][0]) + 1
                    show(f'<div class="meta">score breakdown · #{rank} of {len(lab)} {r.status.lower()} · decides the order on the board</div>')
                    st.plotly_chart(viz.score_waterfall(bs.loc[sym], rank, len(lab)), width="stretch", config=PLOT)
                    show('<div class="note">each part is a rank from 0 to 1 among all board stocks (1 = best). '
                         'the label comes from the gates; the score only orders stocks within a label.</div>')
                hh = history()
                hh = hh[hh.symbol == sym] if len(hh) else hh
                if len(hh):
                    show('<div class="meta">label history</div>')
                    st.plotly_chart(viz.label_timeline(hh), width="stretch", config=PLOT)
                bt_p = DATA / "backtest.json"
                if e["label"] in ("LEADER", "EMERGING", "WAIT") and bt_p.exists():
                    bt_ = pd.DataFrame(json.loads(bt_p.read_text(encoding="utf-8")))
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
        show(table([dict(d=f"{pd.Timestamp(x.date):%d %b}", c=esc(x.category).lower(), _wrap=esc(str(x.headline)[:140]))
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
                                  m3=("ret3m", "median"), sy=("sales_yoy", "median"),
                                  names=("symbol", lambda x: ", ".join(x[:8]))).sort_values("n", ascending=False)
    sec("themes", "box size = how many rising companies an industry holds · colour = 3-month price move · click to zoom in")
    st.plotly_chart(viz.themes_treemap(b), width="stretch", config=PLOT)
    show('<div class="note">many rising names in one industry = a theme with a tailwind (like telecom for STLTECH / HFCL). '
         'green boxes are already moving; dark ones are growing but not yet noticed.</div>')
    sec("by industry")
    show(table([dict(i=esc(i), n=r.n, l=f'<span style="color:{C["amber"]}">{r.l}</span>', e=f'<span class="up">{r.e}</span>',
                     w=f'<span style="color:{C["blue"]}">{r.w}</span>', m3=pct(r.m3), sy=pct(r.sy), _wrap=esc(r.names))
                for i, r in g.iterrows()],
               [("i", "industry"), ("n", "names"), ("l", "leader"), ("e", "emerging"), ("w", "wait"), ("m3", "3m median"),
                ("sy", "sales yoy"), ("_wrap", "names")], numeric={"n", "l", "e", "w", "m3", "sy"}))

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
                     st=status(x.stt) if isinstance(x.stt, str) else "", c=esc(x.category).lower(), _wrap=esc(str(x.headline)[:130]))
                for x in fl.head(300).itertuples()], [("d", "date"), ("sym", "symbol"), ("st", "status"), ("c", "type"), ("_wrap", "headline")]))

# ---------------------------------------------------------------- watchlist
with t_watch:
    txt = st.text_area("symbols, space or newline separated", " ".join(watch), height=80)
    if st.button("save watchlist"):
        WATCH.write_text("\n".join(txt.upper().split()))
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
            rows.append(dict(sym=f"<b>{esc(s_)}</b>", st=status(r.status), c=num(r.close), t=num(r.trigger),
                             g=pct(r.trigger / r.close - 1, 1) if pd.notna(r.trigger) else '<span class="dim">–</span>',
                             s=pct(r.sales_yoy), p=pct(r.pat_yoy), sig=sig))
        else:
            rows.append(dict(sym=f"<b>{esc(s_)}</b>", st='<span class="dim">off board</span>'))
    sec("watchlist", "trigger = price to close above before buying a wait name")
    show(table(rows, [("sym", "symbol"), ("st", "status"), ("c", "close"), ("t", "trigger"), ("g", "to go"),
                      ("s", "sales"), ("p", "profit"), ("sig", "")], numeric={"c", "t", "g", "s", "p"}))

# ---------------------------------------------------------------- history
with t_hist:
    bt = DATA / "backtest.json"
    if bt.exists():
        rows = json.loads(bt.read_text(encoding="utf-8"))
        sec("does it work? backtest", "median 6-month return of each label vs every other stock · bought at each results season")
        st.plotly_chart(viz.scoreboard(rows), width="stretch", config=PLOT)
        show('<div class="note">tall amber/green bars above the grey "everything else" bar = the labels picked better than the market. '
             'small sample (2 years, mostly rising) — the live record below is the real test.</div>')

    # ---- rules sandbox: change any threshold, see today's board and the backtest re-run instantly
    if rising.SEASONS_FILE.exists():
        sec("rules sandbox", "move a threshold → today's board and the backtest re-run with your rules · nothing is saved")
        R = rising.RULES
        k = "sbx_"
        if st.button("reset to current rules"):
            for key in [x for x in st.session_state if x.startswith(k)]:
                del st.session_state[key]
        a1, a2, a3, a4, a5, a6 = st.columns(6)
        mine = dict(
            sales_yoy=a1.slider("sales growth >", 0, 80, int(R["sales_yoy"] * 100), 5, format="%d%%", key=k + "s") / 100,
            pat_yoy=a2.slider("profit growth >", 0, 100, int(R["pat_yoy"] * 100), 5, format="%d%%", key=k + "p") / 100,
            streak=a3.slider("strong quarters of 3 ≥", 1, 3, R["streak"], key=k + "q"),
            seq_ups=a4.slider("sales up QoQ of 3 ≥", 0, 3, R["seq_ups"], key=k + "u"),
            near_high=1 - a5.slider("within x% of 52w high", 5, 40, int(round((1 - R["near_high"]) * 100)), 5,
                                    format="%d%%", key=k + "h") / 100,
            run_up=a6.slider("leader if 1y run >", 30, 200, int(R["run_up"] * 100), 10, format="%d%%", key=k + "r") / 100)
        changed = {kk: vv for kk, vv in mine.items() if abs(vv - R[kk]) > 1e-9}
        U = universe()
        if len(U):
            now_lab = rising.classify(U).fillna("–")
            new_lab = rising.classify(U, mine).fillna("–")
            cnt = lambda s: " · ".join(f'{status(x)} <b>{int((s == x).sum())}</b>' for x in ("LEADER", "EMERGING", "WAIT"))
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
                        for l, r in agg.reindex(["LEADER", "EMERGING", "WAIT", "everything else"]).dropna().iterrows()],
                       [("l", "your rules"), ("n", "stocks/season"), ("m", "median 6m"), ("h", "% up"), ("w", "worst 10%")],
                       numeric={"n", "m", "h", "w"}))
            small = agg.loc[[x for x in ("LEADER", "EMERGING") if x in agg.index], "n"].min() if len(agg) else 0
            show(f'<div class="note">{"<span class=dn>⚠ fewer than 15 stocks a season: these numbers are mostly luck.</span> " if small < 15 else ""}'
                 'beware over-fitting: with only a few past seasons, tight rules always look great in hindsight. '
                 'prefer rules that keep 20+ stocks a season and still beat "everything else".</div>')
    h = history()
    sec("live track record", "how each name did after it first appeared on the board")
    if len(h):
        d = h[h.status != "BROKE"].groupby("symbol").agg(first=("asof", "min"), scans=("asof", "nunique"),
                                                          c0=("close", "first"), last=("status", "last"))
        d = d.join(bs.close).assign(ret=lambda x: x.close / x.c0 - 1).sort_values(["first", "ret"], ascending=[False, False])
        show(table([dict(sym=f"<b>{esc(i)}</b>", f=esc(r["first"]), n=r.scans, st=status(r["last"]), c0=num(r.c0), c=num(r.close),
                         ret=pct(r.ret, 1)) for i, r in d.iterrows()],
                   [("sym", "symbol"), ("f", "first seen"), ("n", "scans"), ("st", "label then"), ("c0", "price then"),
                    ("c", "now"), ("ret", "since")], numeric={"n", "c0", "c", "ret"}))
