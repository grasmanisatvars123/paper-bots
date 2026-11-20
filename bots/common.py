"""Shared helpers for the three paper bots: New York time, sessions, minute arrays, state, journal, phone message.
PAPER ONLY: alpaca.py refuses any account that is not a paper account."""
import json, logging, os, time
import numpy as np, pandas as pd

NY = "America/New_York"
SYMBOL = "SPY"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log = logging.getLogger("bots")


def now():
    return pd.Timestamp.now(tz=NY)


def at(day, hhmm):
    return pd.Timestamp(f"{day} {hhmm}", tz=NY)


def sleep_until(t):
    while now() < t:
        time.sleep(max(1.0, min(30.0, (t - now()).total_seconds())))


def lv_time(t):
    return pd.Timestamp(t).tz_convert("Europe/Riga").strftime("%H:%M")


def usd(x):
    return f"{abs(x):,.0f}".replace(",", " ") + " $"


def session(api, day):
    """(open 'HH:MM', close 'HH:MM') of the session on `day`, or None if the market is closed."""
    cal = api.calendar(day)
    if not cal or cal[0]["date"] != day:
        return None
    return cal[0]["open"], cal[0]["close"]


def prev_session(api, day):
    d = pd.Timestamp(day)
    cal = api.calendar_range((d - pd.Timedelta(days=10)).date(), (d - pd.Timedelta(days=1)).date())
    return cal[-1]["date"] if cal else None


