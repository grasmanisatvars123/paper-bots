"""Bot 3: daily mean reversion on SPY (IBS < 0.2, Connors RSI(2) < 10 above the 200-day average). PAPER ONLY.

Rule (rules.b3_step, fixed before testing): each trading day at ~15:46 New York, using the current price as
"today's close": the bot is long SPY while ANY of the three rules is active, flat otherwise. Orders are
market-on-close (Alpaca accepts them until 15:50), so fills are at the official 16:00 close. Long only, no stop
(as in the original rules); position about 98 % of equity, never more.
State (which rules are active) is kept in state/bot3/rules.json; missed days are replayed with daily closes.

Since 2026-11-21 the bot runs in the SAME paper account as the Williams bot (the user's choice). Williams trades SPY
and touches only SPY, so this bot trades VOO (a Vanguard S&P 500 fund that moves like SPY; signals still come from
SPY data). If Williams still holds SPY at 15:46 and uses the buying power, the bot buys what fits at the close and
the rest with a market order at 15:58:40, after Williams has closed.

Since 2026-11-22 a second copy runs in its OWN paper account (the user's choice, workflow bot3_sso.yml): same rules,
but it holds SSO (ProShares Ultra S&P500, 2x the daily move of the S&P 500). Environment: BOT3_SYMBOL=SSO,
BOT3_ID=bot3sso (own state and journal), BOT3_SHARED=0 (no Williams in that account).
"""
import json, logging, os
import numpy as np, pandas as pd
import common as cm, rules
from alpaca import Alpaca, ApiError

TRADE = os.environ.get("BOT3_SYMBOL", "VOO")      # traded fund; SPY is left to the Williams bot in the same account
BOT = os.environ.get("BOT3_ID", "bot3")           # state/<BOT>/, journal title and order ids
SHARED = os.environ.get("BOT3_SHARED", "1") == "1"  # the account is shared with the Williams bot
if TRADE == "SSO":
    NAME, TITLE = "Bots 3 ar SSO (2×)", "Bots 3 SSO"
    FUND = "SSO (2× S&P 500 fonds: kustas divreiz vairāk nekā tirgus)"
else:
    NAME, TITLE = "Bots 3 (dienas atgriešanās)", "Bots 3 Dienas atgriešanās"
    FUND = f"{TRADE} (S&P 500 fonds, kustas tāpat kā SPY)"
OID = "B3S" if BOT != "bot3" else "B3"
WITH = "kopā ar Williams " if SHARED else ""
RULES_FILE = os.path.join(cm.ROOT, "state", BOT, "rules.json")
LABELS = {"IBS": "IBS (slēgums dienas apakšā)", "RSI2": "RSI(2) zem 10", "D7": "zemākais slēgums 7 dienās"}


def daily(api, day):
    """Daily SPY bars (consolidated feed) before `day`, oldest first."""
    b = api.bars_full(cm.SYMBOL, "1Day", pd.Timestamp(day, tz=cm.NY) - pd.Timedelta(days=600), cm.at(day, "00:00"), feed="sip")
    b = b[b.index.strftime("%Y-%m-%d") < day]
    return b


def replay(b, upto_n, st=None):
    """Run the daily rule over bars b[0:upto_n] as if decided at each close (used for start-up and missed days)."""
    st = dict(st or {})
    c, h, l = b.close.values, b.high.values, b.low.values
    for i in range(max(1, upto_n - 300) if not st else 0, upto_n):
        if i < 1: continue
        st = rules.b3_step(st, c[:i], h[:i], l[:i], c[i], h[i], l[i])
    return st


def journal(s):
    st = s.get("rules_after") or {}
    lines = ["## Lēmums", f"- SPY cena lēmuma brīdī: {s.get('price', 0):.2f}",
             f"- Aktīvie noteikumi pēc lēmuma: {', '.join(LABELS[k] for k in st) or 'neviens'}",
             f"- Pozīcija: {s.get('action', '')}"]
    out = ["---", "tags: [trade, paper-bot]", f"date: {s['day']}", "account: paper", f"bot: \"{TITLE}\"", "---",
           f"# {s['day']} {TITLE}", "", f"Noteikums: pērk {TRADE} slēgumā pēc SPY krituma (IBS, RSI(2)) un pārdod "
           f"pēc atgūšanās. Papīra konts {'kopā ar Williams botu' if SHARED else '(atsevišķs)'}, nav īsta nauda.", ""] + lines
    out += ["", "## Piezīmes"] + ([f"- {x}" for x in s["notes"]] or ["- Nav."])
    return "\n".join(out) + "\n"


