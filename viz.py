"""Chart builders for the visual panels. One palette: colour only carries meaning (status, good/bad)."""
from html import escape as _esc

import pandas as pd
import plotly.graph_objects as go

C = dict(bg="#0f100d", line="#2a2b24", text="#d6d3c4", dim="#7f7c70", amber="#d9a54b",
         green="#93b874", blue="#7d9db6", red="#cf7462", muted="#3b3d34")
STATUS_C = {"LEADER": C["amber"], "EMERGING": C["green"], "WAIT": C["blue"], "HOT": "#d4805a", "BROKE": C["red"]}
FONT = "JetBrains Mono, monospace"
PLOT = {"displayModeBar": False, "scrollZoom": False}


def chart_layout(fig, h=320):
    fig.update_layout(height=h, margin=dict(l=0, r=0, t=24, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family=FONT, size=11, color=C["dim"]), hovermode="x unified", dragmode=False,
                      hoverlabel=dict(bgcolor="#171813", bordercolor=C["line"], font=dict(family=FONT, size=11)),
                      legend=dict(orientation="h", y=1.08, x=0, font=dict(size=10)), showlegend=True)
    fig.update_xaxes(showgrid=False, linecolor=C["line"], fixedrange=True)
    fig.update_yaxes(gridcolor="#1d1e19", zeroline=False, fixedrange=True)
    return fig


def score_color(s):
    return C["green"] if s >= 70 else C["amber"] if s >= 40 else C["red"]


def trap_radar(scores, h=330):
    """Safety profile: a full shape is safe; every dent is a specific risk."""
    axes = [a for a, *_ in scores] + [scores[0][0]]
    vals = [s for _, s, _ in scores] + [scores[0][1]]
    worst = min(s for _, s, _ in scores)
    fig = go.Figure([
        go.Scatterpolar(r=[100] * len(axes), theta=axes, fill="toself", fillcolor="rgba(127,124,112,.06)",
                        line=dict(color=C["line"], width=1), hoverinfo="skip", showlegend=False),
        go.Scatterpolar(r=vals, theta=axes, fill="toself", fillcolor="rgba(147,184,116,.18)" if worst >= 40 else "rgba(207,116,98,.18)",
                        line=dict(color=score_color(worst), width=1.6), marker=dict(size=7, color=[score_color(v) for v in vals]),
                        customdata=[d for *_, d in scores] + [scores[0][2]],
                        hovertemplate="<b>%{theta}</b>  %{r}/100<br>%{customdata}<extra></extra>", showlegend=False)])
    fig.update_layout(height=h, margin=dict(l=40, r=40, t=20, b=20), paper_bgcolor="rgba(0,0,0,0)",
                      font=dict(family=FONT, size=10, color=C["dim"]), dragmode=False,
                      hoverlabel=dict(bgcolor="#171813", bordercolor=C["line"], font=dict(family=FONT, size=11)),
                      polar=dict(bgcolor="rgba(0,0,0,0)", radialaxis=dict(range=[0, 100], showticklabels=False, gridcolor=C["line"], linecolor=C["line"]),
                                 angularaxis=dict(gridcolor=C["line"], linecolor=C["line"], tickfont=dict(size=10, color=C["text"]))))
    return fig


def capacity_waterfall(b, h=300):
    """Sales today + what the plants under construction could add = potential sales."""
    fig = go.Figure(go.Waterfall(
        x=["sales today<br>(last 12 m)", "plants being built<br>could add", "potential sales"],
        measure=["absolute", "relative", "total"], y=[b["ttm_sales"], b["potential_add"], 0],
        text=[f"₹{b['ttm_sales']:,.0f} Cr", f"+₹{b['potential_add']:,.0f} Cr ({b['potential_pct']:+.0%})",
              f"₹{b['ttm_sales'] + b['potential_add']:,.0f} Cr"], textposition="outside",
        textfont=dict(color=C["text"], size=11),
        increasing=dict(marker=dict(color=C["amber"])), totals=dict(marker=dict(color=C["green"])),
        decreasing=dict(marker=dict(color=C["red"])), connector=dict(line=dict(color=C["line"], dash="dot")),
        hovertemplate="%{x}: ₹%{y:,.0f} Cr<extra></extra>"))
    fig = chart_layout(fig, h).update_layout(showlegend=False, hovermode="closest")
    fig.update_yaxes(tickprefix="₹", range=[0, (b["ttm_sales"] + b["potential_add"]) * 1.18])
    return fig