def minute_arrays(api, day, feed="iex"):
    """O, H, L, C, V (390 each, index 0 = 09:30) and the number of finished minutes so far."""
    t = now()
    done = int(min(390, max(0, (t - at(day, "09:30")).total_seconds() // 60)))
    a = np.full((5, 390), np.nan)
    if done > 0:
        end = min(t, at(day, "16:00")) if feed == "iex" else at(day, "16:00")
        b = api.bars_full(SYMBOL, "1Min", at(day, "09:30"), end, feed=feed)
        if len(b):
            m = ((b.index - at(day, "09:30")).total_seconds() // 60).astype(int)
            ok = (m >= 0) & (m < done)
            for k, c in enumerate(("open", "high", "low", "close", "volume")):
                a[k, m[ok]] = b[c].values[ok]
    first = np.where(np.isfinite(a[3, :max(done, 1)]))[0]
    if len(first):
        last = pd.Series(a[3, :done]).ffill().bfill().values
        miss = ~np.isfinite(a[3, :done])
        for k in range(4): a[k, :done][miss] = last[miss]
        a[4, :done][miss] = 0.0
    return a[0], a[1], a[2], a[3], a[4], done


def full_session(api, day):
    """A finished session from the consolidated (SIP) feed, with volume."""
    b = api.bars_full(SYMBOL, "1Min", at(day, "09:30"), at(day, "16:00"), feed="sip")
    a = np.full((5, 390), np.nan)
    m = ((b.index - at(day, "09:30")).total_seconds() // 60).astype(int)
    ok = (m >= 0) & (m < 390)
    for k, c in enumerate(("open", "high", "low", "close", "volume")):
        a[k, m[ok]] = b[c].values[ok]
    last = pd.Series(a[3]).ffill().bfill().values
    miss = ~np.isfinite(a[3])
    for k in range(4): a[k, miss] = last[miss]
    a[4, miss] = 0.0
    return a


def size(equity, entry, stop, risk_pct=0.01, max_notional=1.5):
    risk = abs(entry - stop)
    return int(min(risk_pct * equity / max(risk, 1e-9), max_notional * equity / entry))


# ---------------------------------------------------------------- state, journal, message
def state_path(bot, day):
    return os.path.join(ROOT, "state", bot, f"{day}.json")


def load(bot, day, **defaults):
    s = dict(day=day, notes=[], trade=None, done=False, **defaults)
    p = state_path(bot, day)
    if os.path.exists(p):
        s.update(json.load(open(p, encoding="utf-8")))
    return s


def save(bot, s, journal_fn=None):
    p = state_path(bot, s["day"])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(s, open(p + ".tmp", "w", encoding="utf-8"), indent=1)
    os.replace(p + ".tmp", p)
    if journal_fn:
        os.makedirs(os.path.join(ROOT, "journal"), exist_ok=True)
        with open(os.path.join(ROOT, "journal", f"{s['day']} {journal_fn[0]}.md"), "w", encoding="utf-8") as f:
            f.write(journal_fn[1](s))


def note(s, text):
    log.info(text)
    s["notes"].append(f"{now():%H:%M} {text}")


def message(text):
    """Queue one plain-language line for the phone (the owner's private repo sends it)."""
    if os.environ.get("MSG_FILE"):
        with open(os.environ["MSG_FILE"], "a", encoding="utf-8") as f:
            f.write(text.strip() + "\n")
    print(text)


def order_fill(o):
    """(price, NY time) of a filled order, else None."""
    if o and o.get("status") in ("filled", "partially_filled") and o.get("filled_avg_price"):
        return float(o["filled_avg_price"]), pd.Timestamp(o["filled_at"]).tz_convert(NY)
    return None


def wait_fill(api, oid, seconds=60):
    o = None
    for _ in range(seconds):
        o = api.order(oid)
        if o.get("status") == "filled": break
        time.sleep(1)
    return o


def close_day_trade(api, s):
    """At 15:55: cancel the bracket's open legs and close what is left; record how the trade ended."""
    t = s["trade"]
    o = api.order(t["order_id"])
    legs = o.get("legs") or []
    for leg in legs:                                    # a leg that already filled = target or stop hit
        f = order_fill(leg)
        if f:
            t.update(exit=f[0], exit_time=f"{f[1]:%H:%M}", how="mērķis" if leg.get("type") == "limit" else "stops")
            break
    api.cancel_all_symbol(SYMBOL)
    pos = api.position(SYMBOL)
    if pos and float(pos["qty"]) != 0:
        c = api.close_position(SYMBOL)
        c = wait_fill(api, c["id"], 60)
        f = order_fill(c) or (api.latest_price(SYMBOL), now())
        t.update(exit=f[0], exit_time=f"{f[1]:%H:%M}", how="slēgums 15:55")
    if t.get("exit") is not None:
        t["pnl"] = (t["exit"] - t["entry"]) * t["side"] * t["qty"]


def trade_journal(title, rule, s, extra_lines=()):
    t = s["trade"]
    out = ["---", "tags: [trade, paper-bot]", f"date: {s['day']}", "account: paper", f"bot: \"{title}\"", "---",
           f"# {s['day']} {title}", "", rule + " Papīra konts, nav īsta nauda.", ""]
    out += list(extra_lines)
    out += ["", "## Darījums"]
    if t:
        out += ["| Virziens | Ieeja | Laiks | Stops | Mērķis | Izeja | Laiks | Kā | P&L $ |", "|---|---|---|---|---|---|---|---|---|",
                f"| {'pirkt' if t['side'] == 1 else 'pārdot'} | {t['entry']:.2f} | {t['entry_time']} | {t['stop']:.2f} | "
                f"{t['target']:.2f} | " + (f"{t['exit']:.2f} | {t['exit_time']} | {t['how']} | {t['pnl']:.2f} |"
                                          if t.get("exit") is not None else "atvērts | | | |")]
    else:
        out.append("Nav darījuma.")
    out += ["", "## Piezīmes"] + ([f"- {x}" for x in s["notes"]] or ["- Nav."])
    return "\n".join(out) + "\n"


def trade_message(name, s):
    t, d = s["trade"], s["day"]
    head = f"{name}, {pd.Timestamp(d):%d.%m.} (mācību konts, nav īsta nauda):"
    if t and t.get("exit") is not None:
        eq = float(s.get("equity") or 0) + t["pnl"]
        b = t["side"] == 1
        return (f"{head} šodien {'NOPELNĪJA' if t['pnl'] >= 0 else 'ZAUDĒJA'} {usd(t['pnl'])}. Tas {'nopirka' if b else 'pārdeva'} "
                f"SPY par {t['entry']:.2f} un {'pārdeva' if b else 'atpirka'} par {t['exit']:.2f}. Kontā tagad ap {usd(eq)}.")
    if t:
        what = "NOPIRKA SPY (gaida, ka cena celsies)" if t["side"] == 1 else "PĀRDEVA SPY (gaida, ka cena kritīsies)"
        return (f"{head} {what} par {t['entry']:.2f}. Ja cena aizies līdz {t['stop']:.2f}, tas izies ar zaudējumu ap "
                f"{usd(abs(t['entry'] - t['stop']) * t['qty'])}; ja līdz {t['target']:.2f}, ar peļņu ap "
                f"{usd(abs(t['target'] - t['entry']) * t['qty'])}. Vēlākais {lv_time(at(d, '15:55'))} pēc Latvijas laika tas aizvērs darījumu.")
    return f"{head} šodien darījuma nebija. " + (s["notes"][-1].split(" ", 1)[-1] if s["notes"] else "")
