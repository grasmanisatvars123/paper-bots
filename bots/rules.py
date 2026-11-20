"""The three bots' rules as pure functions on minute arrays (index 0 = the 09:30 bar, 390 bars a full day).
The backtest (Trading Vault 05 Bot/backtest/bots_bt.py) and the live bots call exactly these functions.
Rules were fixed before testing: Trading Vault 03 Strategies/Three Bots Plan 2026-11-21.md.
"""
import numpy as np

EXIT_MIN = 386          # trades are closed at the 15:55 bar close (index 385)


# ---------------------------------------------------------------- bot 1: value-area re-entry
def value_area(H, L, V, step=0.10, share=0.68):
    """Volume profile of one session (each bar's volume spread evenly over its range): (POC, VAL, VAH)."""
    lo = int(np.floor(np.nanmin(L) / step)); hi = int(np.floor(np.nanmax(H) / step))
    prof = np.zeros(hi - lo + 1)
    for h, l, v in zip(H, L, V):
        a, b = int(np.floor(l / step)) - lo, int(np.floor(h / step)) - lo
        if v > 0: prof[a:b + 1] += v / (b - a + 1)
    poc = int(prof.argmax()); tot = prof.sum(); a = b = poc; acc = prof[poc]
    while acc < share * tot and (a > 0 or b < len(prof) - 1):
        up = prof[b + 1] if b < len(prof) - 1 else -1
        dn = prof[a - 1] if a > 0 else -1
        if up >= dn: b += 1; acc += up
        else: a -= 1; acc += dn
    return (lo + poc) * step, (lo + a) * step, (lo + b + 1) * step


def b1_signal(H, L, C, val, vah, done):
    """First re-entry signal using only the first `done` finished minutes.
    Returns (side, entry_minute, stop, target) or None. side 1: back above VAL after trading below it."""
    below = above = False
    for k in range(min(done, 270) // 5):
        h5, l5, c5 = H[5 * k:5 * k + 5].max(), L[5 * k:5 * k + 5].min(), C[5 * k + 4]
        if l5 < val: below = True
        if h5 > vah: above = True
        i0 = 5 * k + 5
        if below and val < c5 < vah:
            return 1, i0, L[:i0].min(), vah
        if above and val < c5 < vah:
            return -1, i0, H[:i0].max(), val
    return None


# ---------------------------------------------------------------- bot 2: ICT time windows
def ict_arm(H, L, C, w0, w1, level, s, done):
    """Sweep of `level` inside minutes w0..w1-1 (s=1: a low is taken, s=-1: a high is taken), then a close through
    the swing before the extreme with a fair-value gap in that leg. Uses only the first `done` minutes.
    Returns (mss_minute, fvg_price, stop) or None."""
    ext_i = None
    for i in range(w0, min(w1, done)):
        if ext_i is None:
            if (s == 1 and L[i] < level) or (s == -1 and H[i] > level): ext_i = i
            continue
        if (s == 1 and L[i] < L[ext_i]) or (s == -1 and H[i] > H[ext_i]): ext_i = i; continue
        a = max(w0, ext_i - 10)
        ref = H[a:ext_i + 1].max() if s == 1 else L[a:ext_i + 1].min()
        if (s == 1 and C[i] > ref) or (s == -1 and C[i] < ref):
            for j in range(i, ext_i + 1, -1):
                if s == 1 and H[j - 2] < L[j]: return i, L[j], L[ext_i]
                if s == -1 and L[j - 2] > H[j]: return i, H[j], H[ext_i]
            return "no_fvg"
    return None


def ict_levels(prevH, prevL, O0, H, L):
    """(long level, long target, short level, short target) for the AM window and the PM window."""
    pm_hi, pm_lo = prevH[240:].max(), prevL[240:].min()
    am_hi, am_lo = prevH[:150].max(), prevL[:150].min()
    lo_lvl = am_lo if O0 < pm_lo else pm_lo
    hi_lvl = am_hi if O0 > pm_hi else pm_hi
    am = (lo_lvl, pm_hi, hi_lvl, pm_lo)
    pm = (L[150:240].min(), H[150:240].max(), H[150:240].max(), L[150:240].min())
    return am, pm


WINDOWS = {"AM": (0, 90), "PM": (240, 330)}


# ---------------------------------------------------------------- bot 3: daily mean reversion (union of three rules)
def rsi2(closes):
    c = np.asarray(closes, float)
    d = np.diff(c, prepend=c[0]); up = np.clip(d, 0, None); dn = np.clip(-d, 0, None)
    au = ad = 0.0
    for u, w in zip(up, dn):
        au += (u - au) / 2; ad += (w - ad) / 2
    return 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)


def b3_step(state, closes, highs, lows, price, hi_today, lo_today):
    """One daily decision, made with today's price shortly before the close.
    closes/highs/lows: previous sessions' daily values (oldest first). state: dict of active rules
    {"IBS": days_held, "RSI2": days_held, "D7": days_held}. Returns the new state; hold = bool(new state)."""
    st = dict(state)
    x = np.r_[np.asarray(closes[-300:], float), price]
    # exits first (checked with today's price)
    if "IBS" in st:
        st["IBS"] += 1
        if price > highs[-1] or st["IBS"] >= 10: del st["IBS"]
    if "RSI2" in st:
        st["RSI2"] += 1
        if price > x[-5:].mean(): del st["RSI2"]
    if "D7" in st:
        st["D7"] += 1
        if price >= x[-7:].max(): del st["D7"]
    # entries
    ibs = (price - lo_today) / max(hi_today - lo_today, 1e-9)
    if "IBS" not in st and ibs < 0.2:
        st["IBS"] = 0
    if len(x) >= 200 and x[-1] > x[-200:].mean():
        if "RSI2" not in st and rsi2(x[-60:]) < 10: st["RSI2"] = 0
        if "D7" not in st and x[-1] <= x[-7:].min(): st["D7"] = 0
    return st