def order_gauge(book_cr, ttm_sales, h=230):
    """How many years of revenue are already booked."""
    years = book_cr / ttm_sales if ttm_sales else 0
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=years, number=dict(suffix=" yrs", valueformat=".1f", font=dict(color=C["text"], size=30)),
        gauge=dict(axis=dict(range=[0, max(3, years * 1.2)], tickcolor=C["dim"], tickfont=dict(size=9)),
                   bar=dict(color=C["green"] if years >= 1 else C["amber"], thickness=.3), bgcolor="rgba(0,0,0,0)",
                   borderwidth=0, steps=[dict(range=[0, 1], color="#1b1c17"), dict(range=[1, max(3, years * 1.2)], color="#15160f")])))
    fig.update_layout(height=h, margin=dict(l=20, r=20, t=30, b=0), paper_bgcolor="rgba(0,0,0,0)", font=dict(family=FONT, color=C["dim"]))
    return fig


def order_wins(wins, h=230):
    df = pd.DataFrame(wins)
    df["date"] = pd.to_datetime(df.date)
    fig = go.Figure(go.Bar(x=df.date, y=df.value_cr, marker_color=C["green"], customdata=df.text,
                           hovertemplate="%{x|%d %b %Y}: ₹%{y:,.0f} Cr<br>%{customdata}<extra></extra>"))
    fig = chart_layout(fig, h).update_layout(showlegend=False, hovermode="closest", bargap=.6)
    fig.update_yaxes(tickprefix="₹")
    return fig


def margin_band_chart(df, h=260):
    """Operating margin vs the range of its previous 12 quarters (shaded)."""
    x = list(df.index)
    fig = go.Figure([
        go.Scatter(x=x, y=df.hi, mode="lines", line=dict(width=0), hoverinfo="skip", showlegend=False),
        go.Scatter(x=x, y=df.lo, mode="lines", line=dict(width=0), fill="tonexty", fillcolor="rgba(127,124,112,.14)",
                   name="its usual range (prior 3 yrs)", hoverinfo="skip"),
        go.Scatter(x=x, y=df.opm, mode="lines+markers", name="operating margin", line=dict(color=C["amber"], width=1.6),
                   marker=dict(size=6, color=[C["green"] if (pd.notna(h_) and v > h_) else C["red"] if (pd.notna(l_) and v < l_) else C["amber"]
                                              for v, h_, l_ in zip(df.opm, df.hi, df.lo)]),
                   hovertemplate="%{x}: %{y:.1f}%<extra></extra>")])
    fig = chart_layout(fig, h)
    fig.update_yaxes(ticksuffix="%")
    fig.update_xaxes(tickangle=0, nticks=6)
    return fig


def pe_chart(pe, h=260):
    """P/E through time: is today's price asking more than usual for each rupee of profit?"""
    med = pe.median()
    now = pe.iloc[-1]
    fig = go.Figure([
        go.Scatter(x=pe.index, y=pe.values, name="P/E", line=dict(color=C["text"], width=1.3),
                   hovertemplate="%{x|%d %b %Y}: P/E %{y:.0f}<extra></extra>"),
        go.Scatter(x=[pe.index[-1]], y=[now], mode="markers+text", text=[f" {now:.0f}"], textposition="middle right",
                   marker=dict(size=8, color=C["red"] if now > med * 1.3 else C["green"] if now < med * .85 else C["amber"]),
                   textfont=dict(color=C["text"]), showlegend=False, hoverinfo="skip")])
    fig.add_hline(y=med, line=dict(color=C["dim"], dash="dot", width=1), annotation_text=f"its usual P/E {med:.0f}",
                  annotation_position="bottom left", annotation_bgcolor="#0f100d",  # readable over the line
                  annotation_font=dict(color=C["text"], size=10))
    fig = chart_layout(fig, h).update_layout(showlegend=False, margin=dict(l=0, r=40, t=24, b=0))
    hi = pe.quantile(.98)
    fig.update_yaxes(range=[0, max(hi, now) * 1.1])
    return fig


