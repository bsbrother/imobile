# Backtest Module — iMobile A-Shares Quantitative Engine

The `backtest/` module is the **quantitative research and simulation** subsystem of iMobile. It implements a full China A-Share market backtesting framework: regime-aware strategy selection, hot-sector stock picking, smart order generation, T+1-compliant order execution, P&L reporting, and benchmark comparison.

---

## Table of Contents

- [Step-by-Step Process](#step-by-step-process)
- [Module Structure](#module-structure)
- [Strategies](#strategies)
- [Market Regime Detection](#market-regime-detection)
- [Smart Order Lifecycle](#smart-order-lifecycle)
- [Environment Variables](#environment-variables)
- [Configuration (`config.json`)](#configuration-configjson)
- [Usage](#usage)
- [Output Files](#output-files)
- [Analysis & Reporting](#analysis--reporting)
- [A-Shares Compliance](#a-shares-compliance)

---

## Step-by-Step Process

The entire backtest is orchestrated by `pick_orders_trading()` in `backtest/engine.py`, called for each trading day in sequence. Here's the full pipeline:

### Phase 0: Initialization

```
Entry: pick_orders_trading(start_date, end_date, src='ts_7AZ', ...)
  ├── If is_live=True → switch to production DB (imobile.db) instead of test DB
  ├── If backtest_search=True → discover_working_search_providers()
  ├── Parse and validate date range
  └── Get trading days list from calendar
```

**Key decision paths:**
- `is_live=True` → uses real `imobile.db`, gets cash from app homepage (today/future) or DB `summary_account` (past dates)
- `is_live=False` → simulated cash = `INITIAL_CASH + cumulative_realized_pnl - current_holdings_cost`
- `backtest_search=False` → all search providers disabled, no web/news context for AI
- `backtest_ai=False` → AI-dependent strategies (ts_ai_pick, ts_daily, ts_auto) redirected to pure-technical fallbacks

---

### Phase 1: Market Regime Detection

```
For each trading day in date range:
  │
  ├── detect_market_regime(this_date)
  │     ├── Fetch 120 trading days of SSE Composite (000001.SH) OHLCV
  │     ├── Compute MA60, MA120
  │     ├── Classify into 4 regimes:
  │     │     bull:     price > MA60 > MA120, uptrend, volatility < 2%
  │     │     bear:     price < MA60 < MA120, downtrend
  │     │     volatile: daily volatility > 3% (override, regardless of trend)
  │     │     normal:   fallback
  │     └── Returns {regime, take_profit_pct, stop_loss_pct, max_hold_days, ...}
  │
  ├── Set dynamic MAX_POSITIONS per regime:
  │     bull → 12, normal → 10, volatile → 8, bear → 5
  │
  └── Apply .env overrides (SL_BULL, SL_NORMAL, SL_VOLATILE, SL_BEAR,
        SL_ENABLED, SL_WITH_RE_PICK, HOLD_DAYS_MULT, SL_WIDEN_STEP, ...)
```

**Config source**: `backtest/config.json` → `trading_rules.risk_reward_ratios.{regime}_market` + `.env` overrides.

---

### Phase 2: Stock Picking

```
pick_stocks_to_file(this_date, src='ts_7AZ', backtest_search, backtest_ai)
  │
  ├── [BIAS Filter] Fetch 3 months of SH index data via AKShare
  │     If close is 5%+ above MA60 → filter stocks above MA60 only
  │     (only applies when ts_ths_dc strategy is used)
  │
  ├── If backtest_ai=False → redirect AI strategies to technical alternatives:
  │     ts_ai_pick → ts_longup (pure ADX trend-following)
  │     ts_daily   → ts_hma   (HMA + SuperTrend)
  │     ts_auto    → ts_7AZ   (CANSLIM, no LLM needed)
  │
  ├── Dispatch to strategy script (subprocess via .venv/bin/python):
  │     ts_7AZ     → backtest/strategies/ts_7AZ.py → CANSLIM 7-factor screener
  │     ts_auto    → backtest/strategies/ts_auto.py → meta-strategy delegator
  │     ts_ths_dc  → backtest/strategies/ts_ths_dc.py → hot-sector channel breakout
  │     ts_hma     → backtest/strategies/ts_hma.py → Hull MA + SuperTrend
  │     ts_longup  → backtest/strategies/ts_longup.py → ADX trend-following
  │     ts_ai_pick → backtest/strategies/ts_ai_pick.py → LLM + web analysis
  │     ts_daily   → backtest/strategies/ts_daily.py → LLM news-driven
  │     ts_go      → utils/go-stock/ → Go compiled binary
  │
  ├── Strategy writes results to /tmp/tmp (JSON)
  │     Engine renames to /tmp/tmp_{src}_{date}_{pid} (parallel-safe)
  │     Fallback: /tmp/ts_7AZ_tmp.json for ts_7AZ
  │
  ├── Read JSON, apply SCORE_MIN filter from .env:
  │     If SCORE_MIN=5 → drop stocks with CANSLIM score < 5
  │
  ├── Apply POS_SCORE_WEIGHT (if set in .env, e.g. 0.7):
  │     Final score = POS_SCORE_WEIGHT × positional_score + (1-POS_SCORE_WEIGHT) × CANSLIM_score
  │
  └── Write pick_stocks_YYYYMMDD.json:
        {pick_date, base_date, target_trading_date, market_pattern,
         regime_data, selected_stocks (capped at MAX_POSITIONS)}
```

**ts_7AZ strategy internals** (backtest/strategies/ts_7AZ.py):
1. `compute_rps()` — Relative Price Strength ranking (60d)
2. `fetch_financial_data()` — Tushare API: EPS, ROE, revenue growth
3. `get_stock_pool()` — Hot-sector stocks from top-performing sectors
4. `compute_technical_indicators()` — RSI, BB, MACD, volume ratio, MA alignment
5. `canslim_score_stock()` — 7-factor binary scoring (C-A-N-S-L-I-M):
   - C: Current quarterly EPS growth ≥ 25%
   - A: Annual ROE ≥ 15%
   - N: Price within 15% of 52-week high
   - S: Market cap < 20B (small-cap)
   - L: RPS rank ≥ 70
   - I: Turnover rate ≥ 3%
   - M: Price above 200-day MA
6. `canslim_screener()` — Filter pool through all 7 factors
7. `pick_strong_stocks()` — Rank by composite score, return top-N

---

### Phase 3: Capital Calculation

```
For each trading day:
  │
  ├── Query DB transactions: SUM P&L from all 'sell' transactions < today
  │     (parses "P&L: ¥XXXX" from transaction notes)
  │
  ├── Query DB holdings: SUM(cost_basis_total) for current positions
  │
  ├── Backtest mode:
  │     current_portfolio_nav = INITIAL_CASH + cumulative_realized_pnl
  │     current_capital = current_portfolio_nav - current_holdings_cost
  │
  └── Live mode (is_live=True):
        If date ≥ today → get real cash from app homepage or caller
        If date < today  → read from DB summary_account table
```

---

### Phase 4: Smart Order Generation

```
create_smart_orders_from_picks(pick_file, user_id, current_capital, ...)
  │
  ├── Step 4.1: CLI Analysis (subprocess)
  │     └── .venv/bin/python -m backtest.cli analyze --stocks-file <pick_file> -o <output>
  │           ├── Fetch OHLCV data for each stock
  │           ├── Compute technical indicators (RSI, BB, ATR)
  │           ├── Determine buy_price per regime:
  │           │     bull: close × (1 + clamp(0.5×ATR/close, 2%, 7%/13%))
  │           │           → Buy above yesterday's close
  │           │           → 7% cap for main board, 13% for ChiNext/STAR
  │           │     other: RSI/BB/support-based conservative entry
  │           ├── Calculate TP = cost × (1 + take_profit_pct)
  │           ├── Calculate SL = cost × (1 - stop_loss_pct)
  │           └── Size quantity = current_capital × weight / buy_price (round to 100s)
  │
  ├── Step 4.2: Expire Stale Orders
  │     └── UPDATE smart_orders SET status='expired'
  │           WHERE status='running' AND valid_until < this_date
  │
  ├── Step 4.3: Recover Existing Positions
  │     For each holding in DB:
  │       ├── Fetch cost_basis, current_price (prev-close), purchase_date
  │       ├── Calculate days_held from transactions
  │       ├── Force-sell conditions:
  │       │     days_held ≥ holding_days         → 'order_expired_before_sell'
  │       │     days_held ≥ stagnate_days AND    → 'stagnation_cut'
  │       │     current_return < 2%
  │       ├── Force-sell: trigger ≥ widen_pct × current_price
  │       │     (90% for main board, 80% for 3/688 stocks)
  │       ├── Normal: TP = cost × (1+take_profit_pct), SL = cost × (1-stop_loss_pct)
  │       └── INSERT OR REPLACE into smart_orders
  │
  ├── Step 4.4: Add New BUY Orders
  │     For each stock in pick file not already in running orders:
  │       ├── Skip if SKIP_GAPS_DOWN_OPEN_PRICE is true AND
  │       │     today's open < yesterday's close (gap-down)
  │       ├── Create buy trigger: 股价<=buy_price元(触发买入)
  │       ├── INSERT into smart_orders with order_number ORD_{date}_{code}_{user_id}
  │       └── Cap at MAX_POSITIONS new orders
  │
  └── Step 4.5: Adjust Existing Orders
        For each re-picked stock:
          ├── BUY order: adjust buy_price lower (min of old and new)
          ├── TP/SL order:
          │     ├── Increase TP by 10% each re-pick (let winners run)
          │     └── If SL_WITH_RE_PICK=true in .env:
          │           ├── Calculate re-picks from SL drift from entry
          │           ├── If re_picks ≥ SL_WIDEN_AFTER → widen SL by SL_WIDEN_STEP
          │           └── Cap at 6% below entry (SL never goes below entry × 0.94)
          ├── valid_until = today (one-day expiry design)
          └── UPDATE smart_orders in DB
```

**Order number conventions:**
- New buy orders: `ORD_{date}_{code}_{user_id}`
- Recovered holdings: `ORD_{date}_{code}_{user_id}_holding`
- Recovered from app: `ORD_{date}_{code}_{user_id}_recovered`

---

### Phase 5: Order Execution (Past Dates Only)

```
OrderAnalyzer.generate_daily_report(this_date)
  └── For each order in smart_orders:
        check_order_execution(order, market_data, date)
          │
          ├── Fetch OHLCV for this_date (open, high, low, close, pre_close)
          │
          ├── Check if stock is held (holdings > 0 in holding_stocks)
          │
          ├── T+1 Check: can_sell = available_shares > 0 AND purchase_date < today
          │
          ├── Limit-down lock check:
          │     main board (0/6): limit_down = pre_close × 0.90
          │     3xx/688:          limit_down = pre_close × 0.80
          │     If high ≤ limit_down → blocked, cannot sell
          │
          ├── BUY ORDER execution:
          │     └── If open_price ≤ buy_price → BUY EXECUTED at open_price
          │         INSERT into transactions table
          │         INSERT/UPDATE holding_stocks
          │         Record P&L in notes
          │
          └── SELL ORDER execution:
                ├── TP hit:  high >= take_profit_price → SELL at TP
                ├── SL hit:  low  <= stop_loss_price    → SELL at SL
                └── INSERT sell into transactions
                      UPDATE holding_stocks (reduce/remove)
                      Record P&L: net = (sell_price - cost) × qty - fees
                      Note: P&L: ¥XXXX (gain/loss)
```

**Fee calculation:**
- Commission: 0.00341% × amount, min ¥5
- Stamp duty: 0.05% on sells only
- Net = sell_amount - commission - stamp_duty

---

### Phase 6: Period Report (Historical Ranges Only)

```
OrderAnalyzer.generate_period_report(start_date, end_date)
  │
  ├── Read ALL transactions from DB for the period
  ├── Calculate:
  │     ├── Total return (final equity / initial cash - 1) × 100
  │     ├── Realized P&L, Unrealized P&L
  │     ├── Win rate, average win/loss
  │     └── Max drawdown
  │
  ├── Benchmark comparison:
  │     ├── SSE Composite (000001.SH)
  │     ├── CSI 300 (000300.SH)
  │     ├── CSI 500 (000905.SH)
  │     └── CSI 1000 (000852.SH)
  │
  └── Write report_period_{start}_{end}.md
```

**Skipped when:**
- `end_date >= today` (no OHLCV/benchmark data for future dates)
- Single-day pre-market runs (only stock picking, no historical comparison)

---

### Phase 7: Inter-Day Handling

```
Between trading days:
  ├── Sleep 30 seconds (API rate limiting) if search or AI is enabled
  ├── Sleep 0.01 seconds (fast) if both search and AI are disabled (offline mode)
  └── Resume check: skip day if report_orders_YYYYMMDD.md already exists
```

---

## Module Structure

```
backtest/
├── engine.py               # Main orchestrator: pick → orders → execute → report
├── cli.py                  # CLI: run / pick / analyze / config commands
├── result_backtest.py      # Standalone result analyzer (monthly P&L + index table)
├── config.json             # Risk parameters, position sizing, strategy configs
├── __init__.py             # Package init: data provider, calendar, config singletons
│
├── strategies/             # Stock-picking strategy scripts
│   ├── ts_auto.py          # ★ Meta-strategy: regime → delegates to sub-strategy
│   ├── ts_7AZ.py           # CANSLIM 7-factor fundamental screener
│   ├── ts_ths_dc.py        # Hot-sector + channel breakout (ts_dc)
│   ├── ts_hma.py           # Hull Moving Average + SuperTrend
│   ├── ts_longup.py        # ADX trend-following
│   ├── ts_ai_pick.py       # Full AI analysis (LLM + news/sentiment)
│   ├── ts_daily.py         # News-driven daily picks (LLM + web search)
│   ├── ts_gb_line.py       # Golden-cross / dead-cross line
│   ├── ts_combine.py       # Multi-strategy combiner
│   ├── picker.py           # ASharesStockPicker base class
│   ├── manager.py          # StrategyManager: register + dispatch
│   ├── bull.py             # Bull-market strategy config
│   ├── bear.py             # Bear-market strategy config
│   ├── normal.py           # Normal-market strategy config
│   └── volatile.py         # Volatile-market strategy config
│
├── core/                   # Backtest engine internals
│   ├── backtest.py         # ChinaASharesBacktest + ASharesBacktestWrapper
│   ├── strategy.py         # ASharesStrategy base class
│   ├── interfaces.py       # DataProvider / MarketPatternDetector / StockPicker ABCs
│   └── validator.py        # TradeValidator: T+1 and short-selling enforcement
│
├── data/                   # Data provider layer
│   ├── provider.py         # TushareDataProvider, AkshareDataProvider, TdxDataProvider
│   ├── provider_akshare.py # AkShare-specific fetching
│   ├── cache.py            # In-memory + file cache
│   ├── sqlite_cache.py     # SQLiteDataCache: persistent OHLCV + index caching
│   └── validator.py        # Data integrity checks
│
├── analysis/               # Post-backtest analysis
│   ├── performance.py      # PerformanceAnalyzer: Sharpe, drawdown, alpha, beta
│   ├── benchmark.py        # Benchmark comparison
│   ├── reporting.py        # Report generation (Markdown, HTML, CSV)
│   ├── plots.py            # Equity curve, drawdown charts
│   ├── pattern_analyzer.py # Market pattern analysis
│   ├── pattern_detector.py # ChinaMarketPatternDetector
│   └── indicators.py       # TechnicalIndicators: RSI, Bollinger, ATR, etc.
│
├── utils/                  # Shared utilities
│   ├── trading_calendar.py # A-Share trading calendar (holiday-aware, pickle-cached)
│   ├── market_regime.py    # detect_market_regime() using MA60/MA120 + volatility
│   ├── config.py           # ConfigManager: JSON config loader
│   ├── basic_information.py# Stock basic info cache
│   ├── trailing_stop.py    # Trailing stop calculation
│   ├── util.py             # Date conversion, formatting
│   ├── logging_config.py   # Loguru setup
│   ├── exceptions.py       # IBacktestError, TradeValidationError
│   └── proxy_config.py     # Proxy rotation
│
├── cbs_ewo/                # CBS/EWO wave analysis (supplemental)
├── scratch/                # Parameter optimization scripts
└── results/                # Backtest output directory (auto-created)
```

---

## Strategies

| Strategy | Type | How It Works | Best In |
|---|---|---|---|
| `ts_7AZ` | ✦ Default | CANSLIM 7-factor screener: C-A-N-S-L-I-M binary scoring | Normal/moderate |
| `ts_auto` | Meta | 20-day momentum → delegates to sub-strategy | All conditions |
| `ts_ths_dc` | Technical | Hot-sector THS data + Donchian channel breakout | Bull/normal |
| `ts_hma` | Technical | Hull Moving Average + SuperTrend reversal | Sharp bears |
| `ts_longup` | Technical | ADX + slope-based trend-following | Strong bulls |
| `ts_ai_pick` | AI | LLM analysis + web search + sentiment | Any (needs API) |
| `ts_daily` | AI | LLM news-driven daily picks | Any (needs API) |
| `ts_gb_line` | Technical | Golden-cross / dead-cross signals | Trending markets |

---

## Market Regime Detection

`backtest/utils/market_regime.py` → `detect_market_regime(date)`

Uses 120 trading days (~6 months) of SSE Composite data:

| Regime | Condition | TP% | SL% | Max Hold Days |
|---|---|---|---|---|
| `bull` | price > MA60 > MA120, uptrend, volatility < 2% | 200% | 0.5% | 15 |
| `bear` | price < MA60 < MA120, downtrend | 200% | 0.5% | 5 |
| `volatile` | daily volatility > 3% (override) | 200% | 0.5% | 8 |
| `normal` | default | 200% | 0.5% | 10 |

**Slope detection** (`get_regime_config`): Uses additional MA10 slope for short-term direction signal.

**Dynamic position limits:** bull=12, normal=10, volatile=8, bear=5.

---

## Smart Order Lifecycle

```
Day D: Order created with valid_until = D (one-day expiry)
  ├── If executed today → transaction written, holding updated
  │     Buy:  shares added, cost_basis recalculated (diluted average)
  │     Sell: shares removed, P&L recorded
  │
  ├── If NOT executed by end of day D:
  │     Day D+1: order still 'running' but past valid_until
  │     Day D+1 pre-market: expired automatically (status='expired')
  │     Stock re-enters the picking pool
  │
  └── If re-picked on day D+1:
        ├── BUY order: buy_price adjusted lower (more conservative)
        ├── SELL order: TP increased 10%, SL possibly widened
        └── New order_number, new valid_until
```

**Force-sell triggers (from holdings):**
1. `days_held ≥ max_hold_days` → `order_expired_before_sell`
2. `days_held ≥ stagnation_days AND return < 2%` → `stagnation_cut`
3. `days_held ≥ 2 AND no update` → `order_expired_or_stale`

---

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `TUSHARE_TOKEN` | Tushare API token | required |
| `SCORE_MIN` | CANSLIM score floor (0-7) | 0 (no filter) |
| `POS_SCORE_WEIGHT` | Weight of positional score (0-1) | not set |
| `SL_BULL/NORMAL/VOLATILE/BEAR` | Per-regime stop-loss % override | from config.json |
| `SL_ENABLED` | `false` to disable stop-loss entirely | true |
| `SL_WITH_RE_PICK` | `true` to widen SL on re-pick | true |
| `SL_WIDEN_STEP` | SL widening step (% of entry price) | 0.005 (0.5%) |
| `SL_WIDEN_AFTER` | Re-picks before widening starts | 0 |
| `HOLD_DAYS_MULT` | Multiplier on max_hold_days | 1.0 |
| `SKIP_GAPS_DOWN_OPEN_PRICE` | Skip buy if today open < prev close | true |
| `BACKTEST_PATH` | Root path for backtest module | ./backtest |
| `REPORT_DIR` | Results output directory | ./backtest/results |
| `CONFIG_FILE` | Path to config.json | ./backtest/config.json |

---

## Configuration (`config.json`)

Key sections:

```json
{
  "init_info": { "data_provider": "tushare", "initial_cash": 600000 },
  "portfolio_config": { "commission": 0.00341, "tax": 0.0005 },
  "trading_rules": {
    "position_sizing": { "max_positions": 20, "rank_weighted": true },
    "risk_reward_ratios": {
      "bull_market":    { "take_profit_pct": 2.0, "stop_loss_pct": 0.005, "max_hold_days": 15 },
      "normal_market":  { "take_profit_pct": 2.0, "stop_loss_pct": 0.005, "max_hold_days": 10 },
      "volatile_market":{ "take_profit_pct": 2.0, "stop_loss_pct": 0.005, "max_hold_days": 8 },
      "bear_market":    { "take_profit_pct": 2.0, "stop_loss_pct": 0.005, "max_hold_days": 5 }
    }
  }
}
```

**Note:** The TP/SL values in config.json are conservative defaults. The engine applies `.env` overrides for aggressive tuning. The 200% TP with 0.5% SL in the regime function acts as a "no-limit" TP — the trailing stop and re-pick widening handle actual risk management.

---

## Usage

```bash
# Default: ts_7AZ, YYYYMMDD format
python backtest/engine.py 20250101 20250612

# Specific strategy
python backtest/engine.py 20250101 20250612 ts_auto

# Disable AI and search (pure technical, 30x faster; note: flags only affect ts_ai_pick/ts_daily/ts_auto)
python backtest/engine.py 20250101 20250612 ts_7AZ --no-search --no-ai

# Resume interrupted run (--search --ai are default, no need to specify)
python backtest/engine.py 20250101 20250612 ts_7AZ --resume

# Analyze past results
python backtest/result_backtest.py backtest/results/20250101_20250612_ts_7AZ
```

---

## Output Files

| File | Contents |
|---|---|
| `pick_stocks_YYYYMMDD.json` | Picked stocks with scores, market regime |
| `smart_orders_YYYYMMDD.json` | Buy/TP/SL orders with prices and quantities |
| `report_orders_YYYYMMDD.md` | Daily P&L: positions, transactions, portfolio value |
| `report_period_{start}_{end}.md` | Full-period: total return vs 4 benchmarks |

---

## A-Shares Compliance

- **T+1 rule:** Buys today are NOT sellable until next trading day (enforced in `check_order_execution`)
- **No short-selling:** Only long positions allowed (validator.py)
- **Limit-up/down:** 10% main board, 20% ChiNext/STAR, 30% BSE; locked limit-down prevents selling
- **Fee simulation:** Commission 0.00341% (min ¥5) + stamp duty 0.05% on sells
- **100-share round lots:** Quantities rounded down to nearest 100 shares
