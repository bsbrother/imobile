import pandas as pd

from cbs_ewo import simulate_cbs_ewo_portfolio


def build_panel():
    dates = pd.to_datetime([
        "2025-10-23",
        "2025-10-24",
        "2025-10-27",
    ])
    records = []
    close_values = [10.0, 11.0, 12.0]
    buy_flags = [1, 0, 0]
    sell_flags = [1, 1, 0]
    for date, close, buy, sell in zip(dates, close_values, buy_flags, sell_flags):
        records.append(
            {
                "trade_date": date,
                "ts_code": "AAA.SZ",
                "close": close,
                "ewo": 1.0,
                "ewo_zero_cross_signal": 1 if buy else -1 if sell else 0,
                "trend_turning_signal": 1 if buy else -1 if sell else 0,
                "cbs_ewo_buy_signal": buy,
                "cbs_ewo_sell_signal": sell,
            }
        )
    panel = pd.DataFrame(records)
    panel.set_index(["trade_date", "ts_code"], inplace=True)
    return panel


def test_tplus_one_blocks_same_day_exit():
    panel = build_panel()
    trading_days = list(panel.index.get_level_values(0).unique())
    result = simulate_cbs_ewo_portfolio(
        panel=panel,
        trading_days=trading_days,
        initial_capital=100_000.0,
        max_positions=1,
        min_hold_days=1,
    )
    assert not result.daily_logs[0].sells
    assert result.daily_logs[1].sells


def test_equity_curve_progresses():
    panel = build_panel()
    trading_days = list(panel.index.get_level_values(0).unique())
    result = simulate_cbs_ewo_portfolio(
        panel=panel,
        trading_days=trading_days,
        initial_capital=100_000.0,
        max_positions=1,
        min_hold_days=1,
    )
    eq = result.equity_curve
    assert eq.index[0] == trading_days[0]
    assert eq.iloc[-1] > 0