def ownership_change_bars(ch, h=260):
    """Quarter-on-quarter change in who owns the company (percentage points)."""
    colors = {"Promoters": C["amber"], "FIIs": C["blue"], "DIIs": C["green"], "Public": C["dim"]}
    cols = list(ch.columns)[-6:]
    fig = go.Figure([go.Bar(x=cols, y=ch.loc[k, cols], name=k.lower(), marker_color=colors.get(k, C["dim"]),
                            hovertemplate="%{x}: %{y:+.2f} pts<extra>" + k + "</extra>") for k in ch.index])
    fig = chart_layout(fig, h).update_layout(barmode="group", bargap=.3)
    fig.update_yaxes(ticksuffix=" pts", zeroline=True, zerolinecolor=C["dim"])
    return fig


def themes_treemap(b, h=520):
    """Industries sized by how many rising companies they hold, coloured by 3-month price momentum."""
    d = b[b.status.isin(["LEADER", "EMERGING", "WAIT", "HOT"])].copy()
    d["industry"] = d.industry.fillna("unclassified")
    d["w"] = d.status.map({"LEADER": 3, "EMERGING": 3, "HOT": 3, "WAIT": 1})
    d["ret3m"] = d.ret3m.clip(-.5, 1) * 100
    d["label"] = d.symbol + "<br>" + d.status.str.lower()
    fig = go.Figure(go.Treemap(
        labels=list(d.label) + list(d.industry.unique()),
        parents=list(d.industry) + [""] * d.industry.nunique(),
        values=list(d.w) + [0] * d.industry.nunique(),
        marker=dict(colors=list(d.ret3m) + [None] * d.industry.nunique(), colorscale=[[0, C["red"]], [.33, "#2a2b24"], [1, C["green"]]],
                    cmid=0, line=dict(color=C["bg"], width=1), showscale=False),
        customdata=list(zip(d.symbol, d.ret3m.round(0), (d.sales_yoy * 100).round(0))) + [("", None, None)] * d.industry.nunique(),
        hovertemplate="<b>%{label}</b><br>3m %{customdata[1]:+}% · sales %{customdata[2]:+}% yoy<extra></extra>",
        textfont=dict(family=FONT, color=C["text"]), branchvalues="remainder", tiling=dict(pad=2)))
    fig.update_layout(height=h, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)",
                      hoverlabel=dict(bgcolor="#171813", bordercolor=C["line"], font=dict(family=FONT, size=11)))
    return fig


def scoreboard(rows, h=300):
    """Median 6-month return of each label, per results season, vs everything else."""
    df = pd.DataFrame(rows)
    colors = {"LEADER": C["amber"], "EMERGING": C["green"], "WAIT": C["blue"], "HOT": STATUS_C["HOT"], "everything else": C["muted"]}
    fig = go.Figure([go.Bar(x=g.on.astype(str), y=g["median"] * 100, name=k.lower(), marker_color=colors.get(k, C["dim"]),
                            customdata=g.n, hovertemplate="%{x}: median %{y:+.0f}% · %{customdata} stocks<extra>" + k.lower() + "</extra>")
                     for k, g in df.groupby("status", sort=False)])
    fig = chart_layout(fig, h).update_layout(barmode="group", bargap=.25)
    fig.update_yaxes(ticksuffix="%", zeroline=True, zerolinecolor=C["dim"])
    fig.update_xaxes(title=dict(text="bought at each results season, held 6 months", font=dict(size=10)))
    return fig


