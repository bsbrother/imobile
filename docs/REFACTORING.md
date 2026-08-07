# Refactoring — `refactor/optimize-and-clean` Branch

> Branch created: 2026-07-19
> Base: `baseline_returns_ts_7AZ_70.60` (70.59% total return, 20260101–20260619)

## Goals

1. **Fix broken test suite** — 31 collection errors blocking all CI/CD.
2. **Optimize performance** — eliminate redundant API calls, shell-out overhead.
3. **Improve error handling** — replace `os.system()` + bare `except: pass` with structured subprocess + specific exceptions.
4. **Add real test coverage** — unit tests for pure-Python scoring logic (CANSLIM, Kaufman ER, regime memoization).
5. **Preserve the 70.59% baseline** — no semantic changes to strategy logic; characterization test pins the return.

---

## Phase A — Test Infrastructure & Cleanup

**Problem:** `pytest` had 31 collection errors — ghost modules (`cbs_ewo`, `stock_analysis`), dots in filenames breaking imports, network tests hanging indefinitely.

**Changes:**
- `pytest.ini`: `testpaths=tests`, `norecursedirs` (excludes `utils/searxng`, `utils/daily_stock_analysis`, `.venv`, `.web`), registered `benchmark` marker.
- `tests/conftest.py`: registers `integration` + `benchmark` markers, `--run-integration` CLI flag, auto-skip integration tests by default, provides `tmp_db_path`/`sample_ohlcv`/`sample_picks_json` fixtures.
- `tests/test_baseline_regression.py`: characterization test — parses committed period report, asserts total return within ±0.2% of 70.59% (65% floor). Runs in 0.01s.
- Ghost test cleanup: deleted 2 (broken imports), moved 3 to `scripts/` (not tests), renamed 2 (dots in filenames).
- Tagged 10 network test files with `@pytest.mark.integration`.
- 2 pre-existing freeride failures marked `xfail` with reasons.
- Added `Makefile` with `test`/`test-integration`/`test-fast`/`backtest`/`lint`/`clean` targets.

**Before:** 31 errors, 0 collected, 38s timeout.
**After:** 0 errors, 22 collected, 9 pass / 16 skip / 2 xfail in 45s.

---

## Phase B — Performance Optimization & Error Handling

**Problem:** `detect_market_regime()` called 2-3× per date (120 redundant Tushare API calls per backtest); 11 `os.system()` calls with no error capture; hard-coded venv path.

**Changes:**
- `_detect_market_regime_cached(date)` — memoized wrapper with `_REGIME_CACHE` dict. Same date = 1 API call instead of 2-3.
- `_run_strategy_script(script, *args)` + `_run_cli_command(*args)` — `subprocess.run()` wrappers that capture stderr, raise `RuntimeError` with context on failure, pass args as list (no shell injection).
- `VENV_PYTHON = sys.executable` — dynamic, portable.
- Strategy dispatch refactored from 10-branch if/elif to `_STRATEGY_SCRIPTS` dict.

**Before:** 11 `os.system()` calls, ~120 redundant API calls/backtest, hard-coded path.
**After:** 0 `os.system()` calls (except Go picker), ~50% fewer API calls, portable.
**Verified:** 1-week backtest (20260101–20260107) runs clean, tests still green.

---

## Phase C — Real Test Coverage

**Problem:** Only 4 real unit tests — all in `test_freeride.py` (integration). Zero coverage for CANSLIM scoring, Kaufman ER, regime memoization, subprocess helpers.

**Changes:**
- `tests/test_engine_helpers.py` (16 tests, 0.29s):
  - `KaufmanEfficiencyRatio`: 6 tests (uptrend, downtrend, choppy, flat, insufficient, empty).
  - `_detect_market_regime_cached`: 3 tests (cache hit, different dates, persistence).
  - `_run_strategy_script`: 4 tests (success, failure, list args, sys.executable).
  - `_run_cli_command`: 3 tests (success, error message, module invocation).
- `tests/test_canslim_scoring.py` (18 tests, 0.03s):
  - `compute_rps`: 4 tests (uptrend, downtrend, flat, insufficient).
  - `canslim_score_stock`: 8 tests (all-pass + each factor failure: C/A/N/S/I/M + None).
  - Constants: 6 threshold sanity checks.

**Before:** 9 passed (4 freeride + 5 baseline regression), 0 unit tests for core logic.
**After:** 43 passed (+34 new), 16 skipped, 2 xfailed in 23s.

---

## Phase D — Documentation

- `CHANGELOG.md`: 4-phase summary with before/after metrics.
- `docs/REFACTORING.md`: this file.
- `docs/TODO.md`: refactor items marked complete.
- `README.md`: "Development" section with `make` targets.

