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
| `HOLD_DAYS_MULT` | 0.5 | Multiplier on max_hold_days per regime. 0.5 = 50% shorter: Bull 7d, Normal 5d, Volatile 4d, Bear 2d |
| `POSITION_SIZING_ALGORITHM` | `true` | `true` = max 25% per position (~10%/slot). `false` = use all available cash |

### Buy/Sell Filters

| Variable | Default | Description |
|---|---|---|
| `SKIP_GAPS_DOWN_OPEN_PRICE` | `false` | `true` = skip buy if open < yesterday's close. **Caution:** drops returns from 56.4% to 29% by missing dip-buy opportunities |
| `INDEX_TREND_FILTER` | `false` | `true` = skip buys when CSI 300 below 10-day MA |
| `ER_EXIT_ENABLED` | `true` | `true` = Kaufman Efficiency Ratio exit: sell when ER > 0.7 + profit > 3% + price rising |
| `BALANCE_PRICE_RATIO` | 0.0 | 0.0 = buy at market open. 1.0 = strict limit price entry |
| `BACKTEST_BUY_OPEN_PRICE` | `true` | `true` = buy at open price (baseline 70.60%). `false` = buy at limit-up level (40.86%) |
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