def compare_radar(metrics, h=380):
    """metrics: {symbol: {axis: score 0..100}}"""
    palette = [C["amber"], C["green"], C["blue"]]
    fig = go.Figure()
    for (sym, m), col in zip(metrics.items(), palette):
        axes = list(m) + [list(m)[0]]
        vals = list(m.values()) + [list(m.values())[0]]
        fig.add_trace(go.Scatterpolar(r=vals, theta=axes, name=sym, fill="toself", line=dict(color=col, width=1.6),
                                      opacity=.85, hovertemplate=f"<b>{sym}</b> %{{theta}}: %{{r:.0f}}/100<extra></extra>"))
    fig.update_layout(height=h, margin=dict(l=40, r=40, t=30, b=40), paper_bgcolor="rgba(0,0,0,0)", dragmode=False,
                      font=dict(family=FONT, size=10, color=C["dim"]),
                      legend=dict(orientation="h", y=-.08, x=.5, xanchor="center", yanchor="top"),  # top legend hit the "margin" label
                      polar=dict(bgcolor="rgba(0,0,0,0)", radialaxis=dict(range=[0, 100], showticklabels=False, gridcolor=C["line"]),
                                 angularaxis=dict(gridcolor=C["line"], tickfont=dict(size=10, color=C["text"]))))
    return fig


def compare_lines(series, h=300, suffix=""):
    """series: {symbol: pd.Series indexed by label}, already indexed to 100 or in common units."""
    palette = [C["amber"], C["green"], C["blue"]]
    fig = go.Figure([go.Scatter(x=list(s.index), y=s.values, name=sym, mode="lines+markers", line=dict(color=col, width=1.6),
                                marker=dict(size=5), hovertemplate=f"{sym} %{{x}}: %{{y:.0f}}{suffix}<extra></extra>")
                     for (sym, s), col in zip(series.items(), palette)])
    fig = chart_layout(fig, h)
    fig.update_xaxes(tickangle=0, nticks=8)
    return fig


# ------------------------------------------------------------------ "how it was ranked" panels
def _bar_pos(t):
    """Scale for one gate test's bullet bar -> (fill %, threshold %). Threshold sits mid-bar where it can."""
    v, need, fmt = t["value"], t["need"], t["fmt"]
    if v is None or pd.isna(v):
        return 0, 50
    if t["name"].startswith("sales growth now"):  # compared against a moving bar (growth 2 quarters ago)
        need = need if need is not None and pd.notna(need) else 0
        lo, hi = min(0, v, need), max(v, need, 1e-9) * 1.15
    elif fmt == "n":
        lo, hi = 0, 3
    elif need > 0:
        lo, hi = 0, 2 * need
    elif need < 0:
        lo, hi = 2 * need, 0
    else:
        span = max(abs(v), .3 if fmt == "pct" else 5)
        lo, hi = -span, span
    pos = lambda x: max(0, min(100, (x - lo) / (hi - lo) * 100)) if hi > lo else 50
    return pos(v), pos(need if need is not None and pd.notna(need) else 0)


def fmt_val(x, fmt):
    if x is None or pd.isna(x):
        return "–"
    return f"{x:+.0%}" if fmt == "pct" else f"{x:+.1f} pts" if fmt == "pts" else f"{x:.0f}"


def gate_path_html(e):
    """The decision path as connected gate cards: each test is a bullet bar (value fill, threshold tick)."""
    cards = []
    for n, g in enumerate(e["gates"], 1):
        state = "info" if g["passed"] is None else "pass" if g["passed"] else "fail"
        mark = {"pass": "✓", "fail": "✗", "info": "↔"}[state]
        rows = []
        for t in g["tests"]:
            fill, tick = _bar_pos(t)
            if g["passed"] is None:  # the run-up gate splits, it doesn't pass/fail
                need = f"{'too hot' if 'hot' in t['name'] else 'leader'} above {fmt_val(t['need'], t['fmt'])}"
            elif t["need"] is None or pd.isna(t["need"]):
                need = ""
            elif t["fmt"] == "n":
                need = f"needs ≥{t['need']:.0f}"
            else:
                need = f"needs &gt;{fmt_val(t['need'], t['fmt'])}"
            ok = "info" if g["passed"] is None else "pass" if t["passed"] else "fail"
            rows.append(f'<div class="gt"><div class="gn" title="{_esc(t["hint"])}">{t["name"]}</div>'
                        f'<div class="gb"><div class="gf {ok}" style="width:{fill:.0f}%"></div>'
                        f'<div class="gk" style="left:{tick:.0f}%"></div></div>'
                        f'<div class="gv"><b class="{ok}">{fmt_val(t["value"], t["fmt"])}</b> <span>{need}</span></div></div>')
        cards.append(f'<div class="gate {state}"><div class="gh"><span class="gnum">{n}</span>{g["name"]}'
                     f'<span class="gm {state}">{mark}</span></div><div class="gr">{g["rule"]}</div>{"".join(rows)}</div>')
    label = e["label"] or "NOT ON BOARD"
    col = STATUS_C.get(e["label"], C["dim"])
    arrow = '<div class="garrow">→</div>'
    return (f'<div class="gates">{arrow.join(cards)}{arrow}'
            f'<div class="gres" style="border-color:{col};color:{col}">{label}</div></div>')


