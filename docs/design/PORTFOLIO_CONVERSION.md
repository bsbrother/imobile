# Portfolio Page - Reflex Conversion

## Overview

This document describes the conversion of the Next.js/React portfolio tracking interface to Reflex framework.

## Original Design

The original design was located in `docs/design/v0.dev_nextjs/` and consisted of:

1. **Sidebar** (`components/sidebar.tsx`) - Collapsible navigation with theme toggle
2. **Market Stats** (`components/market-stats.tsx`) - Portfolio overview statistics
3. **Stock Table** (`components/stock-table.tsx`) - Detailed stock holdings table

## Reflex Implementation

### File Structure

```
imobile/
├── components/
│   ├── __init__.py
│   ├── sidebar.py          # Collapsible sidebar with navigation
│   ├── market_stats.py     # Portfolio statistics display
│   └── stock_table.py      # Stock holdings table
├── pages/
│   ├── __init__.py
│   └── portfolio.py        # Main portfolio page
├── states/
│   ├── __init__.py
│   └── portfolio_state.py  # State management
└── imobile.py              # App initialization and routing
```

### Components

#### 1. Sidebar (`components/sidebar.py`)

**Features:**
- Collapsible design (64px collapsed, 224px expanded)
- Navigation menu items (首页, 行情, 持仓, 交易)
- Theme toggle (dark/light mode)
- Settings button
- Smooth transitions

**State Integration:**
- `is_sidebar_expanded` - Controls sidebar width
- `is_dark_mode` - Controls theme
- `active_menu` - Tracks active navigation item

#### 2. Market Stats (`components/market_stats.py`)

**Features:**
- Large market value display with red color (#e74c3c)
- Grid layout with 6 statistics:
  - 今日变化 (Today's Change)
  - 浮动盘变化 (Float Change)
  - 累计变化 (Cumulative Change)
  - 总资产 (Total Assets)
  - 现金 (Cash)
  - 本金 (Principal)
- Responsive grid (4 columns on desktop, 2 on mobile)

#### 3. Stock Table (`components/stock_table.py`)

**Features:**
- Table with 9 columns:
  - 名称/代码 (Name/Code)
  - 现价 (Current Price)
  - 涨跌 (Change)
  - 市值 (Market Value)
  - 持仓 (Holdings)
  - 成本/成本 (Cost)
  - 浮动盘变化 (Float Change)
  - 累计变化 (Cumulative Change)
  - 操作 (Actions)
- Color-coded changes (red for positive, green for negative)
- Action buttons (记录, 卖出, delete)
- Tabular numbers for proper alignment

#### 4. Portfolio State (`states/portfolio_state.py`)

**State Variables:**
- `is_sidebar_expanded: bool` - Sidebar expansion state
- `is_dark_mode: bool` - Theme state
- `active_menu: str` - Active navigation item
- Market statistics (total_market_value, changes, etc.)
- `stocks: List[Stock]` - Stock holdings data

**Event Handlers:**
- `toggle_sidebar()` - Toggle sidebar expansion
- `toggle_theme()` - Toggle dark/light mode
- `set_active_menu(menu_item)` - Set active navigation
- `remove_stock(stock_code)` - Remove stock from portfolio

### Styling

**Theme Configuration:**
```python
app = rx.App(
    theme=rx.theme(
        appearance="dark",
        has_background=True,
        radius="large",
        accent_color="blue",
    ),
)
```

**Custom CSS:** `assets/custom.css`
- Responsive breakpoints for mobile/tablet/desktop
- Smooth transitions
- Custom scrollbars
- Tabular number formatting
- Color coding for positive/negative values

### Responsive Design

**Mobile (<768px):**
- Sidebar can be toggled (hidden by default)
- Stats grid changes to 2 columns
- Table becomes scrollable horizontally
- Adjusted font sizes

**Tablet (768px - 1024px):**
- Sidebar visible and collapsible
- Stats grid at 4 columns
- Full table view

**Desktop (>1024px):**
- Full layout with expanded sidebar option
- Optimized spacing and typography

## Key Differences from Next.js Version

1. **State Management:**
   - React: useState hooks in components
   - Reflex: Centralized PortfolioState class

2. **Styling:**
   - React: Tailwind CSS classes
   - Reflex: Radix theme system + custom CSS

3. **Components:**
   - React: JSX with TypeScript
   - Reflex: Python functions returning rx.Component

4. **Event Handling:**
   - React: onClick handlers
   - Reflex: on_click with state methods

5. **Conditional Rendering:**
   - React: Ternary operators and &&
   - Reflex: rx.cond() and rx.foreach()

## Usage

### Running the App

```bash
# Start the development server
reflex run

# Access the portfolio page
http://localhost:3000/portfolio
```

### Adding New Stocks

Modify `states/portfolio_state.py` and add new Stock objects to the `stocks` list:

```python
stocks: List[Stock] = [
    Stock(
        name="股票名称",
        code="股票代码",
        price=10.0,
        # ... other fields
    ),
]
```

### Customizing Theme

Modify the theme in `imobile.py`:

```python
app = rx.App(
    theme=rx.theme(
        appearance="light",  # or "dark"
        accent_color="red",  # Change accent color
    ),
)
```

## Testing

Run the application and verify:
- [ ] Sidebar expands/collapses smoothly
- [ ] Theme toggle works
- [ ] Navigation items highlight on click
- [ ] Stock data displays correctly
- [ ] Table actions work (remove stock)
- [ ] Responsive design works on mobile/tablet/desktop
- [ ] Colors display correctly (red for gains, green for losses)

## Future Enhancements

1. Add real-time stock data integration
2. Implement add stock functionality
3. Add stock detail view
4. Implement transaction history
5. Add charts and visualizations
6. User authentication and multiple portfolios
7. Export/import portfolio data
8. Performance analytics and reports

## References

- [Reflex Documentation](https://reflex.dev/docs/getting-started/introduction/)
- [Radix Themes](https://www.radix-ui.com/themes/docs/overview/getting-started)
- Original Next.js design: `docs/design/v0.dev_nextjs/`
