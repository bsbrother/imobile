"""Regression: the skipped-buy record must read the local `latest` Series.

`analyze_stocks_and_generate_orders` records a candidate it skips because no
cash/slots remain. That record used to read `data_provider.latest` — an attribute
no data provider defines — so every such skip raised AttributeError, was swallowed
by the per-symbol ``except``, and the record was lost, spamming one ERROR per
candidate ("'TushareDataProvider' object has no attribute 'latest'"). The value it
wanted is the local ``latest = stock_data.iloc[-1]`` used three lines below.
"""
from __future__ import annotations

import pandas as pd

from backtest import cli


def _frame(n: int = 40) -> pd.DataFrame:
    idx = pd.date_range('2024-01-02', periods=n, freq='B')
    return pd.DataFrame({
        'date': idx,
        'name': ['浦发银行'] * n,
        'close': [10.0 + i * 0.01 for i in range(n)],
        'high': [10.3 + i * 0.01 for i in range(n)],
        'low': [9.8 + i * 0.01 for i in range(n)],
        'open': [10.0 + i * 0.01 for i in range(n)],
        'volume': [1e6] * n,
        'turnover_rate': [1.0] * n,
        'volume_ratio': [1.0] * n,
        'pe': [5.0] * n,
    })


def test_skipped_buy_record_uses_local_latest(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.data_provider, 'get_stock_data', lambda *a, **k: _frame())
    monkeypatch.setattr(cli.data_provider, 'get_index_data', lambda *a, **k: _frame())

    result = cli.analyze_stocks_and_generate_orders(
        symbols=['600000.SH'],
        current_cash=0.0,
        initial_cash=600000.0,
        remaining_slots=10,
        base_date='20240103',
        output_file=str(tmp_path / 'smart_orders.json'),
    )

    skipped = result.get('skipped_buy_orders', [])
    assert skipped, "a pick should be recorded as skipped when there is no cash"
    assert skipped[0]['name'] == '浦发银行'
