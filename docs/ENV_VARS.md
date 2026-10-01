# Environment Variables Reference

Complete reference for all `.env` variables used by iMobile.
Organized by subsystem. Variables marked with `*` are required.

---

## Paths & Database

| Variable | Default | Used By | Description |
|---|---|---|---|
| `BACKTEST_PATH` | `./backtest` | All | Root path for backtest module |
| `CONFIG_FILE` | `${BACKTEST_PATH}/config.json` | Backtest | Strategy/risk parameters |
| `REPORT_PATH` | `${BACKTEST_PATH}/results` | Backtest | Backtest output directory |
| `LOG_PATH` | `./logs` | All | Application log directory |
| `LOG_LEVEL` | `INFO` | All | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `CACHE_PATH` | `./shared/data_cache` | Backtest | Pickle/DB cache files |
| `CAL_PICKLE_FILE` | `./shared/data_cache/cal.pkl` | Backtest | Trading calendar cache |
| `BASIC_INFO_PICKLE_FILE` | `./shared/data_cache/basic_info.pkl` | Backtest | Stock basic info cache |
| `DB_CACHE_FILE` | `./shared/db/db_cache.db` | Backtest | OHLCV + index data cache |
| `DB_IMOBILE_FILE` | `./shared/db/imobile.db` | Trading/Web | Production DB (holdings, orders, P&L) |
| `DBTEST_IMOBILE_FILE` | `./shared/db/test_imobile.db` | Backtest | Test DB for simulations |

---

## Data Providers

| Variable | Required | Used By | Description |
|---|---|---|---|
| `TUSHARE_TOKEN`* | Yes | Backtest | Tushare Pro token (needs 2000+ points) |

---

## AI Providers

| Variable | Required | Used By | Description |
|---|---|---|---|
| `GOOGLE_API_KEY`* | Yes | Trading | Gemini API key (free tier: AI Studio) |
| `GEMINI_API_KEY` | No | Trading | Alias for GOOGLE_API_KEY |
| `GEMINI_MODEL` | No | Trading | Model name (default: `gemini-3.1-flash-lite-preview`) |
| `GEMINI_THINKING_BUDGET` | No | Trading | Thinking tokens: `-1` dynamic, `0` off, 128-32768 |
| `OPENROUTER_API_KEY` | No | Utils | OpenRouter API for multi-model fallback |
| `DEEPSEEK_API_KEY` | No | Utils | DeepSeek API |
| `XAI_API_KEY` | No | Utils | xAI/Grok API |
| `AGNES_API_KEY` | No | Utils | Agnes AI (free multi-model) |
| `GROQ_API_KEY` | No | Utils | Groq API |
| `MINIMAX_API_KEY` | No | Utils | MiniMax API |
| `SENOVA_API_KEY` | No | Utils | SenseNova API |
| `QWEN_API_KEY` | No | Utils | Qwen API |
| `NVIDIA_API_KEY` | No | Utils | NVIDIA NIM API |
| `CEREBRAS_API_KEY` | No | Utils | Cerebras API |
| `ZENMUX_API_KEY` | No | Utils | ZenMux API |
| `LITELLM_MODEL` | No | Utils | LiteLLM model override |

---

## Search & News Providers

| Variable | Required | Used By | Description |
|---|---|---|---|
| `SEARCHAPI_API_KEY` | No | Utils | SearchAPI.io key |
| `SEARCHAPI_SEARCH_ENDPOINT` | No | Utils | SearchAPI endpoint URL |
| `TAVILY_API_KEY` | No | Utils | Tavily search API |
| `SERPAPI_API_KEY` | No | Utils | SerpAPI key |
| `SENOVA_BASE_URL` | No | Utils | SenseNova base URL |
| `SEARXNG_BASE_URLS` | No | Utils | Local SearXNG instance (default: `http://localhost:8080`) |
| `FIRECRAWL_API_KEY` | No | Utils | Firecrawl web extraction |
| `TINYFISH_API_KEY` | No | Utils | TinyFish search |
| `ANYSEARCH_API_KEY` | No | Utils | AnySearch API |
| `BOCHA_API_KEY` | No | Utils | Bocha AI search |
| `ANSPIRE_API_KEY` | No | Utils | Anspire API |
| `FINANCIAL_DATASETS_API_KEY` | No | Backtest | Financial Datasets API |
| `OXYLABS_USERNAME` | No | Utils | Oxylabs proxy username |
| `OXYLABS_PASSWORD` | No | Utils | Oxylabs proxy password |
| `CLOUDFLARE_ACCOUNT_ID` | No | Utils | Cloudflare account for AI gateway |

---

## Strategy Selection & Per-Strategy Sections