def score_waterfall(row, rank, n_label, h=270):
    """How the steadiness score that orders the board is built: each part is a rank (0..25) among board stocks."""
    parts = [("size", row["s_size"], "bigger company = higher · small caps fell hardest in 2024-25"),
             ("calm price", row["s_calm"], "smaller daily swings over 60 days = higher · frantic stocks hit stops more"),
             ("accumulation", row["s_accum"], "delivery % last month vs last 6 months · rising = shares being taken home"),
             ("margin change", row["s_margin"], "operating margin vs a year ago · widening = higher")]
    fig = go.Figure(go.Waterfall(
        orientation="h", y=[p[0] for p in parts] + ["score"], x=[p[1] for p in parts] + [0],
        measure=["relative"] * len(parts) + ["total"],
        text=[f"{p[1]:.0f}" for p in parts] + [f"{row['score']:.0f}"], textposition="outside",
        customdata=[p[2] for p in parts] + [f"#{rank} of {n_label} in its label · best possible 100"],
        hovertemplate="%{y}: %{x:.0f}<br>%{customdata}<extra></extra>",
        increasing=dict(marker=dict(color=C["green"])), decreasing=dict(marker=dict(color=C["red"])),
        totals=dict(marker=dict(color=C["amber"])), connector=dict(line=dict(color=C["line"], dash="dot"))))
    fig = chart_layout(fig, h).update_layout(showlegend=False, hovermode="closest", margin=dict(l=0, r=40, t=10, b=0))
    fig.update_yaxes(autorange="reversed", tickfont=dict(color=C["text"]))
    fig.update_xaxes(range=[0, 108], showgrid=True, gridcolor="#1d1e19")
    return fig


CROWD = [("sales growth", "sales_yoy", "pct", .20, (-.5, 2)), ("profit growth", "pat_yoy", "pct", .25, (-1, 3)),
         ("margin change", "opm_chg", "pts", 0, (-15, 15)), ("price vs 200-day avg", "vs_dma", "pct", 0, (-.5, .8)),
         ("from 52-week high", "from_high", "pct", -.15, (-.7, 0)), ("1-year run", "ret1y", "pct", .80, (-.6, 3))]


def crowd_strips(u, me, h=520):
    """Every scanned stock as a faint dot per metric; this stock bright; dotted line = the rule's threshold."""
    import numpy as np
    from plotly.subplots import make_subplots
    u = u.assign(vs_dma=u.close / u.dma200 - 1, from_high=u.off_high - 1)
    me = dict(me)
    me["vs_dma"] = me["close"] / me["dma200"] - 1 if me.get("dma200") else None
    me["from_high"] = me["off_high"] - 1 if me.get("off_high") is not None else None
    fig = make_subplots(rows=len(CROWD), cols=1, vertical_spacing=.085, subplot_titles=[c[0] for c in CROWD])
    rng = np.random.default_rng(7)
    for i, (title, col, fmt, need, (lo, hi)) in enumerate(CROWD, 1):
        vals = u[col].dropna()
        fig.add_trace(go.Scatter(x=vals.clip(lo, hi), y=rng.uniform(-1, 1, len(vals)), mode="markers", hoverinfo="skip",
                                 showlegend=False, marker=dict(size=3, color=C["dim"], opacity=.3)), i, 1)
        v = me.get(col)
        if v is not None and pd.notna(v):
            beats = (vals < v).mean()
            off = " →" if v > hi else " ←" if v < lo else ""  # beyond the axis: pinned to the edge, arrow says so
            fig.add_trace(go.Scatter(x=[min(max(v, lo), hi)], y=[0], mode="markers+text", showlegend=False,
                                     marker=dict(size=12, color=C["amber"], line=dict(color=C["bg"], width=1.5)),
                                     text=[f"  {fmt_val(v, fmt)}{off} · beats {beats:.0%}  "],
                                     textposition="middle left" if v > lo + .75 * (hi - lo) else "middle right",
                                     textfont=dict(color=C["text"], size=10), hoverinfo="skip"), i, 1)
        fig.add_vline(x=need, row=i, col=1, line=dict(color=C["blue"], width=1, dash="dot"))
        pad = .03 * (hi - lo)  # room for a dot pinned at either edge
        fig.update_xaxes(range=[lo - pad, hi + pad], tickformat=".0%" if fmt == "pct" else None, ticksuffix="" if fmt == "pct" else " pts",
                         row=i, col=1, showgrid=False, tickfont=dict(size=9), fixedrange=True)
        fig.update_yaxes(visible=False, range=[-1.6, 1.6], row=i, col=1, fixedrange=True)
    fig.update_annotations(font=dict(size=10, color=C["text"]), x=0, xanchor="left", yshift=2)
    fig.update_layout(height=h, margin=dict(l=0, r=0, t=22, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family=FONT, size=10, color=C["dim"]), dragmode=False)
    return fig


