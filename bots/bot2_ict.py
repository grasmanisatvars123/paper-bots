"""Bot 2: ICT "one setup for life" time windows on SPY. PAPER ONLY.

Rule (rules.ict_levels / rules.ict_arm, fixed before testing):
  AM window 09:30-11:00: liquidity = yesterday's PM-session (13:30-16:00) low and high (yesterday's AM-session
    extreme instead if today opens beyond the PM range). PM window 13:30-15:00: today's lunch (12:00-13:30) low/high.
  A sweep of the level, then a 1-minute close through the swing before the extreme with a fair-value gap in that leg
  arms a limit order at the FVG edge for 30 minutes; stop beyond the sweep extreme; target = the opposite level
  (AM: yesterday's PM high/low, PM: the lunch high/low); skipped if the target is less than 1R away.
  One trade per window, out at 15:55. Sizing 1 % of equity at risk, at most 1.5 x equity.
Runs: the morning run watches the AM window; the afternoon run watches the PM window and closes at 15:55.
"""
import logging
import common as cm, rules
from alpaca import Alpaca, ApiError

BOT, NAME, TITLE = "bot2", "Bots 2 (ICT)", "Bots 2 ICT"
RULE = ("Noteikums: rītā (09:30–11:00) vakardienas PM sesijas augstākā/zemākā punkta noņemšana, pēcpusdienā (13:30–15:00) "
        "pusdienu diapazona noņemšana; pēc struktūras maiņas ar FVG limita ieeja FVG malā; mērķis pretējā likviditāte; ārā 15:55.")


def journal(s):
    lines = ["## Līmeņi"] + [f"- {k}: {v}" for k, v in (s.get("levels") or {}).items()]
    return cm.trade_journal(TITLE, RULE, s, lines)


def run_window(api, s, win):
    day = s["day"]
    w0, w1 = rules.WINDOWS[win]
    used = {1: False, -1: False}
    pending = None                                   # (order id, side, MSS minute, stop, target)
    end = cm.at(day, "09:30") + cm.pd.Timedelta(minutes=w1 + 31)
    while cm.now() < end:
        cm.sleep_until(cm.now().floor("1min") + cm.pd.Timedelta(minutes=1, seconds=4))
        O, H, L, C, V, done = cm.minute_arrays(api, day)
        if win == "AM":
            lv = s["levels"]["AM"]
        else:
            if done < 240: continue
            lv = rules.ict_levels(cm.np.array(s["prevH"]), cm.np.array(s["prevL"]), O[0], H, L)[1]
            s["levels"]["PM"] = [round(x, 2) for x in lv]
        if pending:
            oid, side, mss, stop, tgt = pending
            o = api.order(oid)
            f = cm.order_fill(o)
            if f:
                s["trade"] = dict(side=side, entry=f[0], entry_time=f"{f[1]:%H:%M}", stop=stop, target=tgt,
                                  qty=int(float(o["filled_qty"])), order_id=oid, window=win)
                cm.note(s, f"{win}: limita ieeja izpildīta pie {f[0]:.2f}; stops {stop:.2f}, mērķis {tgt:.2f}.")
                cm.message(cm.trade_message(NAME, s))
                return
            after = slice(mss + 1, done)
            touched = done > mss + 1 and ((side == 1 and L[after].min() <= stop) or (side == -1 and H[after].max() >= stop))
            if done >= mss + 31 or touched:
                api.cancel(oid)
                cm.note(s, f"{win}: limita rīkojums atcelts ({'stops aizskarts pirms ieejas' if touched else '30 minūtes pagāja'}).")
                pending = None
            continue
        if done >= w1: continue                       # window over; only a pending order can still fill
        for side, lvl, tgt in ((1, lv[0], lv[1]), (-1, lv[2], lv[3])):
            if used[side]: continue
            arm = rules.ict_arm(H, L, C, w0, w1, lvl, side, done)
            if arm is None: continue
            used[side] = True
            if arm == "no_fvg":
                cm.note(s, f"{win}: struktūras maiņa bez FVG, iestatījums nederīgs."); continue
            i, fvg, stop = arm
            if i < done - 2:
                cm.note(s, f"{win}: iestatījums bija pirms bota palaišanas, izlaists."); continue
            risk = (fvg - stop) * side
            if risk < 0.10 or (tgt - fvg) * side < risk:
                cm.note(s, f"{win}: iestatījums atrasts, bet mērķis par tuvu (mazāk par 1R)."); continue
            qty = cm.size(s["equity"], fvg, stop)
            try:
                o = api.bracket_order(cm.SYMBOL, "buy" if side == 1 else "sell", qty, round(tgt, 2), round(stop, 2),
                                      f"B2-{day}-{win}", limit=round(fvg, 2))
            except ApiError as e:
                cm.note(s, f"{win}: Alpaca nepieņēma rīkojumu ({str(e)[:100]})."); continue
            pending = (o["id"], side, i, stop, tgt)
            cm.note(s, f"{win}: {'pirkšanas' if side == 1 else 'pārdošanas'} limits {qty} pie {fvg:.2f}, stops {stop:.2f}, mērķis {tgt:.2f}.")
            break
        cm.save(BOT, s, (TITLE, journal))
    if pending:
        try: api.cancel(pending[0])
        except ApiError: pass


def auto(api):
    day = cm.now().date().isoformat()
    sess = cm.session(api, day)
    if not sess:
        print(day, "birža slēgta"); return
    s = cm.load(BOT, day, am_done=False, pm_done=False, equity=None, levels={})
    if s["done"]:
        print(day, "jau pabeigts"); return
    if sess[1] != "16:00":
        cm.note(s, "Saīsināta diena, bots netirgo."); s["done"] = True; cm.save(BOT, s, (TITLE, journal)); return
    try:
        if "prevH" not in s:
            prev = cm.prev_session(api, day)
            a = cm.full_session(api, prev)
            s["prevH"], s["prevL"] = a[1].tolist(), a[2].tolist()
            s["equity"] = float(api.account()["equity"])
        if not s["am_done"]:
            if cm.now() < cm.at(day, "10:55"):
                cm.sleep_until(cm.at(day, "09:31"))
                O, H, L, C, V, done = cm.minute_arrays(api, day)
                s["levels"]["AM"] = [round(x, 2) for x in rules.ict_levels(cm.np.array(s["prevH"]), cm.np.array(s["prevL"]), O[0], H, L)[0]]
                run_window(api, s, "AM")
            else:
                cm.note(s, "Rīta logs nokavēts (GitHub kavējās).")
            s["am_done"] = True
        if not s["pm_done"] and cm.now() >= cm.at(day, "12:00"):
            if not s["trade"]:                       # live: at most one trade a day (one account, one SPY position)
                if cm.now() < cm.at(day, "14:55"):
                    cm.sleep_until(cm.at(day, "13:30"))
                    run_window(api, s, "PM")
                else:
                    cm.note(s, "Pēcpusdienas logs nokavēts (GitHub kavējās).")
            s["pm_done"] = True
        if s["am_done"] and s["pm_done"]:
            if s["trade"] and s["trade"].get("exit") is None:
                cm.sleep_until(cm.at(day, "15:55"))
                cm.close_day_trade(api, s)
            s["done"] = True
            cm.message(cm.trade_message(NAME, s))
    finally:
        cm.save(BOT, s, (TITLE, journal))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    auto(Alpaca())
