"""Chart builders for the visual panels. One palette: colour only carries meaning (status, good/bad)."""
import pandas as pd
import plotly.graph_objects as go

C = dict(bg="#0f100d", line="#2a2b24", text="#d6d3c4", dim="#7f7c70", amber="#d9a54b",
         green="#93b874", blue="#7d9db6", red="#cf7462", muted="#3b3d34")
STATUS_C = {"LEADER": C["amber"], "EMERGING": C["green"], "WAIT": C["blue"], "BROKE": C["red"]}
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
                  annotation_position="top left", annotation_font=dict(color=C["dim"], size=10))
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
    d = b[b.status.isin(["LEADER", "EMERGING", "WAIT"])].copy()
    d["industry"] = d.industry.fillna("unclassified")
    d["w"] = d.status.map({"LEADER": 3, "EMERGING": 3, "WAIT": 1})
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
    colors = {"LEADER": C["amber"], "EMERGING": C["green"], "WAIT": C["blue"], "everything else": C["muted"]}
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
    fig.update_layout(height=h, margin=dict(l=40, r=40, t=30, b=20), paper_bgcolor="rgba(0,0,0,0)", dragmode=False,
                      font=dict(family=FONT, size=10, color=C["dim"]), legend=dict(orientation="h", y=1.1, x=0),
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
