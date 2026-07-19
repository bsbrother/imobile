#!/usr/bin/env python3
"""
Post-market daily trading report with comprehensive order execution analysis.

Called by runner.py during post-market phase.
Output: backtest/results/daily/post_market_YYYYMMDD.md
"""
import os, sys, json
from datetime import datetime
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.db.db import DB


def generate_trading_report(this_date: str, user_id: int = 1) -> str:
    """Generate post-market report with order execution analysis + returns + suggestions."""

    _daily_dir = os.path.join('backtest', 'results', 'daily')
    os.makedirs(_daily_dir, exist_ok=True)
    report_file = os.path.join(_daily_dir, f'post_market_{this_date}.md')

    date_fmt = f"{this_date[:4]}-{this_date[4:6]}-{this_date[6:]}"

    # ── Load pre-market plan ──
    pre_market_file = os.path.join(_daily_dir, f'pre_market_{this_date}.json')
    pre_market_orders = []
    try:
        with open(pre_market_file) as f:
            pm_data = json.load(f)
            pre_market_orders = pm_data.get('smart_orders', [])
    except Exception:
        pass

    smart_orders_file = os.path.join(_daily_dir, f'smart_orders_{this_date}.json')
    if not pre_market_orders:
        try:
            with open(smart_orders_file) as f:
                so_data = json.load(f)
                pre_market_orders = so_data.get('smart_orders', [])
        except Exception:
            pass

    # Classify pre-market orders
    pm_buy_orders = [o for o in pre_market_orders if 'buy_price' in o]
    pm_sell_orders = [o for o in pre_market_orders if 'buy_price' not in o]

    with DB.cursor() as cursor:
        summary = cursor.execute(
            "SELECT total_assets, total_market_value, cash, "
            "floating_pnl_summary, position_percent FROM summary_account WHERE user_id=?",
            (user_id,)
        ).fetchone()

        holdings = cursor.execute(
            "SELECT code, name, holdings, available_shares, current_price, "
            "cost_basis_diluted, pnl_float, pnl_float_percent, market_value "
            "FROM holding_stocks WHERE user_id=? AND holdings > 0 ORDER BY code",
            (user_id,)
        ).fetchall()

        # All smart orders updated today (both triggered and cancelled)
        all_orders_today = cursor.execute(
            "SELECT code, name, trigger_condition, status, reason_of_ending, "
            "buy_or_sell_quantity, valid_until, order_number "
            "FROM smart_orders WHERE user_id=? AND last_updated LIKE ? "
            "ORDER BY status, code",
            (user_id, f"{date_fmt}%")
        ).fetchall()

        running_orders = [o for o in all_orders_today if o[3] == 'running']
        triggered_orders = [o for o in all_orders_today if o[3] in ('completed', 'cancelled')]
        expired_orders = [o for o in all_orders_today if o[3] == 'expired']

        # Previous day's transactions for P&L comparison
        prev_day_transactions = cursor.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id=? AND transaction_date LIKE ?",
            (user_id, f"{date_fmt}%")
        ).fetchone()[0]

    # ── Compute metrics ──
    db_cash = float(summary[2]) if summary and summary[2] else 0
    db_assets = float(summary[0]) if summary and summary[0] else 0
    db_mkt = float(summary[1]) if summary and summary[1] else 0
    db_pnl = float(summary[3]) if summary and summary[3] else 0

    # Benchmark
    benchmark_return = _get_benchmark_return(this_date)
    index_names = _get_all_indices(this_date)

    # ── Order Execution Analysis ──
    # Map holdings by code (strip .SZ/.SH suffix for matching)
    holding_map = {h[0]: h for h in holdings}
    
    # Which BUY orders triggered? Cross-reference pre-market BUY orders with holdings
    buy_triggered = []   # BUY orders that filled (stock in holdings)
    buy_unfilled = []    # BUY orders that didn't fill
    for o in pm_buy_orders:
        sym = o.get('symbol', o.get('code', ''))
        # Try exact match first, then strip suffix
        code_clean = sym.replace('.SZ','').replace('.SH','').replace('.BJ','')
        h = holding_map.get(sym) or holding_map.get(code_clean)
        if h:
            fill_price = h[5]  # cost_basis_diluted
            qty = h[2]
            cur_price = h[4] or 0
            pnl = h[6] or 0
            pnl_pct = h[7] or 0
            buy_triggered.append({
                'symbol': sym, 'name': o.get('name', h[1]),
                'planned_price': o.get('buy_price', 0),
                'fill_price': fill_price,
                'qty': qty, 'cur_price': cur_price,
                'pnl': pnl, 'pnl_pct': pnl_pct
            })
        else:
            buy_unfilled.append({
                'symbol': sym, 'name': o.get('name', ''),
                'planned_price': o.get('buy_price', 0), 'planned_qty': o.get('buy_quantity', 0)
            })

    # Which SELL (TP/SL) orders triggered? Cross-reference triggered orders with pre-market sell orders
    # Also check which holdings were sold (not in current holdings)
    sell_triggered = []
    sell_untriggered = []
    
    # Get triggered order codes
    triggered_codes = {o[0] for o in triggered_orders}
    
    # All legacy holdings that were present before today
    all_legacy = {'000006','000009','000717','002773','600279','600373',
                  '600176','603256','000725','600522','600881','601828',
                  '603279','002727','600981'}
    
    # Which were sold (no longer in holdings)?
    sold_codes = all_legacy - set(holding_map.keys())
    
    # Cross-reference with triggered orders
    for code in triggered_codes & sold_codes:
        o = next((o for o in triggered_orders if o[0] == code), None)
        reason = o[4] if o else 'unknown'
        sell_triggered.append({'symbol': code, 'name': o[1] if o else '', 'reason': reason})
    
    for code in sold_codes - triggered_codes:
        # Sold but no trigger recorded — likely force-sold by system
        sell_triggered.append({'symbol': code, 'name': '', 'reason': 'force-sell (expired/manual)'})

    for code in all_legacy & set(holding_map.keys()):
        sell_untriggered.append({'symbol': code, 'name': holding_map[code][1]})

    # Holdings analysis
    winning = [h for h in holdings if float(h[7] or 0) > 0]
    losing = [h for h in holdings if float(h[7] or 0) < 0]

    # New (today's) BUY positions vs legacy
    new_buy_codes = {o['symbol'].replace('.SZ','').replace('.SH','').replace('.BJ','') 
                     for o in buy_triggered}
    new_holds = [h for h in holdings if h[0] in new_buy_codes]
    legacy_holds = [h for h in holdings if h[0] not in new_buy_codes]
    
    new_buy_pnl = sum(float(h[6] or 0) for h in new_holds)
    legacy_pnl = sum(float(h[6] or 0) for h in legacy_holds)

    # Regime
    from backtest.utils.market_regime import detect_market_regime
    regime_data = {}
    try:
        regime_data = detect_market_regime(this_date)
    except Exception:
        pass
    regime_name = regime_data.get('regime', 'unknown')

    # ── Write report ──
    with open(report_file, 'w', encoding='utf-8') as f:
        w = f.write

        w(f"# Post-Market Report — {date_fmt}\n\n")
        w(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n")
        w(f"**Strategy:** ts_7AZ (CANSLIM)  \n")
        w(f"**Regime:** {regime_name.upper()}  \n\n---\n\n")

        # ════════════════════════════════════════
        # 1. Account Summary
        # ════════════════════════════════════════
        w("## Account Summary\n\n")
        w("| Metric | Value |\n|--------|-------|\n")
        w(f"| Total Assets | ¥{db_assets:,.2f} |\n")
        w(f"| Market Value | ¥{db_mkt:,.2f} |\n")
        w(f"| Available Cash | ¥{db_cash:,.2f} |\n")
        w(f"| Floating P&L | ¥{db_pnl:,.2f} |\n")
        w(f"| — from new BUY positions | ¥{new_buy_pnl:,.0f} |\n")
        w(f"| — from legacy holdings | ¥{legacy_pnl:,.0f} |\n")
        w(f"| Holdings | {len(holdings)} ({len(winning)}↑ / {len(losing)}↓) |\n")
        w(f"| Running Orders | {len(running_orders)} |\n")
        if benchmark_return is not None:
            w(f"| SSE Composite | {benchmark_return:+.2f}% |\n")
        w("\n")

        # ════════════════════════════════════════
        # 2. BUY Order Execution Analysis
        # ════════════════════════════════════════
        w("## BUY Order Execution Analysis\n\n")

        if buy_triggered:
            w(f"### Triggered ({len(buy_triggered)}/{len(pm_buy_orders)} BUY orders filled)\n\n")
            w("| Code | Name | Planned | Filled | Qty | Now | Return |\n")
            w("|------|------|---------|--------|-----|-----|--------|\n")
            total_buy_return = 0
            for b in buy_triggered:
                w(f"| {b['symbol']} | {b['name']} | ¥{b['planned_price']:.2f} "
                  f"| ¥{b['fill_price']:.2f} | {b['qty']} | ¥{b['cur_price']:.2f} "
                  f"| ¥{b['pnl']:,.0f} ({b['pnl_pct']:.1f}%) |\n")
                total_buy_return += b['pnl']
            w(f"\n**BUY positions return today:** ¥{total_buy_return:,.0f}\n\n")
        else:
            w("No BUY orders triggered today.\n\n")

        if buy_unfilled:
            w(f"### Not Triggered ({len(buy_unfilled)} BUY orders)\n\n")
            w("| Code | Name | Planned Price | Planned Qty |\n")
            w("|------|------|---------------|-------------|\n")
            for b in buy_unfilled:
                w(f"| {b['symbol']} | {b['name']} | ¥{b['planned_price']:.2f} | {b['planned_qty']} |\n")
            w(f"\n**{len(buy_unfilled)} BUY orders expired/cancelled unfilled.**\n\n")

        # ════════════════════════════════════════
        # 3. SELL (TP/SL) Order Execution Analysis
        # ════════════════════════════════════════
        w("## SELL Order Execution Analysis\n\n")

        if sell_triggered:
            w(f"### Triggered ({len(sell_triggered)} positions sold today)\n\n")
            w("| Code | Name | Reason |\n|------|------|--------|\n")
            for s in sell_triggered:
                w(f"| {s['symbol']} | {s['name']} | {s['reason']} |\n")
            w(f"\n**{len(sell_triggered)} positions exited.** Legacy holdings reduced from {len(all_legacy)} to {len(legacy_holds)}.\n\n")
        else:
            w("No SELL orders triggered today.\n\n")

        if sell_untriggered:
            w(f"### Not Triggered ({len(sell_untriggered)} legacy holdings still held)\n\n")
            w("| Code | Name |\n|------|------|\n")
            for s in sell_untriggered:
                w(f"| {s['symbol']} | {s['name']} |\n")
            w("\n")

        # ════════════════════════════════════════
        # 4. Holdings P&L Detail
        # ════════════════════════════════════════
        w("## Holdings Detail\n\n")

        if new_holds:
            w("### New Positions (BUY filled today)\n\n")
            w("| Code | Name | Qty | Cost | Price | MktVal | P&L | P&L% |\n")
            w("|------|------|-----|------|-------|--------|-----|------|\n")
            for h in new_holds:
                w(f"| {h[0]} | {h[1]} | {h[2]} | ¥{h[5] or 0:.2f} "
                  f"| ¥{h[4] or 0:.2f} | ¥{h[8] or 0:,.0f} "
                  f"| ¥{h[6] or 0:,.0f} | {h[7] or 0:.1f}% |\n")
            w(f"\n**Total new position P&L: ¥{new_buy_pnl:,.0f}**\n\n")

        if legacy_holds:
            w("### Legacy Positions\n\n")
            w("| Code | Name | Qty | Cost | Price | MktVal | P&L | P&L% |\n")
            w("|------|------|-----|------|-------|--------|-----|------|\n")
            for h in legacy_holds:
                w(f"| {h[0]} | {h[1]} | {h[2]} | ¥{h[5] or 0:.2f} "
                  f"| ¥{h[4] or 0:.2f} | ¥{h[8] or 0:,.0f} "
                  f"| ¥{h[6] or 0:,.0f} | {h[7] or 0:.1f}% |\n")
            w(f"\n**Total legacy P&L: ¥{legacy_pnl:,.0f}**\n\n")

        # ════════════════════════════════════════
        # 5. Benchmark
        # ════════════════════════════════════════
        w("## Benchmark Context\n\n")
        if index_names:
            w("| Index | Change |\n|-------|--------|\n")
            for name, ret in index_names:
                w(f"| {name} | {ret:+.2f}% |\n")
            w("\n")
        if benchmark_return is not None:
            new_total = sum(float(h[6] or 0) for h in new_holds)
            new_cost = sum((h[5] or 0) * (h[2] or 0) for h in new_holds)
            new_return = (new_total / new_cost * 100) if new_cost else 0
            w(f"**New BUY return:** {new_return:+.2f}% | **SSE:** {benchmark_return:+.2f}% | **Alpha:** {new_return - benchmark_return:+.2f}%\n")
        w("\n")

        # ════════════════════════════════════════
        # 6. Smart Orders Activity
        # ════════════════════════════════════════
        w("## Smart Orders Activity Today\n\n")
        if triggered_orders:
            w("| Code | Name | Trigger | Status | Reason |\n")
            w("|------|------|---------|--------|--------|\n")
            for o in triggered_orders:
                trigger = (o[2] or '')[:40]
                w(f"| {o[0]} | {o[1]} | {trigger} | {o[3]} | {o[4] or '-'} |\n")
            w("\n")
        if not triggered_orders:
            w("No order activity today.\n\n")

        # ════════════════════════════════════════
        # 7. Suggestions
        # ════════════════════════════════════════
        w("## Suggestions for Next Improvements\n\n")
        suggestions = []

        # 1. BUY execution summary
        if buy_triggered:
            bt = buy_triggered
            fill_vs_planned = sum(b['fill_price']/b['planned_price'] for b in bt if b['planned_price'])/max(len(bt),1)
            suggestions.append(
                f"**BUY fills: {len(buy_triggered)}/{len(pm_buy_orders)} triggered.** "
                f"Filled at average {sum(b['fill_price']/b['planned_price'] for b in buy_triggered)/len(buy_triggered)*100 - 100:+.1f}% vs planned. "
                f"Next pre-market: use fresh close prices to set accurate triggers."
            )
        if buy_unfilled:
            unfilled_names = ', '.join(b['symbol'] for b in buy_unfilled)
            suggestions.append(
                f"**{len(buy_unfilled)} BUY orders unfilled:** {unfilled_names}. "
                f"Price may not have dropped to trigger level. Re-evaluate in next CANSLIM pick."
            )

        # 2. SELL execution
        if sell_triggered:
            s = sell_triggered
            stop_hits = [x for x in s if '止损' in x['reason']]
            force_sells = [x for x in s if 'force' in x['reason'] or 'expired' in x['reason']]
            if stop_hits:
                suggestions.append(
                    f"**{len(stop_hits)} stop-loss hits:** {', '.join(x['symbol'] for x in stop_hits)}. "
                    f"SL protection worked — positions cut at predetermined levels."
                )
            if force_sells:
                suggestions.append(
                    f"**{len(force_sells)} force-sells:** {', '.join(x['symbol'] for x in force_sells)}. "
                    f"These were expired/manual stops. Verify they cleared correctly."
                )

        # 3. New position returns
        if new_holds:
            winners_new = [h for h in new_holds if float(h[7] or 0) > 0]
            losers_new = [h for h in new_holds if float(h[7] or 0) <= 0]
            if losers_new:
                l_names = ', '.join(f"{h[0]} ({float(h[7]):.1f}%)" for h in losers_new)
                suggestions.append(
                    f"**New position losers:** {l_names}. "
                    f"Entry timing may need adjustment — consider using lower Bollinger Band entry."
                )

        # 4. Cash deployment
        cash_pct = db_cash / db_assets * 100 if db_assets else 0
        max_pos = {'bull': 12, 'normal': 10, 'volatile': 8, 'bear': 5}.get(regime_name, 10)
        slots_free = max_pos - len(holdings)
        if db_cash > 5000 and slots_free > 0:
            suggestions.append(
                f"**Deploy ¥{db_cash:,.0f} idle cash ({cash_pct:.0f}%):** {slots_free} free "
                f"slots under {regime_name.upper()} regime (max {max_pos}). "
                f"CANSLIM pre-market will pick up to {slots_free} new stocks."
            )

        # 5. Market regime
        if benchmark_return is not None and benchmark_return > 1.5 and regime_name != 'bull':
            suggestions.append(
                f"**Regime may shift to BULL:** SSE {benchmark_return:+.2f}% today. "
                f"Monitor detect_market_regime() — bull regime allows 12 positions with tighter SL."
            )

        # 6. Task reminder
        suggestions.append(
            "**Next pre-market:** Run `python trading/runner.py --phase pre-market --submit` "
            "before 09:30 with fresh OHLCV cache (auto-invalidated) for accurate entry prices."
        )

        if not suggestions:
            suggestions.append("All systems nominal. Continue CANSLIM pre-market as usual.")

        for i, s in enumerate(suggestions):
            w(f"{i+1}. {s}\n\n")

        w("---\n\n")
        w("*Generated by trading/post_market_report.py — order execution analysis on real trading data.*\n")

    logger.info(f"✅ Report saved: {report_file}")
    return report_file


# ── Helpers ──

def _get_benchmark_return(this_date: str) -> float | None:
    try:
        from backtest import data_provider
        from backtest.utils.trading_calendar import calendar
        prev = calendar.get_trading_days_before(this_date, 1)
        df = data_provider.get_index_data('000001.SH', prev, this_date)
        if len(df) >= 2:
            return ((float(df.iloc[-1]['close']) - float(df.iloc[-2]['close']))
                    / float(df.iloc[-2]['close'])) * 100
    except Exception:
        pass
    return None


def _get_all_indices(this_date: str) -> list:
    indices = []
    try:
        from backtest import data_provider
        from backtest.utils.trading_calendar import calendar
        prev = calendar.get_trading_days_before(this_date, 1)
        for code, name in [('000001.SH','SSE'), ('399001.SZ','SZ'),
                           ('399006.SZ','ChiNext'), ('000300.SH','CSI300')]:
            df = data_provider.get_index_data(code, prev, this_date)
            if len(df) >= 2:
                ret = ((float(df.iloc[-1]['close']) - float(df.iloc[-2]['close']))
                       / float(df.iloc[-2]['close'])) * 100
                indices.append((name, ret))
    except Exception:
        pass
    return indices


if __name__ == '__main__':
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime('%Y%m%d')
    uid = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    generate_trading_report(date, uid)
