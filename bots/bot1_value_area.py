"""Bot 1: value-area re-entry on SPY (order-flow / auction idea from the Fabervaale and Axia videos). PAPER ONLY.

Rule (rules.b1_signal, fixed before testing): yesterday's volume profile gives the value area (68 % of volume).
If today a 5-minute bar closes back inside the value area after price traded outside it, enter at the next bar:
long after trading below VAL (stop = the day's low so far, target = VAH), short after trading above VAH
(stop = the day's high so far, target = VAL). Signal bars until 14:00, one trade a day, out at 15:55.
Sizing: 10 % of equity between entry and stop, position at most 4 x equity. Entry, stop and target are one
bracket order at Alpaca, so the broker watches stop and target.

Runs ("python bot1_value_area.py auto"): the open run watches 09:35-14:00; a later run closes at 15:55.
"""
import logging, sys
import common as cm, rules
from alpaca import Alpaca, ApiError

BOT, NAME, TITLE = "bot1", "Bots 1 (vērtības zona)", "Bots 1 Vērtības zona"
RULE = ("Noteikums: vakardienas apjoma vērtības zona (68 %); ja cena iziet ārā un 5 minūšu svece aizveras atpakaļ iekšā, "
        "ieeja zonas otras malas virzienā; stops dienas galējā punktā; ārā 15:55.")


def journal(s):
    lines = ["## Līmeņi"]
    if s.get("val"):
        lines += [f"- Vakardienas POC {s['poc']:.2f}, vērtības zona {s['val']:.2f} – {s['vah']:.2f}"]
    return cm.trade_journal(TITLE, RULE, s, lines)


def watch(api, s):
    day = s["day"]
    while cm.now() < cm.at(day, "14:01"):
        cm.sleep_until(cm.now().floor("5min") + cm.pd.Timedelta(minutes=5, seconds=5))
        O, H, L, C, V, done = cm.minute_arrays(api, day)
        if done < 5: continue
        sig = rules.b1_signal(H, L, C, s["val"], s["vah"], done)
        if not sig: continue
        side, i0, stop, tgt = sig
        if i0 < done - 5:
            cm.note(s, "Bots sāka par vēlu: šodienas signāls jau bija garām. Darījuma nav.")
            return
        price = api.latest_price(cm.SYMBOL)
        risk = (price - stop) * side
        if risk < 0.10 or (tgt - price) * side <= 0.02:
            cm.note(s, f"Signāls {'pirkšanai' if side == 1 else 'pārdošanai'}, bet cena {price:.2f} jau par tuvu stopam vai mērķim. Darījuma nav.")
            return
        qty = cm.size(s["equity"], price, stop, buying_power=s.get("bp", 0))
        if qty < 1:
            cm.note(s, "Pārāk mazs konts šim riskam. Darījuma nav."); return
        try:
            o = api.bracket_order(cm.SYMBOL, "buy" if side == 1 else "sell", qty, round(tgt, 2), round(stop, 2), f"B1-{day}")
        except ApiError as e:
            cm.note(s, f"Alpaca nepieņēma rīkojumu ({str(e)[:120]}). Darījuma nav."); return
        o = cm.wait_fill(api, o["id"])
        f = cm.order_fill(o)
        if not f:
            api.cancel_all_symbol(cm.SYMBOL)
            cm.note(s, "Ieejas rīkojums neizpildījās minūtes laikā, atcelts."); return
        s["trade"] = dict(side=side, entry=f[0], entry_time=f"{f[1]:%H:%M}", stop=stop, target=tgt,
                          qty=int(float(o["filled_qty"])), order_id=o["id"])
        cm.note(s, f"Ieeja {'pirkt' if side == 1 else 'pārdot'} {qty} pie {f[0]:.2f}; stops {stop:.2f}, mērķis {tgt:.2f}.")
        cm.message(cm.trade_message(NAME, s))
        return
    cm.note(s, f"Līdz 14:00 cena neatgriezās vērtības zonā pēc iziešanas no tās ({s['val']:.2f} – {s['vah']:.2f}).")


def auto(api):
    day = cm.now().date().isoformat()
    sess = cm.session(api, day)
    if not sess:
        print(day, "birža slēgta"); return
    s = cm.load(BOT, day, open_done=False, equity=None)
    if s["done"]:
        print(day, "jau pabeigts"); return
    if sess[1] != "16:00":
        cm.note(s, "Saīsināta diena, bots netirgo."); s["done"] = True; cm.save(BOT, s, (TITLE, journal)); return
    try:
        if not s["open_done"]:
            if cm.now() < cm.at(day, "13:55"):
                cm.sleep_until(cm.at(day, "09:31"))
                prev = cm.prev_session(api, day)
                a = cm.full_session(api, prev)
                s["poc"], s["val"], s["vah"] = rules.value_area(a[1], a[2], a[4])
                acct = api.account()
                s["equity"] = float(acct["equity"])
                s["bp"] = float(acct.get("daytrading_buying_power") or 0)
                cm.note(s, f"Vakardienas vērtības zona {s['val']:.2f} – {s['vah']:.2f} (POC {s['poc']:.2f}).")
                cm.save(BOT, s, (TITLE, journal))
                watch(api, s)
            else:
                cm.note(s, "Rīta palaišana nenotika laikā (GitHub kavējās). Darījuma nav.")
            s["open_done"] = True
            if not s["trade"]:
                s["done"] = True
                cm.message(cm.trade_message(NAME, s))
        if s["trade"] and s["trade"].get("exit") is None:
            if cm.now() >= cm.at(day, "14:00"):
                cm.sleep_until(cm.at(day, "15:55"))
                cm.close_day_trade(api, s)
                s["done"] = True
                cm.message(cm.trade_message(NAME, s))
    finally:
        cm.save(BOT, s, (TITLE, journal))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    auto(Alpaca())
