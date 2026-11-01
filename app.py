"""Rising Stocks Terminal. Run:  streamlit run app.py"""
import html
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

WATCH, POS = DATA / "watchlist.txt", DATA / "positions.csv"
DEFAULT_EXCLUDE = ["Oil Gas & Consumable Fuels", "Information Technology", "Energy"]
STATUSES = ["LEADER", "EMERGING", "WAIT", "BROKE"]
CATALYST = ("Capacity addition", "Commencement of commercial production/operations",
            "Bagging/Receiving of orders/contracts", "Outcome of Board Meeting", "Investor Presentation",
            "Acquisition", "Press Release")
# palette: colour only carries meaning (status, gain/loss); everything else is text or dim text
C = dict(bg="#0f100d", line="#2a2b24", text="#d6d3c4", dim="#7f7c70", amber="#d9a54b",
         green="#93b874", blue="#7d9db6", red="#cf7462")
STATUS_C = {"LEADER": C["amber"], "EMERGING": C["green"], "WAIT": C["blue"], "BROKE": C["red"]}

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
    return q("""select d.trade_date, d.close, d.volume, d.delivery_pct from daily_market d join instruments i using(isin)
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


def chart_layout(fig, h=320):
    fig.update_layout(height=h, margin=dict(l=0, r=0, t=24, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="JetBrains Mono, monospace", size=11, color=C["dim"]), hovermode="x unified",
                      legend=dict(orientation="h", y=1.08, x=0, font=dict(size=10)), showlegend=True)
    fig.update_xaxes(showgrid=False, linecolor=C["line"])
    fig.update_yaxes(gridcolor="#1d1e19", zeroline=False)
    return fig


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


PLOT = {"displayModeBar": False}
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
         'price above the dotted 200-day average = uptrend'
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
    opm = _r(T.get("quarters"), "OPM %")
    if opm is not None:
        x = list(opm.index)[-12:]
        fig = go.Figure(go.Scatter(x=x, y=opm[x], mode="lines+markers", name="operating margin",
                                   line=dict(color=C["amber"], width=1.5), marker=dict(size=4),
                                   hovertemplate="%{x}: %{y:.1f}%<extra></extra>"))
        fig.update_yaxes(ticksuffix="%")
        mini("operating margin · quarterly", fig, c1)
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
    if sh is not None:
        x = list(sh.columns)[-10:]
        fig = go.Figure([go.Scatter(x=x, y=sh.loc[k, x], name=k.lower(), mode="lines", line=dict(color=c_, width=1.5),
                                    hovertemplate="%{x}: %{y:.1f}%<extra>" + k + "</extra>")
                         for k, c_ in [("Promoters", C["amber"]), ("FIIs", C["blue"]), ("DIIs", C["green"]), ("Public", C["dim"])]
                         if k in sh.index])
        fig.update_yaxes(ticksuffix="%")
        mini("who owns it · quarterly %", fig, c3)


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
bs = b.set_index("symbol")

t_today, t_board, t_stock, t_sec, t_fil, t_watch, t_hist = st.tabs(
    ["today", "board", "stock", "sectors", "filings", "watchlist", "history"])

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
                                column_config={"buy_date": st.column_config.TextColumn("buy_date (YYYY-MM-DD)")})
        if st.button("save positions"):
            edited.dropna(subset=["symbol"]).assign(symbol=lambda d: d.symbol.str.upper().str.strip()).to_csv(POS, index=False)
            st.rerun()
    for s in pos.symbol:
        c = brief.latest_cached(s)
        if c and (pd.Timestamp.now().date() - c[0]).days <= 7:
            with st.expander(f"new filings brief — {s} — read {c[0]:%d %b}"):
                st.markdown(c[1])
                show('<div class="note">' + " · ".join(f'<a href="{x["url"]}" target="_blank">{x["id"]} {esc(x["type"]).lower()} {x["date"]}</a>'
                                                        for x in c[2]) + "</div>")

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
    sec("board", f"{len(v)} of {len(b)} · sorted by status, then score")
    show(table([dict(st=status(r.status) + (' <span class="am">new</span>' if r.new and r.status != "BROKE" else ""),
                     sym=f"<b>{esc(r.symbol)}</b>", ind=f'<span class="dim">{esc(r.industry) if pd.notna(r.industry) else "–"}</span>',
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

        price_chart(sym, r)
        if fd:
            growth_story(fd)
        else:
            show('<div class="note">no fundamentals page found for this symbol</div>')

        cached = brief.latest_cached(sym)
        sec("ai brief", "read from the company's own filings · explains, never decides")
        if st.button("refresh brief" if cached else "generate brief", key=f"brief_{sym}"):
            with st.spinner("reading filings — 20–60 s"):
                try:
                    brief.brief(sym, status=r.get("status", ""), force=bool(cached))
                    cached = brief.latest_cached(sym)
                except Exception as e:
                    show(f'<div class="flag">brief failed: {esc(e)}</div>')
        if cached:
            gen, md, srcs = cached
            st.markdown(md)
            show(f'<div class="note">read {gen:%d %b} · ' + " · ".join(
                f'<a href="{x["url"]}" target="_blank">{x["id"]} {esc(x["type"]).lower()} {x["date"]}</a>' for x in srcs) + "</div>")
        elif not settings.ai_ready():
            show('<div class="note">add an AI key in <b>settings</b> (top right) to enable briefs</div>')

        fl = q("""select cast(filing_datetime as date) date, category, headline from corporate_filings
                  where symbol = ? order by filing_datetime desc limit 20""", [sym])
        sec("recent filings")
        show(table([dict(d=f"{pd.Timestamp(x.date):%d %b}", c=esc(x.category).lower(), _wrap=esc(str(x.headline)[:140]))
                    for x in fl.itertuples()], [("d", "date"), ("c", "type"), ("_wrap", "headline")]))

# ---------------------------------------------------------------- sectors
with t_sec:
    s = b[b.status != "BROKE"].copy()
    s["industry"] = s.industry.fillna("unclassified")
    g = s.groupby("industry").agg(n=("symbol", "count"), l=("status", lambda x: (x == "LEADER").sum()),
                                  e=("status", lambda x: (x == "EMERGING").sum()), w=("status", lambda x: (x == "WAIT").sum()),
                                  m3=("ret3m", "median"), sy=("sales_yoy", "median"),
                                  names=("symbol", lambda x: ", ".join(x[:8]))).sort_values("n", ascending=False)
    sec("sectors", "many names in one industry = a sector tailwind")
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
    h = history()
    sec("track record", "how each name did after it first appeared — the live forward test")
    if len(h):
        d = h[h.status != "BROKE"].groupby("symbol").agg(first=("asof", "min"), scans=("asof", "nunique"),
                                                          c0=("close", "first"), last=("status", "last"))
        d = d.join(bs.close).assign(ret=lambda x: x.close / x.c0 - 1).sort_values(["first", "ret"], ascending=[False, False])
        show(table([dict(sym=f"<b>{esc(i)}</b>", f=esc(r["first"]), n=r.scans, st=status(r["last"]), c0=num(r.c0), c=num(r.close),
                         ret=pct(r.ret, 1)) for i, r in d.iterrows()],
                   [("sym", "symbol"), ("f", "first seen"), ("n", "scans"), ("st", "label then"), ("c0", "price then"),
                    ("c", "now"), ("ret", "since")], numeric={"n", "c0", "c", "ret"}))
