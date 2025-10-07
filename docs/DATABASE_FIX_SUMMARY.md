# Database Schema Fix Summary

## Date: 2025-10-07

## Problem
The test `python tests/test_sync_app_data.py` was failing with database errors:
1. `no such column: last_updated` in market_indices table
2. `table total_table has no column named position_percent`
3. `table stocks_table has no column named available_shares`
4. UNIQUE constraint failures due to improper handling of stock code/name conflicts

## Root Cause
1. The migration script `db/migrations/add_realtime_fields.sql` existed but was not applied to the database
2. The base schema file `db/imobile.sql` was outdated and didn't reflect the migration changes
3. The `sync_app_data_to_db` function in `app_guotai.py` wasn't handling UNIQUE constraint conflicts properly when stocks existed with different code formats (e.g., "000670" vs "SZ000670")

## Solution

### 1. Applied Database Migration
Applied the migration script to add missing columns:
```bash
sqlite3 imobile.db < db/migrations/add_realtime_fields.sql
```

Added columns:
- `total_table`: `position_percent`, `withdrawable`, `last_updated`
- `stocks_table`: `available_shares`, `last_updated`
- Created indexes on `last_updated` columns

### 2. Updated Base Schema
Updated `db/imobile.sql` to reflect the migration:
- Added new columns to table definitions
- Added new indexes for `last_updated` columns
- Updated sample data INSERT statements to include NULL values for new columns

### 3. Fixed UNIQUE Constraint Handling in app_guotai.py
Modified the stock quote update logic to handle both UNIQUE constraints:
- First attempts to UPDATE by stock name (handles different code formats)
- If no match by name, performs UPSERT by code using `ON CONFLICT`
- This prevents failures when stocks exist with different code formats

**Before:**
```python
# Simple UPSERT that only handled code conflicts
INSERT ... ON CONFLICT(user_id, code) DO UPDATE ...
```

**After:**
```python
# First try update by name
UPDATE stocks_table ... WHERE user_id = ? AND name = ?

# If no match, then upsert by code
if cursor.rowcount == 0:
    INSERT ... ON CONFLICT(user_id, code) DO UPDATE ...
```

## Files Modified

1. **db/imobile.sql**
   - Added `position_percent`, `withdrawable`, `last_updated` to `total_table` schema
   - Added `available_shares`, `last_updated` to `stocks_table` schema
   - Added indexes for new `last_updated` columns
   - Updated INSERT statements with NULL values for new columns

2. **app_guotai.py**
   - Modified `sync_app_data_to_db()` function
   - Changed stock quote update logic to handle both name and code UNIQUE constraints
   - Now properly updates existing stocks even when code formats differ

## Test Results

All tests now pass successfully:
```
✅ PASSED - Quote only test
✅ PASSED - Position only test
```

### Verified Data Updates:
- **Market Indices**: Successfully updated 3 indices with timestamps
- **Stock Quotes**: Updated 12 stocks with current prices and change percentages
- **Portfolio Summary**: Updated total_assets (855169.66), position_percent (95.28%), withdrawable (40330.66)
- **Stock Positions**: Updated 12 stock positions with holdings, available shares, and P&L data

## Database Schema After Fix

### total_table
```sql
CREATE TABLE total_table (
    id INTEGER PRIMARY KEY,
    user_id INTEGER UNIQUE,
    total_market_value FLOAT,
    today_pnl FLOAT,
    today_pnl_percent FLOAT,
    cumulative_pnl FLOAT,
    cumulative_pnl_percent FLOAT,
    cash FLOAT,
    floating_pnl_summary FLOAT,
    floating_pnl_summary_percent FLOAT,
    total_assets FLOAT,
    principal FLOAT,
    position_percent FLOAT,        -- NEW
    withdrawable FLOAT,             -- NEW
    last_updated DATETIME,          -- NEW
    FOREIGN KEY(user_id) REFERENCES users (id)
);
```

### stocks_table
```sql
CREATE TABLE stocks_table (
    id INTEGER PRIMARY KEY,
    user_id INTEGER,
    code VARCHAR,
    name VARCHAR,
    current_price FLOAT,
    change FLOAT,
    change_percent FLOAT,
    market_value FLOAT,
    holdings INTEGER,
    cost_basis_diluted FLOAT,
    cost_basis_total FLOAT,
    pnl_float FLOAT,
    pnl_float_percent FLOAT,
    pnl_cumulative FLOAT,
    pnl_cumulative_percent FLOAT,
    available_shares INTEGER,       -- NEW
    last_updated DATETIME,          -- NEW
    UNIQUE (user_id, code),
    UNIQUE (user_id, name),
    FOREIGN KEY(user_id) REFERENCES users (id)
);
```

### market_indices
```sql
CREATE TABLE market_indices (
    id INTEGER PRIMARY KEY,
    index_code VARCHAR UNIQUE,
    index_name VARCHAR,
    current_value FLOAT,
    change FLOAT,
    change_percent FLOAT,
    last_updated DATETIME,          -- Already existed
);
```

## Next Steps

1. ✅ All database schema issues are resolved
2. ✅ All tests are passing
3. ✅ Documentation updated
4. Consider running `reflex db migrate` to ensure Alembic migrations are in sync
5. Consider adding more test cases for edge scenarios (empty data, malformed CSV, etc.)