def auto(api):
    day = cm.now().date().isoformat()
    sess = cm.session(api, day)
    if not sess:
        print(day, "birža slēgta"); return
    s = cm.load(BOT, day)
    if s["done"]:
        print(day, "jau pabeigts"); return
    close = cm.at(day, sess[1])
    decide = close - pd.Timedelta(minutes=14)                     # 15:46 on a normal day
    if cm.now() > close - pd.Timedelta(minutes=11):
        cm.note(s, "Palaišana nokavēja slēguma rīkojumu termiņu (GitHub kavējās). Šodien nekas nemainās.")
        s["done"] = True; cm.save(BOT, s, (TITLE, journal)); return
    try:
        cm.sleep_until(decide)
        b = daily(api, day)
        saved = json.load(open(RULES_FILE)) if os.path.exists(RULES_FILE) else None
        if saved and saved.get("last_day") in set(b.index.strftime("%Y-%m-%d")):
            k = list(b.index.strftime("%Y-%m-%d")).index(saved["last_day"]) + 1
            st = saved["state"]
            for i in range(k, len(b)):                              # days the bot missed
                st = rules.b3_step(st, b.close.values[:i], b.high.values[:i], b.low.values[:i],
                                   b.close.values[i], b.high.values[i], b.low.values[i])
        else:
            st = replay(b, len(b))
            cm.note(s, "Sākuma stāvoklis aprēķināts no pēdējo dienu vēstures.")
        O, H, L, C, V, done = cm.minute_arrays(api, day)
        price = api.latest_price(cm.SYMBOL)
        hi, lo = max(np.nanmax(H[:done]), price), min(np.nanmin(L[:done]), price)
        new = rules.b3_step(st, b.close.values, b.high.values, b.low.values, price, hi, lo)
        s.update(price=price, rules_before=st, rules_after=new)
        pos = api.position(TRADE)
        qty = int(float(pos["qty"])) if pos else 0
        acct = api.account()
        eq = float(acct["equity"])
        tp = api.latest_price(TRADE)
        bp = float(acct.get("buying_power") or 0)                 # Williams may be using it until 15:58
        s["equity"] = eq
        want = int(0.98 * eq / tp) if new else 0
        if new and qty == 0:
            now_qty = min(want, int(0.95 * bp / tp)) if bp > 0 else want
            if now_qty > 0:
                o = api.market_order(TRADE, "buy", now_qty, f"{OID}-{day}-buy", tif="cls"); s["order_id"] = o["id"]
            s["action"] = f"pērk {want} {TRADE} slēgumā"
            cm.message(f"{NAME}, {pd.Timestamp(day):%d.%m.} (mācību konts, nav īsta nauda): šodien PĒRK {FUND} "
                       f"slēgumā (~{cm.usd(want * tp)}), jo cena nokritās ({', '.join(LABELS[k] for k in new)}). "
                       f"Tas turēs, līdz cena atgūsies (parasti 2–7 dienas).")
            if want > now_qty:
                cm.note(s, f"{'Williams vēl izmantoja pirktspēju' if SHARED else 'Nepietika pirktspējas'}: slēgumā {now_qty}, atlikušās {want - now_qty} pērk 15:58:40.")
                cm.save(BOT, s, (TITLE, journal))
                cm.sleep_until(cm.at(day, "15:58") + pd.Timedelta(seconds=40))
                try:
                    o2 = api.market_order(TRADE, "buy", want - now_qty, f"{OID}-{day}-rest")
                    s["rest_order_id"] = o2["id"]
                except ApiError as e:
                    cm.note(s, f"Atlikušo daļu nopirkt neizdevās: {str(e)[:120]}")
        elif not new and qty > 0:
            o = api.market_order(TRADE, "sell", qty, f"{OID}-{day}-sell", tif="cls")
            avg = float(pos["avg_entry_price"])
            s["action"] = f"pārdod {qty} {TRADE} slēgumā"; s["order_id"] = o["id"]
            cm.message(f"{NAME}, {pd.Timestamp(day):%d.%m.} (mācību konts, nav īsta nauda): šodien PĀRDOD {TRADE} slēgumā. "
                       f"Pirka par {avg:.2f}, tagad ap {tp:.2f}: aptuveni {'+' if tp >= avg else '−'}{cm.usd((tp - avg) * qty)}. "
                       f"Kontā {WITH}ap {cm.usd(eq)}.")
        else:
            s["action"] = "tur pozīciju" if qty else "paliek bez pozīcijas"
        cm.note(s, f"Cena {price:.2f}; aktīvi: {', '.join(new) or 'neviens'}; {s['action']}.")
        os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
        json.dump({"last_day": day, "state": new}, open(RULES_FILE, "w"))
        s["done"] = True
    except ApiError as e:
        cm.note(s, f"Alpaca kļūda: {str(e)[:150]}")
        raise
    finally:
        cm.save(BOT, s, (TITLE, journal))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    auto(Alpaca())
