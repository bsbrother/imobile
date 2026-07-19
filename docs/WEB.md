# Web Module — iMobile Stock Portfolio Dashboard

The `web/` module provides a comprehensive **portfolio tracking and analysis dashboard** built with [Reflex](https://reflex.dev/), a full-stack Python framework. It visualizes data synced from the mobile real trading system (`trading/` module) and provides real-time insights into market conditions and portfolio performance.

---

## Table of Contents

- [Main Features](#main-features)
- [Module Structure](#module-structure)
- [Architecture & Data Flow](#architecture--data-flow)
- [Pre-Requirements](#pre-requirements)
- [Configuration](#configuration)
- [Usage](#usage)
- [Core Components](#core-components)
  - [Portfolio Page (`/portfolio`)](#portfolio-page-portfolio)
  - [Sector History Page (`/sector-history`)](#sector-history-page-sector-history)
- [Database Schema](#database-schema)
- [API Endpoints](#api-endpoints)

---

## Main Features

| Feature | Description |
|---|---|
| **Real-time Portfolio Overview** | Tracks total market value, cash, principal, total assets, daily P&L, and cumulative P&L. |
| **Holdings Table** | Detailed view of all current holdings (price, market value, P&L, available shares) with sortable columns. |
| **Market Stats** | Live tracking of major indices (e.g., SSE Composite) and personal portfolio metrics. |
| **Sector History Analysis** | Interactive tool to explore historical hot sectors, view top stocks in sectors, and visualize sector trends. |
| **Trading Operations Integration** | Direct links to analysis reports and operation commands (smart orders) for each holding. |
| **Responsive Design** | Fully responsive dark-mode UI with mobile sidebar support, built using Radix UI via Reflex. |
| **Data Sync** | Reads directly from the shared SQLite database (`shared/db/imobile.db`) populated by the trading/backtest engines. |

---

## Module Structure

```
web/
├── rxconfig.py             # Reflex app configuration (db URL, theme, plugins)
├── requirements.txt        # Web-specific dependencies (Reflex, Plotly, Pandas, etc.)
├── alembic.ini             # Alembic config for database migrations
│
├── app/
│   ├── app.py              # Main app entry point, route definitions
│   ├── db.py               # Database schema definitions (SQLModel/SQLAlchemy)
│   ├── api.py              # Custom FastAPI endpoints (for serving files)
│   │
│   ├── components/         # Reusable UI components
│   │   ├── market_stats.py # Top stats row (market value, daily P&L, indices)
│   │   ├── sidebar.py      # Navigation sidebar (desktop & mobile)
│   │   └── stock_table.py  # Data table for current holdings
│   │
│   ├── pages/              # App routes/views
│   │   ├── __init__.py
│   │   ├── portfolio.py    # Main portfolio dashboard (`/portfolio`)
│   │   └── sector_history.py # Sector analysis tool (`/sector-history`)
│   │
│   ├── states/             # Reflex state management
│   │   ├── __init__.py
│   │   └── portfolio_state.py # State for portfolio data loading and UI toggles
│   │
│   └── utils/              # Web-specific utilities
│       ├── __init__.py
│       └── stock_info.py   # Helpers for stock code formatting
│
└── assets/                 # Static assets (images, CSS, symlinked reports)
```

---

## Architecture & Data Flow

The web dashboard is designed as a **read-heavy presentation layer** on top of the shared `imobile.db` database.

1.  **Data Ingestion:** The `trading/` module (specifically `db_sync.py`) continuously updates `shared/db/imobile.db` with live account data from the mobile brokerage app.
2.  **State Management:** Reflex state classes (e.g., `PortfolioState`) query this SQLite database using SQLAlchemy when the user loads the page or triggers a refresh.
3.  **UI Rendering:** Reflex compiles the Python UI components into a React frontend and manages the WebSocket connection between the frontend and the Python backend state.
4.  **File Serving:** Custom FastAPI endpoints (`api.py`) and dynamic symlinks in `assets/` serve AI-generated stock analysis reports and smart order command files from the `/tmp` directory.

---

## Pre-Requirements

### Python Environment

The web module requires Reflex and data analysis libraries. From the root directory:

```bash
cd web
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Key packages:
- `reflex`: The core full-stack framework.
- `sqlmodel` / `sqlalchemy`: Database ORM.
- `pandas`, `tushare`, `plotly`, `ta`: Used in the sector history analysis tool.

### Environment Variables

If using the Sector History feature, you need a Tushare token set in the `.env` file (loaded from the root directory or web directory):

```
TUSHARE_TOKEN=your_tushare_token_here
```

---

## Configuration

### `rxconfig.py`

This file configures the Reflex app instance:

-   **Database URL:** Points to the shared SQLite database: `sqlite:///../shared/db/imobile.db`
-   **Theme:** Uses Radix UI's Dark mode with a large border radius and blue accent color.
-   **Plugins:** Incorporates Tailwind CSS for custom styling and a Sitemap plugin.

---

## Usage

### Running the Dashboard

To start the Reflex development server:

```bash
cd web
reflex run
```

The dashboard will be available at `http://localhost:3000`.

### Database Migrations (if modifying schema)

If you modify `app/db.py`, you need to generate and apply migrations:

```bash
reflex db makemigrations --message "description of changes"
reflex db migrate
```

---

## Core Components

### Portfolio Page (`/portfolio`)

The central dashboard view.

*   **State (`app/states/portfolio_state.py`):**
    *   Loads data from `summary_account`, `market_indices`, and `holding_stocks` tables.
    *   Handles UI state (sidebar visibility, dark mode).
    *   Provides sorting functionality for the holdings table.
    *   Generates dynamic symlinks to view stock analysis reports and operation commands.
*   **Market Stats (`app/components/market_stats.py`):** Displays top-level metrics (Total Market Value, Total Assets, Daily Change, Position Percentage, etc.) and top market indices.
*   **Stock Table (`app/components/stock_table.py`):** A responsive, sortable table listing all current holdings with their cost basis, current price, float P&L, and available shares (T+1 aware).

### Sector History Page (`/sector-history`)

An analytical tool for exploring market trends.

*   **State (`app/pages/sector_history.py`):**
    *   Uses `tushare` to fetch historical daily hot sectors (`ths_daily`).
    *   Uses `adata` to fetch real-time "Dongfang Caifu" (DC) concept sector data.
    *   Calculates MACD indicators (`ta.trend.MACD`) and plots interactive candlestick charts using Plotly.
    *   Fetches the top 10 stocks within a selected sector.
*   **UI:** A split-pane view. The left pane lists dates and hot sectors. The right pane displays a detailed interactive Plotly chart (candlesticks + SSE overlay + MACD) and the top stocks for the selected sector.

---

## Database Schema

The web module reads from models defined in `app/db.py`, which map to tables in `imobile.db`:

| Model | Table | Purpose |
|---|---|---|
| `SummaryAccount` | `summary_account` | High-level account metrics (total assets, daily P&L, cash). |
| `HoldingStock` | `holding_stocks` | Details for currently held stocks (cost, current price, P&L). |
| `MarketIndex` | `market_indices` | Live data for major indices (SSE, CSI300). |
| `SuggestionStock` | `suggestion_stocks` | AI/Strategy picked stocks and their smart order parameters. |
| `SmartOrder` | `smart_orders` | Execution details for planned trades. |
| `Transaction` | `transactions` | Historical buy/sell records. |
| `AppConfig` | `app_config` | User preferences and app settings. |

*Note: The web app primarily reads from these tables. Writes are mostly handled by the `trading` module.*

---

## API Endpoints

### `api.py`

Provides custom FastAPI endpoints to serve dynamically generated files that sit outside the standard Reflex `assets/` directory.

*   `get_stock_file(stock_code: str, file_type: str)`
    *   Serves analysis reports (`report.html`) and command logs (`cmd.md`) from `/tmp/{stock_code}/`.
    *   Used in conjunction with symlinking in `PortfolioState` to provide in-browser viewing of trade rationale and execution plans.