`.env` is the source of truth for which strategy a run uses, and each strategy can carry its own
config section. Implemented in `backtest/utils/strategy_env.py`.

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_STRATEGY` | `ts_7AZ_96MA_flow_review` | Strategy used when none is given on the command line — including `make backtest`. Must be a real strategy name, else it warns and falls back. **Backtest CLI only**: the live path never calls `apply_strategy_env` and hardcodes its strategy (`trading/runner.py:164`) |

**Sections.** A comment header holding exactly a strategy name opens a section; the `KEY=VALUE`
lines under it apply **only when that strategy runs**:

    # ── ts_7AZ_96MA_flow_review ──────────────────────────────────
    REVIEW_COMPOUND_SIZING=true
    # ── end ts_7AZ_96MA_flow_review ──

A section ends at the next header, an `end` marker, a divider line, or EOF. Section values
**replace** same-named keys set globally above or read from `config.json`. Within a section the
**last** assignment wins, so appending a line is enough — unlike the global area, which needs the
old line commented out because python-dotenv keeps the first assignment.

python-dotenv has no concept of sections and exports every line as a global, so
`apply_strategy_env()` also removes keys owned only by a different strategy's section. Without
that, a value in one strategy's section would silently apply to all of them.

---

## Backtest Strategy Parameters

These override values in `backtest/config.json`. Comment out any to use config.json defaults.

### Stop-Loss Per Regime

| Variable | Default | Range | Description |
|---|---|---|---|
| `SL_BULL` | 0.025 | 0.005-0.10 | Stop-loss % in bull market |
| `SL_NORMAL` | 0.025 | 0.005-0.10 | Stop-loss % in normal market |
| `SL_VOLATILE` | 0.02 | 0.005-0.10 | Stop-loss % in volatile market |
| `SL_BEAR` | 0.015 | 0.005-0.10 | Stop-loss % in bear market |

### Stop-Loss Behavior

| Variable | Default | Description |
|---|---|---|
| `SL_ENABLED` | `true` | `false` = disable SL entirely (only TP and max-hold exits) |
| `SL_WITH_RE_PICK` | `false` | `true` = widen SL on each re-pick. `false` = keep SL frozen at initial level (simpler, often higher aggregate returns) |
| `SL_WIDEN_STEP` | 0.005 | SL widening per re-pick (fraction of entry price). 0.005 = 0.5%/re-pick |
| `SL_WIDEN_AFTER` | 2 | Delay: only start SL widening after N re-picks. 0 = immediate |

### Position Sizing & Scoring

| Variable | Default | Description |
|---|---|---|
| `SCORE_MIN` | 0 | Minimum CANSLIM score filter (0-7). 5 = only A-grade stocks |
| `POS_SCORE_WEIGHT` | `false` | `true` = score-weighted sizing (higher-score stocks get more capital). `false` = rank-weighted |
| `HOLD_DAYS_MULT` | 1.0 | Multiplier on max_hold_days per regime. Default 1.0 = config values: Bull 7d, Normal 5d, Volatile 4d, Bear 2d |
| `POSITION_SIZING_ALGORITHM` | `true` | `true` = max 25% per position (~10%/slot). `false` = use all available cash |
| `REVIEW_COMPOUND_SIZING` | `false` | `true` = position size scales with the account instead of the fixed initial capital (the 600k ceiling). Measured 157.52% → 193.54% (maxDD -1.75% → -1.80%). **Changes real position sizes on real money** — `cli.py` is the same path that generates live orders. Read by the shared CLI sizing path, so it applies to whichever strategy runs |

### Buy/Sell Filters

| Variable | Default | Description |
|---|---|---|
| `SKIP_GAPS_DOWN_OPEN_PRICE` | `false` | `true` = skip buy if open < yesterday's close. **Caution:** drops returns from 56.4% to 29% by missing dip-buy opportunities |
| `INDEX_TREND_FILTER` | `false` | `true` = skip buys when CSI 300 below 10-day MA |
| `ER_EXIT_ENABLED` | `true` | `true` = Kaufman Efficiency Ratio exit: sell when ER > 0.7 + profit > 3% + price rising |
| `BALANCE_PRICE_RATIO` | 0.0 | 0.0 = buy at market open. 1.0 = strict limit price entry |
| `BUY_OPEN_PRICE` | `true` | `true` = buy at the day's open, unconditionally (baseline). `false` = simulate a pre-market limit order (fills between buy_price and open, or not at all) |
| `SELL_OPEN_PRICE` | `true` | `true` = sell **exactly at the TP/SL price** whenever the day's high/low touches it. `false` = gap-aware: if the day opens through the TP/SL, fill at the **open** instead. **Use `false` for realistic results** — with `true`, 143 of 575 stops in the 193.58% run are booked above the day's high (see `docs/STRATEGIES.md`) |
| `SELL_SLIPPAGE_PCT` | `0.0` | Sell-side slippage as a fraction (`0.002` = 0.2%). A-share reality is 0.2-0.8%. The cost is large here because turnover is ~219x the account |
| `BUY_SLIPPAGE_PCT` | `0.0` | Buy-side slippage (usually 0) |
| `BACKTEST_BUY_OPEN_PRICE` | — | Legacy, superseded by `BUY_OPEN_PRICE` + `SELL_OPEN_PRICE`; still serves as the fallback for both |
| `SWITCH_INDEX_COMBINE_MA` | `false` | `true` = use CSI500+MA20 for regime. `false` = SSE+MA120 (default, avoids over-detecting bears) |
| `START_REAL_TRADING_DATE` | `2026-06-29` | Cutoff date for real trading sync |

---

## Trading (Mobile App)

| Variable | Required | Used By | Description |
|---|---|---|---|
| `GUOTAI_PACKAGE_NAME`* | Yes | Trading | Android package: `com.guotai.dazhihui` |
| `GUOTAI_PASSWORD`* | Yes | Trading | Trading account PIN (6 digits) |

---

## Infrastructure

| Variable | Used By | Description |
|---|---|---|
| `RG_PATH` | Utils | Path to ripgrep binary |
| `GRPC_TRACE` | Utils | gRPC trace flags |
| `GRPC_VERBOSITY` | Utils | gRPC log level |
| `ZENMUX_PLATFORM_API_KEY` | Utils | ZenMux platform key |
| `DEEPSEEK_AUTH_TOKEN` | Utils | DeepSeek web auth token |
| `CLOUDFLARE_API_TOKEN` | Utils | Cloudflare API token |
| `OXY_WSA_USERNAME` | Utils | Oxylabs Web Scraper API username |
| `OXY_WSA_PASSWORD` | Utils | Oxylabs Web Scraper API password |
