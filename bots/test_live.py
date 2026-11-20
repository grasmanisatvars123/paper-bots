"""Offline run of the three live bots against a fake broker on past SPY minute data (no request reaches Alpaca).
The clock is simulated; bracket legs, limit entries and market-on-close orders are filled on the minute bars.
Usage (needs the SPY cache from the Trading Vault backtest): python test_live.py [days, default 60]
"""
import os, sys, tempfile, types
import numpy as np, pandas as pd
import common as cm

CACHE = os.path.join(os.path.expanduser("~"), "trading-data", "spy_sessions.npz")
z = np.load(CACHE); DATES = list(pd.to_datetime(z["dates"]).strftime("%Y-%m-%d")); A = z["A"]
IDX = {d: i for i, d in enumerate(DATES)}


class Clock:
    t = None


def advance(t):
    Clock.t = max(Clock.t, t)


class Fake:
    def __init__(self):
        self.orders, self.n, self.pos, self.avg, self.cash = {}, 0, 0, 0.0, 100000.0

    def day(self): return Clock.t.strftime("%Y-%m-%d")

    def minute(self):
        return int((Clock.t - cm.at(self.day(), "09:30")).total_seconds() // 60)

    def px(self, i=None):
        d = IDX[self.day()]; i = min(max(self.minute() - 1 if i is None else i, 0), 389)
        return float(A[3, d, i])

    # fills between the last check and now
    def sync(self):
        d = IDX.get(self.day())
        if d is None: return
        m = min(self.minute(), 390)
        for o in list(self.orders.values()):
            if o["status"] != "new": continue
            for i in range(max(o["from"], 0), m):
                h, l, c = A[1, d, i], A[2, d, i], A[3, d, i]
                if o["type"] == "limit" and ((o["side"] == "buy" and l <= o["lp"]) or (o["side"] == "sell" and h >= o["lp"])):
                    self.fill(o, o["lp"], i); break
                if o["type"] == "stop" and ((o["side"] == "sell" and l <= o["sp"]) or (o["side"] == "buy" and h >= o["sp"])):
                    self.fill(o, o["sp"], i); break
            if o["type"] == "market" and o["tif"] == "cls" and m >= 390:
                self.fill(o, float(A[3, d, 389]), 389)

    def fill(self, o, p, i):
        o.update(status="filled", filled_avg_price=str(p), filled_qty=str(o["qty"]),
                 filled_at=(cm.at(self.day(), "09:30") + pd.Timedelta(minutes=i + 1)).isoformat())
        q = o["qty"] if o["side"] == "buy" else -o["qty"]
        if self.pos == 0: self.avg = p
        self.pos += q; self.cash -= q * p
        if o.get("parent"):                                      # one leg filled: cancel the other
            for lid in self.orders[o["parent"]]["leg_ids"]:
                if lid != o["id"] and self.orders[lid]["status"] == "new": self.orders[lid]["status"] = "canceled"
        for lid in o.get("leg_ids", []):                         # entry filled: legs become active
            self.orders[lid]["from"] = i + 1; self.orders[lid]["status"] = "new"

    def _new(self, **k):
        self.n += 1
        o = dict(id=f"o{self.n}", status="new", **{"tif": "day", "from": self.minute(), **k})
        self.orders[o["id"]] = o
        return o

    # --- Alpaca methods the bots use
    def calendar(self, day): return [dict(date=day, open="09:30", close="16:00")] if day in IDX else []
    def calendar_range(self, a, b): return [dict(date=d, open="09:30", close="16:00") for d in DATES if str(a) <= d <= str(b)]
    def account(self): self.sync(); return dict(equity=str(self.cash + self.pos * self.px()))
    def latest_price(self, s): self.sync(); return self.px()

    def bars_full(self, s, tf, start, end=None, feed="iex"):
        if tf == "1Day":
            rows = [(pd.Timestamp(d, tz=cm.NY), A[0, i, 0], A[1, i].max(), A[2, i].min(), A[3, i, 389], A[4, i].sum())
                    for i, d in enumerate(DATES) if pd.Timestamp(start) <= pd.Timestamp(d, tz=cm.NY) < pd.Timestamp(end)]
            df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"]).set_index("t")
            df.index.name = None
            return df
        day = pd.Timestamp(start).tz_convert(cm.NY).strftime("%Y-%m-%d"); d = IDX[day]
        last = 390 if day < self.day() else self.minute()
        idx = [cm.at(day, "09:30") + pd.Timedelta(minutes=i) for i in range(max(last, 0))]
        return pd.DataFrame({"open": A[0, d, :last], "high": A[1, d, :last], "low": A[2, d, :last],
                             "close": A[3, d, :last], "volume": A[4, d, :last]}, index=pd.DatetimeIndex(idx))

    def bracket_order(self, s, side, qty, tp, sl, cid, limit=None):
        ex = "sell" if side == "buy" else "buy"
        tpo = self._new(type="limit", side=ex, qty=qty, lp=tp, parent=None); tpo["status"] = "held"
        slo = self._new(type="stop", side=ex, qty=qty, sp=sl, parent=None); slo["status"] = "held"
        if limit is None:
            e = self._new(type="market", side=side, qty=qty, leg_ids=[tpo["id"], slo["id"]])
            tpo["parent"] = slo["parent"] = e["id"]
            self.fill(e, self.px(), self.minute() - 1)
        else:
            e = self._new(type="limit", side=side, qty=qty, lp=limit, leg_ids=[tpo["id"], slo["id"]])
            tpo["parent"] = slo["parent"] = e["id"]
        return dict(e)

    def market_order(self, s, side, qty, cid, tif="day"):
        o = self._new(type="market", side=side, qty=qty, tif=tif)
        if tif != "cls": self.fill(o, self.px(), self.minute() - 1)
        return dict(o)

    def order(self, oid):
        self.sync(); o = dict(self.orders[oid])
        if o.get("leg_ids"): o["legs"] = [dict(self.orders[x]) for x in o["leg_ids"]]
        return o

    def open_orders(self, s): self.sync(); return [dict(o) for o in self.orders.values() if o["status"] in ("new", "held")]
    def cancel(self, oid):
        o = self.orders[oid]
        if o["status"] in ("new", "held"): o["status"] = "canceled"
        for lid in o.get("leg_ids", []):
            if self.orders[lid]["status"] in ("new", "held"): self.orders[lid]["status"] = "canceled"
    def cancel_all_symbol(self, s):
        for o in self.open_orders(s): self.cancel(o["id"])
    def position(self, s): self.sync(); return dict(qty=str(self.pos), avg_entry_price=str(self.avg)) if self.pos else None
    def close_position(self, s):
        return self.market_order(s, "sell" if self.pos > 0 else "buy", abs(self.pos), "close")


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    cm.ROOT = tempfile.mkdtemp()
    cm.now = lambda: Clock.t
    cm.sleep_until = advance
    cm.time.sleep = lambda sec: advance(Clock.t + pd.Timedelta(seconds=sec))
    import bot1_value_area as b1, bot2_ict as b2, bot3_daily as b3
    b3.RULES_FILE = os.path.join(cm.ROOT, "state", "bot3", "rules.json")
    msgs = []
    cm.message = lambda text: msgs.append(text)
    b1.cm.message = b2.cm.message = b3.cm.message = cm.message
    res = {}
    for name, mod, starts in (("bot1", b1, ("08:40", "14:20")), ("bot2", b2, ("08:40", "12:00", "14:30")),
                              ("bot3", b3, ("14:00",))):
        f = Fake(); trades = 0
        for day in DATES[-days:]:
            for hhmm in starts:
                Clock.t = cm.at(day, hhmm)
                mod.auto(f)
            Clock.t = cm.at(day, "16:01"); f.sync()
            s = cm.load(name, day)
            if s.get("trade"): trades += 1
            if name != "bot3":
                assert f.pos == 0, f"{name} {day}: position left open"
        res[name] = (trades, round(float(f.account()["equity"]), 2))
    print("bots ran without errors on", days, "days:", res)
    print("sample messages:"); [print(" ", m) for m in msgs[:3] + msgs[-3:]]


if __name__ == "__main__":
    main()
