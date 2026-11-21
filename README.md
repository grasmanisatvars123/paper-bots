# Paper bots

Three small bots that trade SPY on **Alpaca paper accounts** (simulated money, no real orders), one account per
bot. They run on GitHub Actions; this repository is public only so the Actions minutes are free. The keys are
encrypted repository secrets; without them every run stops at once and does nothing.

| Bot | Idea | Trades | Workflow |
|---|---|---|---|
| 1 Value area | Yesterday's volume profile (value area = 68 % of volume). If price leaves the value area and a 5-minute bar closes back inside, trade towards the other edge; stop at the day's extreme; out at 15:55. | about 2–3 a week | `bot1.yml` |
| 2 ICT | ICT time windows: 09:30–11:00 sweep of yesterday's PM-session (13:30–16:00) high/low, 13:30–15:00 sweep of today's lunch (12:00–13:30) range; then a structure shift with a fair-value gap and a limit entry at the gap; target the opposite level; out at 15:55. | about 2–3 a week | `bot2.yml` |
| 3 Daily mean reversion | Long VOO (SPY signals) at the close while IBS < 0.2 or Connors RSI(2) < 10 (above the 200-day average) is active; flat otherwise. ("Double 7s" was dropped on 2026-11-21 after it failed the unseen 2018–2022 check.) Market-on-close orders. No stop. | about 1 entry a fortnight, holds 2–7 days | `bot3.yml` |

Bots 1 and 2 risk 10 % of equity between entry and stop (since 2026-11-21; position at most 4 × equity and 95 % of the day-trading buying power, and that cap decides the size on most days) and send entry, stop and
target to Alpaca as one bracket order, so the broker watches them. Bot 3 holds about 98 % of equity in SPY.

Rules were fixed before testing. The shared rule functions are in `bots/rules.py`; the backtest calls the same
functions. `bots/test_live.py` runs all three bots against a fake broker on past minute data.

**Since 2026-11-21 (the user's choice): only bot 3 is in use, and it runs in the same paper account as the
Williams bot** (github.com/grasmanisatvars123/williams-paper-bot). Williams trades SPY and touches only SPY, so bot 3
trades **VOO** (an S&P 500 fund that moves like SPY; the signals still come from SPY data). If Williams still uses the
buying power at 15:46, bot 3 buys what fits at the close and the rest at 15:58:40. Its secrets `BOT3_…` are the
Williams keys (Desktop script "Bots 3 Williams konta.bat"). Bots 1 and 2 stay without keys, so they do nothing; their
tests showed no edge.

Original setup: create three Alpaca paper accounts, then add the secrets `BOT1_ALPACA_API_KEY`, `BOT1_ALPACA_SECRET_KEY`,
`BOT2_…`, `BOT3_…` (Settings → Secrets and variables → Actions).

Nothing here is investment advice.
