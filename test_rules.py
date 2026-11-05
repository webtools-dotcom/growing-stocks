"""Self-check for the status + exit rules. Run: python test_rules.py"""
import pandas as pd

from rising import classify, position_alerts


def row(**kw):
    base = dict(streak=3, sales_yoy=.40, pat_yoy=.60, seq_ups=3, sales_yoy_2=.30, opm_chg=2.0,
                above_200=True, off_high=.95, ret1y=.30)
    return base | kw


f = pd.DataFrame([
    row(),                                    # 0 accelerating, confirmed, not run -> EMERGING
    row(ret1y=1.5),                           # 1 already ran -> LEADER
    row(above_200=False, off_high=.75),       # 2 not confirmed -> WAIT
    row(above_200=False, off_high=.50),       # 3 too far below high -> nothing
    row(sales_yoy=.10),                       # 4 growth too slow -> nothing
    row(sales_yoy_2=.60, opm_chg=-1),         # 5 decelerating -> nothing (not LEADER either: ret1y < .8)
    row(above_200=False, ret1y=1.5),          # 6 ran + broken trend -> nothing (WAIT is only for not-yet-run)
])
got = classify(f).fillna("-").tolist()
assert got == ["EMERGING", "LEADER", "WAIT", "-", "-", "-", "-"], got

# the "how it was ranked" panel must always agree with the real label
from rising import explain  # noqa: E402

for i, r in f.assign(close=100.0, dma200=90.0, high52=105.0).iterrows():
    e = explain(r)
    assert (e["label"] or "-") == got[i], (i, e["label"], got[i])
    if e["label"] == "EMERGING":
        assert any("LEADER" in why for _, _, why in e["next"]), e["next"]   # shows the price that makes it a leader
    if e["label"] is None:
        assert e["next"] and "stopped at" in e["next"][0][1], e["next"]   # says which gate it failed

lv = lambda a: [x[0] for x in a]
assert lv(position_alerts(100, "2026-10-01", 95, "2026-11-01", "EMERGING")) == []
assert lv(position_alerts(100, "2026-10-01", 79, "2026-11-01", "EMERGING")) == ["EXIT"]           # 20% stop
assert lv(position_alerts(100, "2026-01-01", 130, "2026-08-01", "LEADER")) == ["REVIEW"]           # 6m, still on board
assert lv(position_alerts(100, "2026-01-01", 130, "2026-08-01", "not on board")) == ["EXIT"]      # 6m, dropped off
q = dict(quarter_end="2026-09-30", sales_yoy=.05, pat_yoy=.40)
assert lv(position_alerts(100, "2026-10-01", 105, "2026-11-01", "EMERGING", q)) == ["WARN"]       # growth slowed

# price adjustment: 2-for-1 split, one-day bad print, and a normal 10% day must all come out right
from data_sync import adjust_prices  # noqa: E402

raw = pd.Series([100, 110, 55, 60.5, 30, 60, 66.0])  # split after day 2, bad print on day 5
a = adjust_prices(raw).round(2).tolist()
assert a[:4] == [50, 55, 55, 60.5], a                 # pre-split prices halved; the split is not a "crash"
assert pd.isna(a[4]) and a[5:] == [60, 66], a         # bad print dropped, the rebound is not a +100% "rally"
print("all rule checks passed")

# AI brief verdict block: parsed out of the answer, and only the allowed scale is kept
from brief import split_verdicts  # noqa: E402

md_, v_ = split_verdicts('```json\n{"business": ["Good", "ok"], "war_risk": ["Excellent", "not on scale"]}\n```\n### In one line\nx')
assert md_.startswith("### In one line") and v_["business"] == ["Good", "ok"] and "war_risk" not in v_, (md_, v_)
print("brief verdict parsing ok")

# freshness banner: never nags after a successful update; holidays and late files don't loop
from rising import freshness  # noqa: E402

T = pd.Timestamp
wed = "2026-11-04"  # board data from Wednesday
assert freshness(wed, {}, T("2026-11-05 12:00"))[0] == "ok"                                   # Thu noon: nothing new yet
assert freshness(wed, {}, T("2026-11-05 19:30"))[0] == "stale"                                # Thu 7:30 pm: Thu file may be out
ok = {"ok": True, "checked_at": "2026-11-05 19:45"}
assert freshness(wed, ok, T("2026-11-05 22:00"))[0] == "ok"                                   # updated, NSE had nothing (holiday)
assert freshness(wed, ok, T("2026-11-06 19:30"))[0] == "stale"                                # next evening: ask again
assert freshness("2026-11-06", {"ok": True, "checked_at": "2026-11-06 20:00"}, T("2026-11-08 10:00"))[0] == "ok"  # weekend
assert freshness(wed, {"ok": False, "checked_at": "2026-11-05 20:00", "error": "x"}, T("2026-11-05 21:00"))[0] == "failed"
print("freshness banner ok")
