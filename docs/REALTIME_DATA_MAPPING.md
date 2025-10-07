# Real-time Data Mapping from Mobile App to Database

## Overview

This document describes how real-time data from the Guotai mobile app is mapped to the database schema.

## Data Sources

### 1. Quote Page Data (`get_from_app_quote_page()`)

Returns CSV format data with two sections:

#### Section 1: Market Indices
```csv
Index Name,Index Number,Index Ratio
shanghai,3882.78,+0.52%
shenzhen,13526.51,+0.35%
chinext,3238.16,0.00%
```

#### Section 2: Stock Quotes
```csv
Stock_Name(Code),Latest_Price,Increase_Percentage(%),Increase_Amount
中科三环(000970),14.17,+1.21%,+0.17
```

### 2. Position Page Data (`get_from_app_position_page()`)

Returns CSV format data with two sections:

#### Section 1: Portfolio Summary
```csv
浮动盈亏,账户资产,总市值,仓位,可用,可取
-361757.86,855169.66,814839.00,95.28%,40330.66,40330.66
```

Field Descriptions:
- **浮动盈亏** (Floating Profit/Loss): Current unrealized P&L
- **账户资产** (Account Assets): Total account value
- **总市值** (Market Cap): Total market value of holdings
- **仓位** (Positions): Position percentage (e.g., 95.28%)
- **可用** (Available): Available cash for trading
- **可取** (Desirable/Withdrawable): Cash available for withdrawal

#### Section 2: Stock Positions
```csv
stock_name,market_cap,open,available,current_price,cost,floating_profit,floating_loss(%)
深振业Ａ,385875.000,37500,37500,10.290,13.361,-115165.77,-22.99%
```

Field Descriptions:
- **stock_name**: Stock name
- **market_cap**: Total market value of position
- **open**: Total shares held
- **available**: Shares available for trading
- **current_price**: Current price per share
- **cost**: Cost basis per share
- **floating_profit**: Unrealized profit/loss amount
- **floating_loss(%)**: Unrealized profit/loss percentage

## Database Schema Mapping

### Table: `market_indices`

| App Field | Database Field | Type | Transformation |
|-----------|---------------|------|----------------|
| Index Name | index_name | VARCHAR | Direct |
| Index Name | index_code | VARCHAR | Mapped via dictionary (shanghai→sh000001, etc.) |
| Index Number | current_value | FLOAT | Parse number |
| Index Ratio | change_percent | FLOAT | Parse percentage (+0.52%→0.52) |
| - | last_updated | DATETIME | Current timestamp |

### Table: `total_table`

| App Field | Database Field | Type | Transformation |
|-----------|---------------|------|----------------|
| 浮动盈亏 | floating_pnl_summary | FLOAT | Parse number |
| 账户资产 | total_assets | FLOAT | Parse number |
| 总市值 | total_market_value | FLOAT | Parse number |
| 仓位 | position_percent | FLOAT | Parse percentage (95.28%→95.28) ⭐ NEW |
| 可用 | cash | FLOAT | Parse number |
| 可取 | withdrawable | FLOAT | Parse number ⭐ NEW |
| Calculated | floating_pnl_summary_percent | FLOAT | (floating_pnl / market_value) * 100 |
| - | last_updated | DATETIME | Current timestamp ⭐ NEW |

### Table: `stocks_table`

#### From Quote Page:
| App Field | Database Field | Type | Transformation |
|-----------|---------------|------|----------------|
| Stock_Name(Code) | name | VARCHAR | Extract name from "中科三环(000970)" |
| Stock_Name(Code) | code | VARCHAR | Extract code from "中科三环(000970)" |
| Latest_Price | current_price | FLOAT | Parse number |
| Increase_Amount | change | FLOAT | Parse number (+0.17→0.17) |
| Increase_Percentage | change_percent | FLOAT | Parse percentage (+1.21%→1.21) |

#### From Position Page:
| App Field | Database Field | Type | Transformation |
|-----------|---------------|------|----------------|
| stock_name | name | VARCHAR | Direct |
| market_cap | market_value | FLOAT | Parse number |
| open | holdings | INTEGER | Parse and convert to int |
| available | available_shares | INTEGER | Parse and convert to int ⭐ NEW |
| current_price | current_price | FLOAT | Parse number |
| cost | cost_basis_diluted | FLOAT | Parse number |
| cost | cost_basis_total | FLOAT | Parse number (same as diluted) |
| floating_profit | pnl_float | FLOAT | Parse number |
| floating_loss(%) | pnl_float_percent | FLOAT | Parse percentage (-22.99%→-22.99) |
| - | last_updated | DATETIME | Current timestamp ⭐ NEW |

## Database Schema Updates Required

Run the migration script: `db/migrations/add_realtime_fields.sql`

```sql
-- Add new fields to total_table
ALTER TABLE total_table ADD COLUMN position_percent FLOAT;
ALTER TABLE total_table ADD COLUMN withdrawable FLOAT;
ALTER TABLE total_table ADD COLUMN last_updated DATETIME;

-- Add new fields to stocks_table
ALTER TABLE stocks_table ADD COLUMN available_shares INTEGER;
ALTER TABLE stocks_table ADD COLUMN last_updated DATETIME;

-- Create indexes
CREATE INDEX ix_stocks_table_last_updated ON stocks_table (last_updated);
CREATE INDEX ix_total_table_last_updated ON total_table (last_updated);
```

## Usage Example

```python
# Get data from mobile app
quote_data = get_from_app_quote_page()
position_data = get_from_app_position_page()

# Save to database
result = save_app_data_to_db(
    quote_data=quote_data,
    position_data=position_data,
    user_id=1,
    db_path='imobile.db'
)

print(f"Success: {result['success']}")
print(f"Message: {result['message']}")
print(f"Indices updated: {result['indices_updated']}")
print(f"Stocks updated: {result['stocks_updated']}")
print(f"Total updated: {result['total_updated']}")
```

## Data Flow

```
Mobile App (Guotai)
    ↓
DroidAgent (via ADB)
    ↓
CSV Format String
    ↓
parse_csv_data()
    ↓
extract_stock_code()
parse_percentage()
parse_number()
    ↓
save_app_data_to_db()
    ↓
SQLite Database (imobile.db)
```

## Notes

1. **Upsert Logic**: The function uses `INSERT ... ON CONFLICT ... DO UPDATE` for market_indices and total_table (which have unique constraints), and UPDATE followed by INSERT for stocks_table.

2. **Stock Matching**: Stocks are matched by `code` field when updating from quote page, and by `name` field when updating from position page.

3. **Timestamp Tracking**: All updates include a `last_updated` timestamp to track data freshness.

4. **Error Handling**: The function rolls back the transaction on any error and returns detailed error information.

5. **Default User**: The default user_id is 1 (demo@example.com).

5. **Error Handling**: Comprehensive error handling with rollback on failure.

6. **Database Location**: By default, uses `imobile.db` in the same directory as the script.

## Notes