def universe_funnel(fn, h=340):
    lab = fn["labels"]
    stages = [("listed on NSE", fn["listed"]), ("results in the last 6 months", fn["recent"]),
              ("profitable · Rs 25 Cr+ quarterly sales · Rs 1 Cr+ traded a day", fn["scanned"]),
              ("growth gate: strong, consistent growth", fn["growth"]), ("+ growth accelerating", fn["accel"]),
              ("labelled: leader / emerging / wait / hot", sum(lab.values())),
              ("buy-list: trend confirmed (leader + emerging)", lab.get("LEADER", 0) + lab.get("EMERGING", 0))]
    fig = go.Figure(go.Funnel(
        y=[s for s, _ in stages], x=[n for _, n in stages], textinfo="value+percent initial",
        marker=dict(color=[C["muted"]] * 3 + [C["blue"], C["blue"], C["amber"], C["green"]]),
        connector=dict(line=dict(color=C["line"], width=1)), textfont=dict(color=C["text"], family=FONT),
        hovertemplate="%{y}<br>%{x:,} stocks · %{percentInitial:.1%} of all listed<extra></extra>"))
    fig.update_layout(height=h, margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family=FONT, size=11, color=C["dim"]), dragmode=False)
    fig.update_yaxes(tickfont=dict(color=C["text"], size=10))
    return fig


def label_timeline(hist, h=150):
    """When this stock carried which label, at what price."""
    order = {"WAIT": 1, "EMERGING": 2, "LEADER": 3, "HOT": 4, "BROKE": 0}
    hist = hist.assign(asof=pd.to_datetime(hist["asof"]), y=hist.status.map(order))
    fig = go.Figure(go.Scatter(x=hist["asof"], y=hist.y, mode="lines+markers", line=dict(color=C["line"], width=1, shape="hv"),
                               marker=dict(size=10, color=[STATUS_C.get(s, C["dim"]) for s in hist.status]),
                               customdata=list(zip(hist.status.str.lower(), hist.close)),
                               hovertemplate="%{x|%d %b %Y}: %{customdata[0]} at Rs %{customdata[1]:,.1f}<extra></extra>"))
    fig = chart_layout(fig, h).update_layout(showlegend=False, hovermode="closest", margin=dict(l=0, r=0, t=6, b=0))
    fig.update_yaxes(tickvals=[0, 1, 2, 3, 4], ticktext=["broke", "wait", "emerging", "leader", "hot"], range=[-.5, 4.5])
    fig.update_xaxes(tickformat="%d %b")  # one line: the default two-line date label was clipped
    return fig


VERDICT_C = {"Best": "#7fc46a", "Good": C["green"], "Average": C["amber"], "Bad": "#d98c4b", "Worse": C["red"], "Critical": "#e0473a"}


