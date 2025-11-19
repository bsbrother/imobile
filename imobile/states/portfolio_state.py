"""State management for portfolio page."""
import reflex as rx
from typing import List, Optional
from pydantic import BaseModel, ConfigDict
from imobile.utils.stock_info import add_suffix_to_stock_code


class MarketIndex(BaseModel):
    """Market index data model for display."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    index_code: str
    index_name: str
    current_value: float
    change_percent: float


class Stock(BaseModel):
    """Stock data model for display."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    name: str
    code: str
    current_price: float
    change: float
    change_percent: float
    market_value: float  # market_value from DB
    volume: float  # market_value from DB (in 万)
    holdings: int  # holdings from DB
    available_shares: int # available shares from DB
    float_change: float  # pnl_float from DB
    float_change_percent: float  # pnl_float_percent from DB
    cumulative_change: float  # pnl_cumulative from DB
    cumulative_change_percent: float  # pnl_cumulative_percent from DB
    cost_basis_total: float  # cost_basis_total from DB
    analysis_report_url: Optional[str] = None  # URL to stock analysis report
    operation_cmd_url: Optional[str] = None  # URL to operation commands


class PortfolioState(rx.State):
    """State for the portfolio page."""
    
    # Sidebar state
    is_sidebar_expanded: bool = False
    is_sidebar_visible_on_mobile: bool = False
    
    # Theme state (dark mode by default)
    is_dark_mode: bool = True
    
    # Active menu item
    active_menu: str = "首页"
    
    # Current user ID (hardcoded to demo user for now)
    user_id: int = 1
    
    # Market stats
    total_market_value: float = 0.0
    today_change: float = 0.0
    today_change_percent: float = 0.0
    float_change: float = 0.0
    float_change_percent: float = 0.0
    cumulative_change: float = 0.0
    cumulative_change_percent: float = 0.0
    total_assets: float = 0.0
    principal: float = 300000.0 # 本金, manual adjust when withdraw or deposit.
    position_percent: float = 0.0  # 仓位
    withdrawable: float = 0.0  # 可用金额
    cash: float = 0.0 # 可提现金
    
    # Stock data
    stocks: List[Stock] = []
    
    # Market indices data
    market_indices: List[MarketIndex] = []
    
    # Loading state
    is_loading: bool = False
    
    # Sorting state
    sort_by: str = "market_value"  # Default sort by market value
    sort_order: str = "desc"  # "asc" or "desc"
    
    @rx.event
    def on_load(self):
        """Load portfolio data when page loads."""
        self.load_portfolio_data()
    
    @rx.event
    def load_portfolio_data(self):
        """Load portfolio data from database."""
        self.is_loading = True
        
        # Load market stats from summary_account table 
        with rx.session() as session:
            # Query summary_account for the user
            from sqlalchemy import text
            total_query = text("""
                SELECT total_market_value, today_pnl, today_pnl_percent,
                       cumulative_pnl, cumulative_pnl_percent, cash,
                       floating_pnl_summary, floating_pnl_summary_percent,
                       total_assets, principal, position_percent, withdrawable
                FROM summary_account
                WHERE user_id = :user_id
                LIMIT 1
            """)
            total_result = session.execute(total_query, {"user_id": self.user_id}).fetchone()
            
            if total_result:
                self.total_market_value = total_result[0] or 0.0
                self.today_change = total_result[1] or 0.0
                self.today_change_percent = total_result[2] or 0.0
                self.cumulative_change = total_result[3] or 0.0
                self.cumulative_change_percent = total_result[4] or 0.0
                self.cash = total_result[5] or 0.0
                self.float_change = total_result[6] or 0.0
                self.float_change_percent = total_result[7] or 0.0
                self.total_assets = total_result[8] or 0.0
                self.principal = total_result[9] or 0.0
                self.position_percent = total_result[10] or 0.0
                self.withdrawable = total_result[11] or 0.0
            
            # Query market_indices for top 3 indices
            indices_query = text("""
                SELECT index_code, index_name, current_value, change_percent
                FROM market_indices
                ORDER BY id
                LIMIT 3
            """)
            indices_results = session.execute(indices_query).fetchall()
            
            # Convert database results to MarketIndex objects
            self.market_indices = []
            for row in indices_results:
                index = MarketIndex(
                    index_code=row[0],
                    index_name=row[1],
                    current_value=row[2] or 0.0,
                    change_percent=row[3] or 0.0,
                )
                self.market_indices.append(index)
            
            # Query holding_stocks for the user's stocks
            stocks_query = text("""
                SELECT code, name, current_price, change, change_percent,
                       market_value, holdings, available_shares, pnl_float, pnl_float_percent,
                       pnl_cumulative, pnl_cumulative_percent, cost_basis_total,
                       analysis_report_url, operation_cmd_url
                FROM holding_stocks
                WHERE user_id = :user_id
                ORDER BY market_value DESC
            """)
            stocks_results = session.execute(stocks_query, {"user_id": self.user_id}).fetchall()
            
            # Convert database results to Stock objects
            self.stocks = []
            for row in stocks_results:
                stock = Stock(
                    code=row[0],
                    name=row[1],
                    current_price=row[2] or 0.0,
                    change=row[3] or 0.0,
                    change_percent=row[4] or 0.0,
                    market_value=row[5] or 0.0,  # market_value
                    volume=(row[5] or 0.0) / 10000,  # Convert to 万
                    holdings=row[6] or 0,  # holdings
                    available_shares=row[7] or 0,  # available_shares
                    float_change=row[8] or 0.0,
                    float_change_percent=row[9] or 0.0,
                    cumulative_change=row[10] or 0.0,
                    cumulative_change_percent=row[11] or 0.0,
                    cost_basis_total=row[12] or 0.0,  # cost_basis_total
                    analysis_report_url=row[13],  # analysis_report_url
                    operation_cmd_url=row[14],  # operation_cmd_url
                )
                self.stocks.append(stock)
        
        self.is_loading = False
    
    @rx.event
    def toggle_sidebar(self):
        """Toggle sidebar expanded/collapsed state."""
        self.is_sidebar_expanded = not self.is_sidebar_expanded
    
    @rx.event
    def toggle_mobile_sidebar(self):
        """Toggle sidebar visibility on mobile."""
        self.is_sidebar_visible_on_mobile = not self.is_sidebar_visible_on_mobile
    
    @rx.event
    def close_mobile_sidebar(self):
        """Close sidebar on mobile."""
        self.is_sidebar_visible_on_mobile = False
    
    @rx.event
    def toggle_theme(self):
        """Toggle dark/light mode."""
        self.is_dark_mode = not self.is_dark_mode
    
    @rx.event
    def set_active_menu(self, menu_item: str):
        """Set the active menu item."""
        self.active_menu = menu_item
    
    @rx.event
    def remove_stock(self, stock_code: str):
        """Remove a stock from the portfolio."""
        self.stocks = [s for s in self.stocks if s.code != stock_code]
    
    @rx.event
    def refresh_data(self):
        """Manually refresh portfolio data."""
        self.load_portfolio_data()
    
    @rx.event
    def sort_stocks(self, field: str):
        """Sort stocks by the given field."""
        # Toggle sort order if clicking the same field
        if self.sort_by == field:
            self.sort_order = "asc" if self.sort_order == "desc" else "desc"
        else:
            self.sort_by = field
            self.sort_order = "desc"  # Default to descending for new field
        
        # Sort the stocks list
        reverse = (self.sort_order == "desc")
        
        # Map field names to Stock attributes
        field_map = {
            "market_value": lambda s: s.market_value,
            "change_percent": lambda s: s.change_percent,
            "holdings": lambda s: s.holdings,
            "current_price": lambda s: s.current_price,
            "float_change_percent": lambda s: s.float_change_percent,
        }
        
        if field in field_map:
            self.stocks = sorted(self.stocks, key=field_map[field], reverse=reverse)
    
    @rx.event
    def open_analysis_report(self, stock_code: str):
        """Open analysis report in new tab by creating symlink to assets."""
        import pathlib
        stock_code = add_suffix_to_stock_code(stock_code)

        # Source file in /tmp
        source = pathlib.Path(f"/tmp/{stock_code}/report.html")
        
        # Target in assets folder root (Reflex serves assets/ from /)
        assets_dir = pathlib.Path("assets")
        target = assets_dir / f"stock_analysis/{stock_code}_report.html"
        
        # Create or update symlink
        if target.exists() or target.is_symlink():
            target.unlink()
        
        if source.exists():
            try:
                # Create symlink with absolute path
                target.symlink_to(source.absolute())
                # Open the file via the web server (assets/file.html -> /file.html)
                url = f"/stock_analysis/{stock_code}_report.html"
                return rx.call_script(f"window.open('{url}', '_blank')")
            except Exception as e:
                print(f"Error creating symlink: {e}")
                return rx.window_alert(f"无法打开文件: {e}")
        else:
            return rx.window_alert(f"分析报告不存在: {source}")
    
    @rx.event
    def open_operation_cmd(self, stock_code: str):
        """Open operation command in new tab by creating symlink to assets."""
        import pathlib
        
        # Source file in /tmp
        source = pathlib.Path(f"/tmp/{stock_code}/cmd.md")
        
        # Target in assets folder root (Reflex serves assets/ from /)
        assets_dir = pathlib.Path("assets")
        target = assets_dir / f"{stock_code}_cmd.md"
        
        # Create or update symlink
        if target.exists() or target.is_symlink():
            target.unlink()
        
        if source.exists():
            try:
                # Create symlink with absolute path
                target.symlink_to(source.absolute())
                # Open the file via the web server (assets/file.md -> /file.md)
                url = f"/{stock_code}_cmd.md"
                return rx.call_script(f"window.open('{url}', '_blank')")
            except Exception as e:
                print(f"Error creating symlink: {e}")
                return rx.window_alert(f"无法打开文件: {e}")
        else:
            return rx.window_alert(f"操作指令不存在: {source}")
    