"""Read a stock tip pasted from a chat (symbol, CMP, buy range, stop/support, targets) and score it against history:
how often did similar breakouts reach that target before the stop?"""
import re

import numpy as np
import pandas as pd

def _nums(text):
    return [float(x.replace(",", "")) for x in re.findall(r"(?<![\w.])([\d,]+(?:\.\d+)?)", text)]


def parse(text, symbols, names=None):
    """-> dict(symbol, cmp, buy_low, buy_high, stop, targets) from free text like the group messages.
    `symbols`: known NSE symbols; `names`: {symbol: company name} to match 'Engineers India' style mentions."""
    t = text.replace("*", " ").replace("–", "-").replace("—", "-")
    up = t.upper()
    sym = None
    words = re.findall(r"[A-Z][A-Z0-9&\-]{1,19}", up)
    for wd in words:  # an exact NSE symbol in the text
        if wd in symbols and wd not in {"CMP", "SL", "NSE", "BSE", "DCB"}:
            sym = wd
            break
    if sym is None and names:  # company name, e.g. "ENGINEERS INDIA", "PC JEWELLER"
        clean = lambda s: re.sub(r"\b(LIMITED|LTD|INDIA|OF|THE|AND)\b|[^A-Z ]", " ", s.upper()).split()
        head = " ".join(up.splitlines()[:3])
        best = 0
        for s, n in names.items():
            toks = [x for x in clean(n) if len(x) > 2]
            if toks and all(x in head for x in toks) and len(toks) > best:
                sym, best = s, len(toks)
        if sym is None:
            for s, n in names.items():
                toks = [x for x in clean(n) if len(x) > 2]
                if toks and toks[0] in head.split() and len(toks[0]) >= 5:
                    sym = s
                    break
    out = dict(symbol=sym, cmp=None, buy_low=None, buy_high=None, stop=None, targets=[])
    for line in t.splitlines():
        L = line.upper()
        n = _nums(line)
        if not n:
            continue
        if "CMP" in L:
            out["cmp"] = n[0]
        elif re.search(r"STOP|\bSL\b|SUPPORT", L):
            out["stop"] = n[0]
        elif re.search(r"TARGET|UPSIDE|🎯", L):
            out["targets"] = [x for x in n if x > 0]
        elif re.search(r"BUY|RANGE|ZONE", L) and "%" not in L:
            if len(n) >= 2:
                out["buy_low"], out["buy_high"] = sorted(n[:2])
            elif out["buy_low"] is None:
                out["buy_low"] = out["buy_high"] = n[0]
    if out["cmp"] is None and out["buy_low"]:
        out["cmp"] = (out["buy_low"] + out["buy_high"]) / 2
    return out


def odds(events, pattern, target_pct, condition=None):
    """Of past completed breakouts of `pattern` (optionally filtered), the share whose best gain before the stop or
    the horizon reached `target_pct`. -> (share, n)."""
    e = events[(events.pattern == pattern) & events.complete]
    if condition is not None:
        e = e[condition(e)]
    if not len(e):
        return np.nan, 0
    return float((e.maxgain >= target_pct).mean()), len(e)


if __name__ == "__main__":
    syms = {"RAYMOND", "PCJEWELLER", "ENGINERSIN", "GOODLUCK"}
    names = {"RAYMOND": "Raymond Limited", "PCJEWELLER": "PC Jeweller Limited", "ENGINERSIN": "Engineers India Limited"}
    a = parse("*RAYMOND - CMP 1130*\n*SUPPORT - 1060 (DCB)✅*\n*UPSIDE - 1185/1230+🎯*\nSwing trade for week", syms, names)
    assert a["symbol"] == "RAYMOND" and a["cmp"] == 1130 and a["stop"] == 1060 and a["targets"] == [1185, 1230], a
    b = parse("💎ENGINEERS INDIA\n🟢*Buying Range: ₹ 270 – ₹ 300*\n💰Add More: if it dips near ₹ 260-270\n"
              "🎯Upside Potential: ₹ 365 / ₹ 420+\n🛑Stop Loss: ₹ 215 (Weekly Closing Basis)\n⏳Timeframe: 1 Year", syms, names)
    assert b["symbol"] == "ENGINERSIN" and (b["buy_low"], b["buy_high"]) == (270, 300) and b["stop"] == 215 and b["targets"] == [365, 420], b
    c = parse("💎PC JEWELLER – Investment Pick\nOn the verge of an 8-year base breakout\n💰CMP: ₹ 16\n"
              "🛡️*Support: ₹12 (weekly closing)*\n🎯Targets: ₹ 19 / ₹ 22+", syms, names)
    assert c["symbol"] == "PCJEWELLER" and c["cmp"] == 16 and c["stop"] == 12 and c["targets"] == [19, 22], c
    ev = pd.DataFrame(dict(pattern=["base"] * 4, complete=True, maxgain=[.1, .3, .5, .05]))
    assert odds(ev, "base", .25) == (.5, 4)
    print("tips ok")
