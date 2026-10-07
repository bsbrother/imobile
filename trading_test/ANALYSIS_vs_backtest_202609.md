# Why the backtest says +1.17% for September and the live replay says -9.89%

Measured 2026-10-01 on the same picks, same dates (20260901-20260930), same strategy.

## Answer in one line

**Yes — the negative return is almost entirely the account's pre-existing holdings, not the
strategy.** One legacy position (中际旭创 300308.SZ) accounts for ¥-82,418 of the ¥-89,307 total
realized. Excluding the account's starting positions, the same month on the same picks returns
**-1.87%**, i.e. a ~3pp execution-realism gap against the backtest's +1.17%, not an 11pp one.

## The two runs

| Metric | Replay WITH account history | Replay, strategy only (`--no-holdings`) | Backtest, Sept slice |
|---|---|---|---|
| Starting point | ¥300,000 cash + 6 holdings (¥604,730 at cost) | ¥300,000 cash | ¥1,714,146.79 (continuing book) |
| Final | ¥815,237.55 | ¥294,394.59 | ¥1,761,481.45 |
| **Total return** | **-9.89%** | **-1.87%** | **+2.76%** (Sept realized +¥47,334.66) |
| Max drawdown | -1.35% | -2.49% | -0.65% |
| Trades | 49 (24 buy / 25 sell) | 46 (23 / 23) | 60 |
| Fees | ¥860.65 | ¥476.41 | — |
| Realized | ¥-89,306.95 | ¥-5,480.40 | ¥+47,334.66 |

The backtest's own daily table puts September's realized P&L at **+¥47,334.66** over 21 days, 16 of
them positive — a modestly profitable month on a ¥1.71M book. A fresh September-only run at the
default ¥600,000 base is the +1.17% figure; it differs from +2.76% only because a fresh run starts
with a different book and cash available, not because the strategy changed.

## Where the -9.89% actually comes from

| Source | Realized | Note |
|---|---|---|
| Account history (中际旭创 300308.SZ, 200 sh) | **¥-82,418.47** | 92% of the whole loss |
| Strategy picks (24 buys / 24 sells) | ¥-6,888.49 | essentially flat |

中际旭创 was in the account before the period, carried at a **cost basis of ¥1,263.95** from the DB's
last sync (2026-07-14). The engine's stop is -2.5% off cost = **¥1,232.35**, while the stock traded at
**¥852.50** on 20260901. The stop was already through the market before the day began, so the app's
conditional order fires at the open — the replay books ¥-82,418 on day one.

That loss is real money the account has already lost, and it happened *before* September: the same DB
row shows ¥1,093.98 and a ¥-33,995 float back in July. It is not September's trading, and it is not
the strategy's doing. Realizing it inside the period makes the headline measure the account's
cumulative drawdown rather than the month.

## The remaining ~3pp: strategy-only -1.87% vs backtest +1.17%

| Cause | Evidence |
|---|---|
| **Gap-through stops** | 5 sells flagged `trigger already through the market at the open`, ¥-6,961.22. A stop that gaps overnight fills at the open; the backtest books the exact stop price. This is the dominant term. |
| **Costs** | ¥476.41 of commission + stamp duty on a ¥-5,480 realized month — roughly 0.1% of turnover, and pure subtraction the backtest does not pay. |
| **Scale, not rate** | The replay sizes ¥300,000 / 10 slots = ¥30,000 per position; the backtest's September ran on a ¥1.71M continuing book with far more cash available per slot. Its +¥47,334 is a larger absolute P&L on a much larger book, so the *rates* are closer than the levels suggest. |
| **Universe** | All 24 planned buys filled — the missing-bars problem did NOT silently drop picks. It only affects the 5 legacy holdings, which have no cached bars at all (000006.SZ, 000009.SZ, 000717.SZ, 600279.SH, 603279.SH): they cannot be traded, are carried at cost, and are excluded from P&L. They do occupy 5 of the 10 position slots, but the day-level picks rarely exceeded the remaining room. |

## What to conclude

1. **Do not read -9.89% as the strategy's September.** Read -1.87%, and read the -82,418 as an
   opening balance-sheet item. The account-level replay is correct to realize it — the live path
   *would* submit that stop and it *would* fire — but it is not a strategy result.
2. **The execution-realism gap is ~3pp over a month**, and it is dominated by gap-through stops, not
   by fees or by the data granularity. That is consistent with the earlier whole-run measurement
   (as-booked ~193% vs gap-aware ~61%).
3. **The 30-minute bars and the missing legacy-bars are caveats, not the cause.** No pick was lost to
   missing data; every planned buy filled.
4. For strategy comparisons, run `--no-holdings`. For an account replay, keep the holdings and read
   the return knowing it starts with the account's existing drawdown.

## Reproduce

```bash
.venv/bin/python trading_test/run_trading_test.py --start 20260901 --end 20260930 --cash 300000 --no-app
.venv/bin/python trading_test/run_trading_test.py --start 20260901 --end 20260930 --cash 300000 --no-app --no-holdings --tag strategy-only
```

Outputs: `trading_test/results/20260901_20260930_trading/` and `..._strategy-only/`
(`result_report.md`, `report_trading_<date>.md`, `trades.json`, `day_state_<date>.json`).
