"""State management for portfolio page."""
import reflex as rx
from typing import List, Optional
from sqlalchemy import select


class Stock(rx.Base):
    """Stock data model for display."""
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
    
    # Loading state
    is_loading: bool = False
    
    @rx.event
    def on_load(self):
        """Load portfolio data when page loads."""
        self.load_portfolio_data()
    
    @rx.event
    def load_portfolio_data(self):
        """Load portfolio data from database."""
        self.is_loading = True
        
        # Load market stats from total_table
        with rx.session() as session:
            # Query total_table for the user
            from sqlalchemy import text
            total_query = text("""
                SELECT total_market_value, today_pnl, today_pnl_percent,
                       cumulative_pnl, cumulative_pnl_percent, cash,
                       floating_pnl_summary, floating_pnl_summary_percent,
                       total_assets, principal, position_percent, withdrawable
                FROM total_table
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
            
            # Query stocks_table for the user's stocks
            stocks_query = text("""
                SELECT code, name, current_price, change, change_percent,
                       market_value, holdings, available_shares, pnl_float, pnl_float_percent,
                       pnl_cumulative, pnl_cumulative_percent, cost_basis_total
                FROM stocks_table
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