def verdict_card_html(v, rules=None):
    """Each verdict as a 6-step meter (Best -> Critical) with its reason. `rules` (the free rule-based verdicts) is
    shown beside each AI verdict as a cross-check; ≠ marks where they disagree."""
    scale = list(VERDICT_C)
    rows = [("business", "business"), ("sector outlook", "sector_outlook"), ("war & oil risk", "war_risk"),
            ("before buying", "checks"), ("overall", "overall")]
    out = []
    for label, key in rows:
        if key not in v:
            continue
        verdict, reason = v[key]
        idx = scale.index(verdict)
        meter = "".join(f'<i class="{"on" if i == idx else ""}" style="background:{VERDICT_C[s] if i <= idx else "#1f201a"}'
                        f'{";opacity:.35" if i < idx else ""}"></i>' for i, s in enumerate(scale))
        cross = ""
        if rules and key in rules:
            rv = rules[key][0]
            diff = abs(scale.index(rv) - idx) >= 1
            cross = (f'<span class="xr" title="rule-based: {_esc(rules[key][1])}">{"≠ " if diff else ""}rules: '
                     f'<b style="color:{VERDICT_C[rv]}">{rv.lower()}</b></span>')
        cls = ' class="vall"' if key == "overall" else ""
        out.append(f'<div{cls}><span class="vl">{label}</span></div><div{cls}><div class="vm">{meter}</div></div>'
                   f'<div{cls}><span class="vr"><b style="color:{VERDICT_C[verdict]}">{verdict}</b>{_esc(reason)}</span>{cross}</div>')
    legend = '<div class="note" style="margin-top:.5rem">scale: best · good · average · bad · worse · critical — '              'for war &amp; oil risk, best = almost no exposure, critical = very likely to fall sharply on escalation</div>'
    return f'<div class="vc">{"".join(out)}</div>{legend}'


def sandbox_compare(base, mine, h=300):
    """Median 6-month return per label: current rules vs your rules (averaged over seasons), with sample sizes."""
    labels = ["LEADER", "EMERGING", "WAIT", "HOT", "everything else"]
    agg = lambda d: d.groupby("status").agg(median=("median", "mean"), n=("n", "mean")).reindex(labels)
    a, b = agg(base), agg(mine)
    fig = go.Figure([
        go.Bar(x=[l.lower() for l in labels], y=a["median"] * 100, name="current rules", marker_color=C["muted"],
               text=[f"n≈{n:.0f}" if pd.notna(n) else "" for n in a.n], textposition="outside", textfont=dict(size=9),
               hovertemplate="%{x}: median %{y:+.1f}%<extra>current rules</extra>"),
        go.Bar(x=[l.lower() for l in labels], y=b["median"] * 100, name="your rules",
               marker_color=[STATUS_C.get(l, C["dim"]) for l in labels],
               text=[f"n≈{n:.0f}" if pd.notna(n) else "" for n in b.n], textposition="outside", textfont=dict(size=9),
               hovertemplate="%{x}: median %{y:+.1f}%<extra>your rules</extra>")])
    fig = chart_layout(fig, h).update_layout(barmode="group", bargap=.3, hovermode="closest")
    fig.update_yaxes(ticksuffix="%", zeroline=True, zerolinecolor=C["dim"])
    return fig


def panic_bars(p, h=150):
    """Average move on the market's worst days: the market vs this stock."""
    fig = go.Figure(go.Bar(
        y=["market", "this stock"], x=[p["market_avg"] * 100, p["stock_avg"] * 100], orientation="h",
        marker_color=[C["muted"], C["red"] if p["ratio"] > 1.2 else C["amber"] if p["ratio"] > .9 else C["green"]],
        text=[f"{p['market_avg']:+.1%}", f"{p['stock_avg']:+.1%} ({p['ratio']:.1f}×)"], textposition="inside",
        insidetextanchor="middle", textfont=dict(color=[C["text"], "#0f0f0c"], size=11),  # outside labels collided with the axis on narrow screens
        hovertemplate="%{y}: %{x:.1f}% average<extra>on the " + str(p["days"]) + " worst market days</extra>"))
    fig = chart_layout(fig, h).update_layout(showlegend=False, hovermode="closest", margin=dict(l=0, r=10, t=4, b=0))
    fig.update_xaxes(ticksuffix="%", range=[min(p["market_avg"], p["stock_avg"]) * 100 * 1.1, 0])
    fig.update_yaxes(tickfont=dict(color=C["text"]))
    return fig
