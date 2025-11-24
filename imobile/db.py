"""Database models and configuration for iMobile application."""
import reflex as rx
from datetime import datetime, date, time
from typing import Optional, List
import sqlalchemy as sa
from sqlmodel import Field, Relationship

class User(rx.Model, table=True):
    """User model."""
    __tablename__ = "users"
    email: str = Field(unique=True, index=True)
    password_hash: str
    is_verified: bool = Field(default=False)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    profile_picture_url: Optional[str] = None
    language: Optional[str] = None

class MarketIndex(rx.Model, table=True):
    """Market index model."""
    __tablename__ = "market_indices"
    index_code: str = Field(unique=True, index=True)
    index_name: str
    current_value: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    last_updated: Optional[datetime] = None

class AppConfig(rx.Model, table=True):
    """Application configuration model."""
    __tablename__ = "app_config"
    user_id: int = Field(foreign_key="users.id", unique=True, index=True)
    theme: str = Field(default="light")
    notifications_enabled: bool = Field(default=True)
    market: str = Field(default="A-shares")
    default_currency: str = Field(default="CNY")
    language: str = Field(default="en")
    data_refresh_interval: int = Field(default=15)
    last_synced: Optional[datetime] = None
    open_time_morning_start: time = Field(default=time(9, 30))
    open_time_morning_end: time = Field(default=time(11, 30))
    open_time_afternoon_start: time = Field(default=time(13, 0))
    open_time_afternoon_end: time = Field(default=time(15, 0))

class StockEvent(rx.Model, table=True):
    """Stock event model."""
    __tablename__ = "stock_events"
    user_id: int = Field(foreign_key="users.id")
    stock_code: str
    event_type: str
    event_date: date
    description: Optional[str] = None
    amount: Optional[float] = None
    ratio: Optional[str] = None

class SmartOrder(rx.Model, table=True):
    """Smart order model."""
    __tablename__ = "smart_orders"
    user_id: int = Field(foreign_key="users.id", index=True)
    code: str = Field(index=True)
    name: str = Field(index=True)
    trigger_condition: str
    buy_or_sell_price_type: str
    buy_or_sell_quantity: int
    valid_until: Optional[str] = None
    order_number: Optional[str] = Field(unique=True, index=True)
    reason_of_ending: Optional[str] = None
    status: Optional[str] = Field(default="cancelled", index=True)
    last_updated: Optional[datetime] = Field(sa_column=sa.Column(sa.DateTime, server_default=sa.text("CURRENT_TIMESTAMP")))

class Strategy(rx.Model, table=True):
    """Strategy model."""
    __tablename__ = "strategies"
    user_id: int = Field(foreign_key="users.id")
    strategy_name: str
    description: Optional[str] = None
    created_at: Optional[datetime] = Field(sa_column=sa.Column(sa.DateTime, server_default=sa.text("CURRENT_TIMESTAMP")))
    updated_at: Optional[datetime] = Field(sa_column=sa.Column(sa.DateTime, server_default=sa.text("CURRENT_TIMESTAMP")))
    
    __table_args__ = (sa.UniqueConstraint("user_id", "strategy_name"),)

class SummaryAccount(rx.Model, table=True):
    """Summary account model."""
    __tablename__ = "summary_account"
    user_id: int = Field(foreign_key="users.id", unique=True, index=True)
    total_market_value: Optional[float] = None
    today_pnl: Optional[float] = None
    today_pnl_percent: Optional[float] = None
    cumulative_pnl: Optional[float] = None
    cumulative_pnl_percent: Optional[float] = None
    cash: Optional[float] = None
    floating_pnl_summary: Optional[float] = None
    floating_pnl_summary_percent: Optional[float] = None
    total_assets: Optional[float] = None
    principal: Optional[float] = None
    position_percent: Optional[float] = None
    withdrawable: Optional[float] = None
    last_updated: Optional[datetime] = Field(index=True)

class HoldingStock(rx.Model, table=True):
    """Holding stock model."""
    __tablename__ = "holding_stocks"
    user_id: int = Field(foreign_key="users.id")
    code: str = Field(index=True)
    name: str = Field(index=True)
    current_price: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    market_value: Optional[float] = None
    holdings: Optional[int] = None
    cost_basis_diluted: Optional[float] = None
    cost_basis_total: Optional[float] = None
    pnl_float: Optional[float] = None
    pnl_float_percent: Optional[float] = None
    pnl_cumulative: Optional[float] = None
    pnl_cumulative_percent: Optional[float] = None
    available_shares: Optional[int] = None
    last_updated: Optional[datetime] = Field(index=True)
    analysis_report_url: Optional[str] = None
    operation_cmd_url: Optional[str] = None
    
    __table_args__ = (
        sa.UniqueConstraint("user_id", "code"),
        sa.UniqueConstraint("user_id", "name"),
    )

class Watchlist(rx.Model, table=True):
    """Watchlist model."""
    __tablename__ = "watchlist"
    user_id: int = Field(foreign_key="users.id", index=True)
    stock_code: str
    stock_name: str
    added_date: datetime
    notes: Optional[str] = None
    target_price: Optional[float] = None
    
    __table_args__ = (sa.UniqueConstraint("user_id", "stock_code"),)

class SuggestionStock(rx.Model, table=True):
    """Suggestion stock model."""
    __tablename__ = "suggestion_stocks"
    user_id: int = Field(foreign_key="users.id", index=True)
    code: str = Field(index=True)
    name: str = Field(index=True)
    picked_score: Optional[float] = Field(index=True)
    reason: Optional[str] = None
    buy_price: Optional[float] = None
    sell_stop_loss_price: Optional[float] = None
    sell_take_profit_price: Optional[float] = None
    quantity: Optional[int] = None
    buy_smart_order: Optional[str] = None
    sell_smart_order: Optional[str] = None
    buy_smart_order_status: Optional[str] = Field(default="pending")
    sell_smart_order_status: Optional[str] = Field(default="pending")
    buy_smart_order_created_at: Optional[datetime] = None
    sell_smart_order_created_at: Optional[datetime] = None
    created_at: Optional[datetime] = Field(sa_column=sa.Column(sa.DateTime, server_default=sa.text("CURRENT_TIMESTAMP"), index=True))

class PortfolioHistory(rx.Model, table=True):
    """Portfolio history model."""
    __tablename__ = "portfolio_history"
    user_id: int = Field(foreign_key="users.id")
    record_date: date
    total_assets: Optional[float] = None
    total_market_value: Optional[float] = None
    cash: Optional[float] = None
    daily_pnl: Optional[float] = None
    daily_pnl_percent: Optional[float] = None
    cumulative_pnl: Optional[float] = None
    cumulative_pnl_percent: Optional[float] = None
    
    __table_args__ = (sa.UniqueConstraint("user_id"),)

class Transaction(rx.Model, table=True):
    """Transaction model."""
    __tablename__ = "transactions"
    user_id: int = Field(foreign_key="users.id", index=True)
    code: str = Field(index=True)
    name: str
    transaction_type: str
    transaction_date: datetime = Field(index=True)
    price: float
    quantity: int
    amount: float
    commission: Optional[float] = None
    tax: Optional[float] = None
    net_amount: Optional[float] = None
    notes: Optional[str] = None