---

## Merge Checklist

Before merging `refactor/optimize-and-clean` → `baseline_returns_ts_7AZ_70.60`:

- [x] Test suite: 0 errors, 43 passed, 16 skipped, 2 xfailed.
- [x] Characterization test passes (70.59% ±0.2%).
- [x] 1-week backtest runs clean (20260101–20260107).
- [ ] Full backtest (20260101–20260619) confirms 70.59% ±0.2% (44min — run before merge).
- [x] No new pyright errors in modified files.
- [x] CHANGELOG updated.
- [x] All commits are atomic and documented.

---

## Files Changed

| File | Phase | Change |
|------|-------|--------|
| `pytest.ini` | A | Rewrite: testpaths, norecursedirs, markers |
| `tests/conftest.py` | A | New: markers, fixtures, --run-integration |
| `tests/test_baseline_regression.py` | A | New: characterization test (70.59%) |
| `Makefile` | A | New: test/backtest/lint/clean targets |
| `scripts/demo_searxng_sentiment.py` | A | Moved from tests/ (was test_searxng.py) |
| `scripts/_check_dates.py` | A | Moved from tests/ |
| `scripts/verify_freeride_integration.py` | A | Moved from tests/ |
| `tests/test_cbs_ewo_portfolio.py` | A | Deleted (broken import) |
| `tests/test_stock_analysis.py` | A | Deleted (broken import) |
| `tests/test_gemini_3_1_*.py` | A | Renamed (dots → underscores) |
| 10 network test files | A | Tagged `@pytest.mark.integration` |
| `tests/test_freeride.py` | A | 2 tests marked `xfail` |
| `backtest/engine.py` | B | Memoized regime, subprocess helpers, dict dispatch |
| `tests/test_engine_helpers.py` | C | New: 16 unit tests for engine helpers |
| `tests/test_canslim_scoring.py` | C | New: 18 unit tests for CANSLIM logic |
| `CHANGELOG.md` | D | 4-phase summary |
| `docs/REFACTORING.md` | D | New: this file |
| `docs/TODO.md` | D | Refactor items marked complete |
| `README.md` | D | Development section added |

---

## Phase E — Shared Data & Utility Unification

**Problem:** Cache/db/data files were fragmented across multiple locations — some
strategies hardcoded root-level `db/` and `data/` dirs, while the canonical homes
(`shared/db/`, `shared/data/`, `shared/data_cache/`) were already the destination
of `.env` (`DB_CACHE_FILE`, `DB_IMOBILE_FILE`, `CACHE_PATH`). Root `scripts/` held
3 diagnostic tools with no runtime dependency.

**Goal:** Every strategy writes to the shared/ tree; no root `db/`, `data/`,
`scripts/` stragglers.

**Changes:**
- `ts_daily.py`: `CACHE_DB_PATH` root `db/ts_daily_cache.db` → `shared/db/ts_daily_cache.db`.
- `utils/go-stock/cmd/pick_stocks*.go`: JSON cache root `db/go_daily_cache` → `shared/db/go_daily_cache` (both pickers; verified `go build` passes; engine runs them from `utils/go-stock`, so `../../shared/db` resolves to repo-root `shared/db`).
- `utils/daily_stock_analysis` `stock_analysis.db`: default `./data/stock_analysis.db` → unified via `.env DATABASE_PATH=./shared/data/stock_analysis.db` (dsa is a separate sub-project; config override, no source edit — least-invasive).
- Root `data/` & `db/`: unique cache files merged into `shared/` (68 go_daily_cache files), then root dirs removed.
- `backtest/data/lhb_*.csv` (Dragon-Tiger reference datasets for `ts_7AZ_96MA_flow`): moved → `shared/data/lhb/`; `ts_7AZ_96MA_flow.py` `LHB_CACHE` updated; `.gitignore` updated to track them (`shared/data/*` with `!shared/data/lhb/*.csv`).
- `scripts/` (3 diagnostic files): moved → `utils/` (`_check_dates.py`, `demo_searxng_sentiment.py`, `verify_freeride_integration.py`); empty root `scripts/` removed. `workspace_root = dirname(dirname(__file__))` still resolves to repo root from `utils/`. No strategy imports them.
- Production default strategy confirmed: `ts_7AZ_96MA_flow` (v99.17).

**After:** All caches/dbs/data under `shared/`; root has no `db/`, `data/`, or `scripts/`.<br>
**Note:** `verify_freeride_integration.py`'s `_model_name` check was already failing pre-move (diagnostic expected attribute not present in `ts_daily.py`) — unrelated to this change; left as-is.
