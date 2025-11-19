"""CBS EWO strategy implementation.

This module implements:
- EWO indicator (Elder Wave Oscillator)
- Simple trend turning / divergence-based signal helpers
- A minimal vectorized backtest engine

It is intentionally dependency‑light and uses only pandas/numpy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import chain
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


def _coerce_float(value: Any) -> float:
    """Safely convert numpy/pandas scalars to float."""

    if isinstance(value, (int, float, np.number)):
        return float(value)
    if hasattr(value, "item"):
        try:
            return float(value.item())
        except Exception:  # pragma: no cover - defensive fallback
            pass
    return float(value)


def _coerce_int(value: Any) -> int:
    """Safely convert numpy/pandas scalars to int."""

    if isinstance(value, (int, np.integer)):
        return int(value)
    if hasattr(value, "item"):
        try:
            return int(value.item())
        except Exception:  # pragma: no cover - defensive fallback
            pass
    return int(value)


def compute_ewo(
    df: pd.DataFrame,
    fast_span: int = 5,
    slow_span: int = 35,
    price_col: str = "close",
    adjust: bool = False,
) -> pd.Series:
    """Compute EWO (Elder Wave Oscillator).

    Parameters
    ----------
    df:
        Price dataframe containing at least `price_col`.
    fast_span:
        Fast EMA span.
    slow_span:
        Slow EMA span (> fast_span recommended).
    price_col:
        Column name for price.
    adjust:
        Passed to ``pd.Series.ewm``; default False for typical trading usage.

    Returns
    -------
    pd.Series
        The EWO series aligned with ``df.index``.
    """

    if price_col not in df:
        raise KeyError(f"Column '{price_col}' not found in dataframe")

    price = df[price_col].astype(float)
    ema_fast = price.ewm(span=fast_span, adjust=adjust).mean()
    ema_slow = price.ewm(span=slow_span, adjust=adjust).mean()
    ewo = ema_fast - ema_slow
    return ewo.rename("ewo")


def zero_cross_signals(ewo: pd.Series) -> pd.Series:
    """Generate basic zero‑cross buy(+1)/sell(‑1)/flat(0) signals from EWO.

    +1 when EWO crosses from below 0 to >= 0 (bullish),
    -1 when EWO crosses from above 0 to <= 0 (bearish),
    otherwise 0.
    """

    ewo = ewo.astype(float).fillna(0.0)
    prev = ewo.shift(1).fillna(0.0)
    buy = (prev < 0) & (ewo >= 0)
    sell = (prev > 0) & (ewo <= 0)
    sig = pd.Series(0, index=ewo.index, dtype=int)
    sig[buy] = 1
    sig[sell] = -1
    return sig.rename("ewo_zero_cross_signal")


def find_divergence_points(
    df: pd.DataFrame,
    osc: pd.Series,
    price_col: str = "close",
    lookback: int = 20,
) -> pd.Series:
    """Very simple divergence detector.

    Bullish divergence (+1): price makes a new N‑bar low but oscillator does not.
    Bearish divergence (‑1): price makes a new N‑bar high but oscillator does not.

    This is deliberately minimalistic – it is only a helper for
    high‑level "trend turning" signals as described in the doc.
    """

    if price_col not in df:
        raise KeyError(f"Column '{price_col}' not found in dataframe")

    price = df[price_col].astype(float)
    osc = osc.astype(float)

    rolling_low = price.rolling(lookback, min_periods=lookback).min()
    rolling_high = price.rolling(lookback, min_periods=lookback).max()

    rolling_osc_low = osc.rolling(lookback, min_periods=lookback).min()
    rolling_osc_high = osc.rolling(lookback, min_periods=lookback).max()

    bullish = (price <= rolling_low) & (osc > rolling_osc_low)
    bearish = (price >= rolling_high) & (osc < rolling_osc_high)

    sig = pd.Series(0, index=df.index, dtype=int)
    sig[bullish] = 1
    sig[bearish] = -1
    return sig.rename("divergence_signal")


def trend_turning_signal(
    df: pd.DataFrame,
    osc: pd.Series,
    price_col: str = "close",
    ma_fast: int = 50,
    ma_slow: int = 200,
    lookback: int = 20,
) -> pd.Series:
    """Composite trend‑turning signal based on MA regime and divergence.

    +1: bullish divergence in a non‑strong‑downtrend regime.
    -1: bearish divergence in a non‑strong‑uptrend regime.
    0: otherwise.
    """

    price = df[price_col].astype(float)
    ma_f = price.rolling(ma_fast, min_periods=ma_fast).mean()
    ma_s = price.rolling(ma_slow, min_periods=ma_slow).mean()

    regime_up = ma_f > ma_s
    regime_down = ma_f < ma_s

    div = find_divergence_points(df, osc, price_col=price_col, lookback=lookback)

    sig = pd.Series(0, index=df.index, dtype=int)
    sig[(div == 1) & ~regime_down] = 1
    sig[(div == -1) & ~regime_up] = -1
    return sig.rename("trend_turning_signal")


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    benchmark_curve: Optional[pd.Series]
    trades: pd.DataFrame
    metrics: dict


def backtest_cbs_ewo(
    df: pd.DataFrame,
    signal: pd.Series,
    benchmark: Optional[pd.Series] = None,
    initial_capital: float = 600_000.0,
    price_col: str = "close",
    side: Literal["long_only", "long_short"] = "long_only",
) -> BacktestResult:
    """Run a minimal vectorized backtest for the CBS EWO strategy.

    Assumptions
    ----------
    - 100% of capital invested when in position, no leverage.
    - Trades at ``price_col`` on signal day close (for simplicity).
    - ``signal`` is interpreted as:
        * long_only: 1 = long, 0 = flat (negative values treated as flat)
        * long_short: 1 = long, -1 = short, 0 = flat
    """

    if price_col not in df:
        raise KeyError(f"Column '{price_col}' not found in dataframe")

    price = df[price_col].astype(float).reindex(signal.index).ffill()
    sig = signal.reindex(price.index).fillna(0).astype(int)

    if side == "long_only":
        sig = sig.clip(lower=0, upper=1)
    elif side == "long_short":
        sig = sig.clip(lower=-1, upper=1)
    else:
        raise ValueError("side must be 'long_only' or 'long_short'")

    ret = price.pct_change().fillna(0.0)
    strat_ret = sig.shift(1).fillna(0) * ret
    equity_curve = (1 + strat_ret).cumprod() * initial_capital
    equity_curve.name = "strategy_equity"

    trades = _extract_trades(price, sig, initial_capital)

    benchmark_curve = None
    if benchmark is not None:
        bench = benchmark.reindex(price.index).ffill().astype(float)
        bench_ret = bench.pct_change().fillna(0.0)
        benchmark_curve = (1 + bench_ret).cumprod() * initial_capital
        benchmark_curve.name = "benchmark_equity"

    metrics = _compute_metrics(equity_curve, benchmark_curve)

    return BacktestResult(
        equity_curve=equity_curve,
        benchmark_curve=benchmark_curve,
        trades=trades,
        metrics=metrics,
    )


def _extract_trades(
    price: pd.Series,
    sig: pd.Series,
    initial_capital: float,
) -> pd.DataFrame:
    """Convert position series into a simple trade list.

    This is not meant to be a full‑featured trade ledger, just a helpful
    summary for inspection and testing.
    """

    pos_prev = sig.shift(1).fillna(0).astype(int)
    pos_now = sig.astype(int)
    change = pos_now - pos_prev

    # Entry when position changes from 0 to != 0, exit when != 0 to 0 or sign flip.
    trades = []
    current_side = 0
    entry_price: Optional[float] = None
    entry_time: Optional[pd.Timestamp] = None

    price_values = price.astype(float).to_numpy()
    pos_now_values = pos_now.to_numpy()

    for idx, ts in enumerate(change.index):
        new_side = int(pos_now_values[idx])
        bar_price = float(price_values[idx])

        if current_side == 0 and new_side != 0:
            # open
            current_side = new_side
            entry_price = bar_price
            entry_time = ts
        elif current_side != 0 and new_side == 0:
            # close
            if entry_price is None or entry_time is None:
                continue
            exit_price = bar_price
            pnl_pct = (exit_price / entry_price - 1.0) * current_side
            trades.append(
                {
                    "entry_time": entry_time,
                    "exit_time": ts,
                    "side": current_side,
                    "entry_price": float(entry_price),
                    "exit_price": float(exit_price),
                    "pnl_pct": float(pnl_pct),
                    "pnl_cash": float(initial_capital * pnl_pct),
                }
            )
            current_side = 0
            entry_price = None
            entry_time = None
        elif current_side != 0 and new_side != 0 and np.sign(new_side) != np.sign(current_side):
            # flip position: close then reopen opposite at same bar
            if entry_price is None or entry_time is None:
                continue
            exit_price = bar_price
            pnl_pct = (exit_price / entry_price - 1.0) * current_side
            trades.append(
                {
                    "entry_time": entry_time,
                    "exit_time": ts,
                    "side": current_side,
                    "entry_price": float(entry_price),
                    "exit_price": float(exit_price),
                    "pnl_pct": float(pnl_pct),
                    "pnl_cash": float(initial_capital * pnl_pct),
                }
            )
            # reopen opposite
            current_side = new_side
            entry_price = bar_price
            entry_time = ts

    return pd.DataFrame(trades)


def _compute_metrics(
    equity_curve: pd.Series,
    benchmark_curve: Optional[pd.Series],
) -> dict:
    """Compute core performance metrics used for quick evaluation.

    Metrics include:
    - total_return
    - annualized_return (assuming 252 trading days per year)
    - max_drawdown
    - sharpe (daily, risk‑free = 0)
    - vs_benchmark_excess_return (if benchmark provided)
    """

    eq = equity_curve.astype(float)
    ret = eq.pct_change().fillna(0.0)

    total_return = eq.iloc[-1] / eq.iloc[0] - 1.0 if len(eq) > 1 else 0.0
    # annualized = (1+R)^ (252/N) -1
    n = max(len(eq) - 1, 1)
    annualized_return = (1.0 + total_return) ** (252.0 / n) - 1.0

    cummax = eq.cummax()
    dd = eq / cummax - 1.0
    max_drawdown = dd.min() if len(dd) else 0.0

    sharpe = 0.0
    if ret.std(ddof=1) > 0:
        sharpe = ret.mean() / ret.std(ddof=1) * np.sqrt(252.0)

    excess = None
    if benchmark_curve is not None:
        bench = benchmark_curve.astype(float).reindex(eq.index).ffill()
        excess = eq.iloc[-1] / eq.iloc[0] - (bench.iloc[-1] / bench.iloc[0] - 1.0)

    return {
        "total_return": float(total_return),
        "annualized_return": float(annualized_return),
        "max_drawdown": float(max_drawdown),
        "sharpe": float(sharpe),
        "vs_benchmark_excess_return": float(excess) if excess is not None else None,
    }


# --- Portfolio simulation utilities ---------------------------------------------------------


SIGNAL_BUY_COLUMN = "cbs_ewo_buy_signal"
SIGNAL_SELL_COLUMN = "cbs_ewo_sell_signal"


@dataclass
class PortfolioPosition:
    symbol: str
    qty: float
    entry_price: float
    entry_value: float
    entry_date: pd.Timestamp
    entry_index: int
    last_price: float


@dataclass
class TradeAction:
    date: pd.Timestamp
    symbol: str
    action: Literal["BUY", "SELL"]
    price: float
    qty: float
    value: float
    reason: str
    signal_snapshot: Dict[str, float]
    pnl: float = 0.0
    cash_after: float = 0.0


@dataclass
class DailyLog:
    date: pd.Timestamp
    day_index: int
    buys: List[TradeAction] = field(default_factory=list)
    sells: List[TradeAction] = field(default_factory=list)
    ranked_candidates: List[Tuple[str, float]] = field(default_factory=list)
    holdings: Dict[str, float] = field(default_factory=dict)
    cash: float = 0.0
    equity: float = 0.0


@dataclass
class PortfolioSimulationResult:
    equity_curve: pd.Series
    daily_logs: List[DailyLog]
    trades: pd.DataFrame


def build_signal_panel(
    df: pd.DataFrame,
    price_col: str = "close",
    buy_mode: Literal["strict", "relaxed"] = "strict",
) -> pd.DataFrame:
    """Create a MultiIndex panel with CBS+EWO signal columns per symbol."""

    if df.empty:
        return pd.DataFrame()

    if "ts_code" not in df.columns or "trade_date" not in df.columns:
        raise KeyError("Dataframe must contain 'ts_code' and 'trade_date' columns")

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    normalized_buy_mode = buy_mode.lower()
    if normalized_buy_mode not in {"strict", "relaxed"}:
        raise ValueError("buy_mode must be either 'strict' or 'relaxed'")
    frames: List[pd.DataFrame] = []

    for symbol, sym_df in df.groupby("ts_code"):
        sym_df = sym_df.sort_values("trade_date").copy()
        ewo = compute_ewo(sym_df, price_col=price_col)
        zero_cross = zero_cross_signals(ewo)
        trend_sig = trend_turning_signal(sym_df, ewo, price_col=price_col)
        sym_df["ewo"] = ewo
        sym_df["ewo_zero_cross_signal"] = zero_cross
        sym_df["trend_turning_signal"] = trend_sig
        strict_buy = (trend_sig == 1) & (zero_cross == 1)
        relaxed_buy = (trend_sig == 1) | (zero_cross == 1)
        buy_mask = strict_buy if normalized_buy_mode == "strict" else relaxed_buy
        sym_df[SIGNAL_BUY_COLUMN] = buy_mask.astype(int)
        sym_df[SIGNAL_SELL_COLUMN] = ((trend_sig == -1) | (zero_cross == -1)).astype(int)
        for col in (price_col, "pct_chg", "vol", "amount"):
            if col not in sym_df.columns:
                sym_df[col] = 0.0
        frames.append(sym_df)

    panel = pd.concat(frames, ignore_index=True)
    panel.set_index(["trade_date", "ts_code"], inplace=True)
    panel.sort_index(inplace=True)
    return panel


class CBS_EWOPortfolioSimulator:
    """Long-only portfolio simulator that respects T+1 exit rules."""

    def __init__(
        self,
        initial_capital: float = 600_000.0,
        max_positions: int = 10,
        min_hold_days: int = 1,
        ranking_field: str = "ewo",
        buy_field: str = SIGNAL_BUY_COLUMN,
        sell_field: str = SIGNAL_SELL_COLUMN,
        side: Literal["long_only"] = "long_only",
    ) -> None:
        if max_positions <= 0:
            raise ValueError("max_positions must be positive")
        if side != "long_only":
            raise ValueError("Only 'long_only' side is supported in this simulator")
        self.initial_capital = float(initial_capital)
        self.cash = float(initial_capital)
        self.max_positions = int(max_positions)
        self.min_hold_days = max(0, int(min_hold_days))
        self.ranking_field = ranking_field
        self.buy_field = buy_field
        self.sell_field = sell_field
        self.side = side
        self.positions: Dict[str, PortfolioPosition] = {}

    def step(self, date: pd.Timestamp, day_index: int, day_df: pd.DataFrame) -> DailyLog:
        date = pd.to_datetime(date)
        day_df = self._ensure_day_frame(day_df)

        if not day_df.empty:
            close_updates = day_df["close"].dropna()
            for symbol, price in close_updates.items():
                symbol_key = str(symbol)
                if symbol_key in self.positions:
                    self.positions[symbol_key].last_price = _coerce_float(price)

        sells = self._handle_sells(day_df, day_index, date)
        buys, ranked_candidates = self._handle_buys(day_df, day_index, date)

        equity = self._compute_equity(day_df)
        holdings_snapshot = {sym: pos.qty for sym, pos in self.positions.items()}

        return DailyLog(
            date=date,
            day_index=day_index,
            buys=buys,
            sells=sells,
            ranked_candidates=ranked_candidates,
            holdings=holdings_snapshot,
            cash=self.cash,
            equity=equity,
        )

    def _handle_sells(
        self,
        day_df: pd.DataFrame,
        day_index: int,
        date: pd.Timestamp,
    ) -> List[TradeAction]:
        sells: List[TradeAction] = []
        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]
            if self.min_hold_days and day_index - position.entry_index < self.min_hold_days:
                continue
            price = self._get_price(symbol, day_df)
            if price is None or price <= 0:
                continue
            signal_value = self._get_signal(day_df, symbol, self.sell_field)
            if signal_value != 1:
                continue
            exit_value = position.qty * price
            pnl = position.qty * (price - position.entry_price)
            self.cash += exit_value
            action = TradeAction(
                date=date,
                symbol=symbol,
                action="SELL",
                price=float(price),
                qty=position.qty,
                value=exit_value,
                reason="CBS_EWO_exit",
                signal_snapshot=self._signal_snapshot(day_df, symbol),
                pnl=pnl,
                cash_after=self.cash,
            )
            sells.append(action)
            del self.positions[symbol]
        return sells

    def _handle_buys(
        self,
        day_df: pd.DataFrame,
        day_index: int,
        date: pd.Timestamp,
    ) -> Tuple[List[TradeAction], List[Tuple[str, float]]]:
        buys: List[TradeAction] = []
        ranked_candidates: List[Tuple[str, float]] = []
        if day_df.empty or self.buy_field not in day_df.columns:
            return buys, ranked_candidates

        available_slots = max(self.max_positions - len(self.positions), 0)
        if available_slots <= 0:
            return buys, ranked_candidates

        candidates = day_df[day_df[self.buy_field] == 1].copy()
        if candidates.empty:
            return buys, ranked_candidates

        candidates = candidates[~candidates.index.isin(self.positions)]
        if candidates.empty:
            return buys, ranked_candidates

        rank_field = self.ranking_field if self.ranking_field in candidates.columns else "ewo"
        candidates["_rank_score"] = candidates[rank_field].fillna(0.0)
        candidates.sort_values("_rank_score", ascending=False, inplace=True)
        ranked_candidates = [
            (str(symbol), _coerce_float(score))
            for symbol, score in zip(candidates.index.tolist(), candidates["_rank_score"].tolist())
        ][: max(available_slots * 2, available_slots)]

        slice_for_buys = candidates.head(available_slots)
        for raw_symbol, row in slice_for_buys.iterrows():
            symbol = str(raw_symbol)
            price = _coerce_float(row.get("close", 0.0))
            if price <= 0:
                continue
            allocation = self._allocation_per_trade()
            invest = min(allocation, self.cash)
            if invest <= 0:
                break
            qty = invest / price
            self.cash -= invest
            self.positions[symbol] = PortfolioPosition(
                symbol=symbol,
                qty=qty,
                entry_price=price,
                entry_value=invest,
                entry_date=date,
                entry_index=day_index,
                last_price=price,
            )
            action = TradeAction(
                date=date,
                symbol=symbol,
                action="BUY",
                price=price,
                qty=qty,
                value=invest,
                reason="CBS_EWO_entry",
                signal_snapshot=self._signal_snapshot(day_df, symbol),
                pnl=0.0,
                cash_after=self.cash,
            )
            buys.append(action)

        return buys, ranked_candidates

    def _allocation_per_trade(self) -> float:
        if not self.max_positions:
            return 0.0
        equity = self._compute_equity(None)
        return equity / self.max_positions

    def _compute_equity(self, day_df: Optional[pd.DataFrame]) -> float:
        equity = self.cash
        for symbol, position in self.positions.items():
            price = self._get_price(symbol, day_df)
            if price is None:
                price = position.last_price
            if price is None:
                continue
            equity += position.qty * price
        return float(equity)

    def _get_price(self, symbol: str, day_df: Optional[pd.DataFrame]) -> Optional[float]:
        if day_df is not None and not day_df.empty and symbol in day_df.index:
            price = day_df.at[symbol, "close"]
            if pd.notna(price):
                return _coerce_float(price)
        position = self.positions.get(symbol)
        if position:
            return position.last_price
        return None

    @staticmethod
    def _ensure_day_frame(day_df: Optional[pd.DataFrame]) -> pd.DataFrame:
        if day_df is None:
            return pd.DataFrame()
        if isinstance(day_df, pd.Series):
            return day_df.to_frame().T
        return day_df

    @staticmethod
    def _get_signal(day_df: pd.DataFrame, symbol: str, column: str) -> int:
        if day_df is None or day_df.empty or column not in day_df.columns or symbol not in day_df.index:
            return 0
        value = day_df.at[symbol, column]
        if pd.isna(value):
            return 0
        return _coerce_int(value)

    @staticmethod
    def _signal_snapshot(day_df: pd.DataFrame, symbol: str) -> Dict[str, float]:
        snapshot: Dict[str, float] = {}
        if day_df is None or day_df.empty or symbol not in day_df.index:
            return snapshot
        for col in (
            "trend_turning_signal",
            "ewo_zero_cross_signal",
            "ewo",
            SIGNAL_BUY_COLUMN,
            SIGNAL_SELL_COLUMN,
        ):
            if col in day_df.columns:
                value = day_df.at[symbol, col]
                if pd.notna(value):
                    snapshot[col] = _coerce_float(value)
        return snapshot


def simulate_cbs_ewo_portfolio(
    panel: pd.DataFrame,
    trading_days: Sequence[pd.Timestamp],
    initial_capital: float = 600_000.0,
    max_positions: int = 10,
    min_hold_days: int = 1,
    ranking_field: str = "ewo",
) -> PortfolioSimulationResult:
    """Run the CBS+EWO portfolio simulator on a precomputed panel."""

    if panel is None or panel.empty:
        raise ValueError("Signal panel is empty; cannot run simulation")

    if not trading_days:
        raise ValueError("No trading days provided")

    simulator = CBS_EWOPortfolioSimulator(
        initial_capital=initial_capital,
        max_positions=max_positions,
        min_hold_days=min_hold_days,
        ranking_field=ranking_field,
    )

    logs: List[DailyLog] = []
    eq_values: List[float] = []
    eq_index: List[pd.Timestamp] = []

    for idx, day in enumerate(trading_days):
        day_ts = pd.to_datetime(day)
        try:
            day_slice = panel.xs(day_ts, level=0)
        except KeyError:
            day_slice = pd.DataFrame()
        if isinstance(day_slice, pd.Series):
            day_slice = day_slice.to_frame().T
        if not day_slice.empty and isinstance(day_slice.index, pd.MultiIndex):
            day_slice.index = day_slice.index.get_level_values(-1)
        log = simulator.step(day_ts, idx, day_slice)
        logs.append(log)
        eq_values.append(log.equity)
        eq_index.append(day_ts)

    equity_curve = pd.Series(eq_values, index=eq_index, name="strategy_equity")
    trades_df = _logs_to_trades_df(logs)

    return PortfolioSimulationResult(
        equity_curve=equity_curve,
        daily_logs=logs,
        trades=trades_df,
    )


def _logs_to_trades_df(logs: Sequence[DailyLog]) -> pd.DataFrame:
    rows: List[Dict[str, float]] = []
    for log in logs:
        for action in chain(log.buys, log.sells):
            row = {
                "date": log.date,
                "action": action.action,
                "symbol": action.symbol,
                "price": action.price,
                "qty": action.qty,
                "value": action.value,
                "reason": action.reason,
                "pnl": action.pnl,
                "cash_after": action.cash_after,
            }
            for key, value in action.signal_snapshot.items():
                row[key] = value
            rows.append(row)
    return pd.DataFrame(rows)
