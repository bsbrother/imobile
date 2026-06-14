"""
Base classes for mobile app data extraction with Mobilerun/DroidRun.
"""
from pydantic import BaseModel
from typing import Optional, List


class ExtractTransaction(BaseModel):
    """Transaction data extracted from broker app."""
    name: str
    transaction_date: str
    price: float
    quantity: int
    transaction_type: str  # 证券买入 or 证券卖出
    amount: float


class ExtractOrder(BaseModel):
    """Smart order extracted from broker app."""
    name: str
    code: str
    trigger_condition: str
    commission_method: str
    buy_or_sell_quantity: int
    valid_until: Optional[str] = None
    order_number: Optional[str] = None
    reason_of_ending: Optional[str] = None


class ExtractQuote(BaseModel):
    """Index and stock quote extracted from broker app."""
    # Indices
    indices: List[dict] = []   # [{index_name, index_number, index_ratio}]
    # Stocks
    stocks: List[dict] = []    # [{name, code, latest_price, increase_percentage, increase_amount}]


class ExtractPosition(BaseModel):
    """Position summary + holdings extracted from broker app."""
    # Summary
    floating_profit_loss: float = 0.0
    account_assets: float = 0.0
    market_cap: float = 0.0
    positions_pct: float = 0.0
    available: float = 0.0
    desirable: float = 0.0
    # Holdings
    holdings: List[dict] = []  # [{name, market_cap, holdings, available, current_price, cost, floating_profit, floating_loss_pct}]


class AppDataExtractor:
    """Base class for broker app data extractors."""

    def __init__(self, config, llm, driver, app_package_name=None, password=None):
        self.config = config
        self.llm = llm
        self.driver = driver
        self.app_package_name = app_package_name
        self.password = password

    def goto_homepage(self) -> None:
        raise NotImplementedError

    async def login(self) -> None:
        raise NotImplementedError

    async def get_transactions(self) -> ExtractTransaction:
        raise NotImplementedError

    async def get_positions(self) -> ExtractPosition:
        raise NotImplementedError
