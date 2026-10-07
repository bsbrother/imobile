"""Day/profit-adaptive TP/SL for a held position — the strategy's bracket policy.

Lives here (rather than inside `backtest/engine.py`) so the live pre-market path can recompute a
holding's bracket with exactly the same rule on the same day, which is what makes the two paths
comparable. Pure function: no I/O, no clock, no database. `h_current_price` must be the PREVIOUS
day's close — feeding it anything later is lookahead.

Base: SL = cost*(1-sl_pct), TP = cost*(1+tp_pct). Then a carried prior trailing order is ratcheted
up, and (env-gated by HOLD_SL_ADAPT, default on):
  * breakeven — after SL_BREAKEVEN_DAY (default 1) held trading days, if in profit the SL is raised
    to at least cost, protecting capital;
  * trail — the SL keeps ratcheting to h_current_price*(1-SL_TRAIL_PCT) (default 5%);
  * TP ratchets with recent highs, letting winners run.
"""
from __future__ import annotations

import os


def adaptive_tp_sl(h_cost, h_current_price, days_held, tp_pct, sl_pct,
                   last_sl=None, last_tp=None):
    """(profit_price, lose_price) for a held position, given the previous close.

    `last_sl`/`last_tp` carry forward yesterday's trailing levels so a stop never loosens.
    """
    sl = h_cost * (1 - sl_pct) if h_cost > 0 else 0.0
    tp = h_cost * (1 + tp_pct) if h_cost > 0 else 0.0
    if last_sl is not None and last_sl > 0:
        sl = max(sl, float(last_sl))
    if last_tp is not None and last_tp > 0:
        tp = max(tp, float(last_tp))
    if os.getenv('HOLD_SL_ADAPT', 'true').lower() in ('true', '1', 'yes'):
        in_profit = h_current_price > h_cost
        breakeven_day = int(os.getenv('SL_BREAKEVEN_DAY', '1'))
        trail_pct = float(os.getenv('SL_TRAIL_PCT', '0.05'))
        if in_profit and days_held >= breakeven_day:
            sl = max(sl, h_cost)                             # breakeven shield
            sl = max(sl, h_current_price * (1 - trail_pct))  # trail up
        if in_profit and h_current_price > 0:
            tp = max(tp, h_current_price * (1 + tp_pct))     # let winners run
    return tp, sl
