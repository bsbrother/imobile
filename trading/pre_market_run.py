#!/usr/bin/env python3
"""
Pre-market run for 2026-06-29 — pick stocks, compute orders, generate report.

NO --submit: orders are NOT written to DB or app (dry run).
WITH --submit: submit to app first, then sync app→DB.

Output: backtest/results/daily/pre_market_yyyymmdd.md
"""
import os, sys, json, asyncio, subprocess
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + '/utils')
import dotenv; dotenv.load_dotenv(os.path.expanduser('.env'), override=False)
from loguru import logger

THIS_DATE = datetime.now().strftime('%Y%m%d')
USER_ID = 1
REPORT_PATH = 'backtest/results'
VENV_PYTHON = '/home/kasm-user/apps/imobile/.venv/bin/python'


def resolve_code(name: str, user_id: int, cursor) -> str:
    """Resolve stock code from name using multiple sources."""
    # Try holding_stocks first
    for lookup in [name, name.replace('Ａ', 'A').replace('Ｂ', 'B')]:
        row = cursor.execute(
            "SELECT code FROM holding_stocks WHERE user_id=? AND name=?",
            (user_id, lookup)
        ).fetchone()
        if row and row[0] and row[0] != '000000':
            return row[0]
    # Try transactions
    row = cursor.execute(
        "SELECT code FROM transactions WHERE user_id=? AND name=? AND code != '000000' LIMIT 1",
        (user_id, name)
    ).fetchone()
    if row:
        return row[0]
    # Try stock index
    try:
        from trading.sync_app_to_db import init_stock_index_map, get_stock_code_by_name
        init_stock_index_map()
        code = get_stock_code_by_name(name, user_id)
        if code:
            return code
    except Exception:
        pass
    return '000000'


async def main(submit: bool = False):
    from shared.db.db import DB
    from backtest.utils.trading_calendar import calendar
    from backtest.utils.market_regime import detect_market_regime

    # ── Step 1: Read app state ──
    logger.info("═══ Step 1: Read app state ═══")
    from trading.guotai import login, pre_requirements, parse_csv_data
    from trading.sync_app_to_db import get_summary_position_from_app_position_page_structured

    login()
    tools, llm, config = await pre_requirements()

    app_cash = None
    app_positions_raw = []

    pos_csv = await get_summary_position_from_app_position_page_structured(config, llm, tools)
    if pos_csv:
        sections = pos_csv.strip().split('\n\n')
        if sections:
            header, summary_rows = parse_csv_data(sections[0])
            if summary_rows:
                app_cash = float(summary_rows[0][4])
        if len(sections) > 1:
            _, pos_rows = parse_csv_data(sections[1])
            for row in pos_rows:
                app_positions_raw.append({
                    'name': row[0],
                    'holdings': int(row[2]), 'available': int(row[3]),
                    'current_price': float(row[4]), 'cost': float(row[5])
                })

    # Resolve codes for all positions
    app_positions = []
    with DB.cursor() as cursor:
        for pos in app_positions_raw:
            code = resolve_code(pos['name'], USER_ID, cursor)
            app_positions.append({**pos, 'code': code})

    logger.info(f"App: Cash=¥{app_cash:,.2f}, Positions={len(app_positions)}")

    # Verify DB matches
    with DB.cursor() as cursor:
        db_cash = float(cursor.execute(
            "SELECT cash FROM summary_account WHERE user_id=?", (USER_ID,)
        ).fetchone()[0] or 0)
        db_holdings = cursor.execute(
            "SELECT COUNT(*) FROM holding_stocks WHERE user_id=? AND holdings > 0",
            (USER_ID,)
        ).fetchone()[0]
    cash_diff = abs(db_cash - (app_cash or 0))
    if cash_diff > 500 or db_holdings != len(app_positions):
        logger.error(f"DB mismatch! Cash diff=¥{cash_diff:,.0f}, Holdings: DB={db_holdings} App={len(app_positions)}")
        raise RuntimeError("Run pre-start-real-trading.py first to sync DB.")
    logger.info(f"✅ DB matches App: Cash=¥{db_cash:,.2f}, Holdings={db_holdings}")

    # ── Invalidate recent OHLCV cache to avoid stale prices ──
    logger.info("═══ Invalidate recent OHLCV cache ═══")
    from backtest import DB_CACHE_FILE
    from backtest.data.sqlite_cache import SQLiteDataCache
    cache = SQLiteDataCache(DB_CACHE_FILE)
    cache.invalidate_recent(data_type='ohlcv_data', days=3)

    # ── Step 2: Pick stocks ──
    logger.info("═══ Step 2: Pick stocks ═══")
    regime_data = detect_market_regime(THIS_DATE)
    regime_name = regime_data['regime']
    take_profit_pct = regime_data.get('take_profit_pct', 2.0)
    sl_pct = float(os.getenv(f'SL_{regime_name.upper()}', str(regime_data.get('stop_loss_pct', 0.025))))
    max_hold_days = regime_data.get('max_hold_days', 2)

    # Read HOLD_DAYS_MULT from env
    hold_mult = float(os.getenv('HOLD_DAYS_MULT', '1.0'))
    effective_hold = int(max_hold_days * hold_mult)
    logger.info(f"Regime: {regime_name.upper()}, TP=200%, SL={sl_pct*100:.1f}%, "
                f"MaxHold={max_hold_days}d × {hold_mult} = {effective_hold}d")

    _daily_dir = os.path.join('backtest', 'results', 'daily')
    os.makedirs(_daily_dir, exist_ok=True)
    pick_file = os.path.join(_daily_dir, f'pick_stocks_{THIS_DATE}.json')

    result = subprocess.run(
        [VENV_PYTHON, 'backtest/strategies/ts_7AZ.py', THIS_DATE, 'ts_7AZ', '--no-search', '--no-ai'],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"Stock picker failed: {result.stderr}")

    tmp_file = '/tmp/tmp'
    fallback = '/tmp/ts_7AZ_tmp.json'
    picks_data = {}
    src = tmp_file if os.path.exists(tmp_file) else fallback
    with open(src) as f:
        picks_data = json.load(f)

    selected = picks_data.get('selected_stocks', [])
    score_min = int(os.getenv('SCORE_MIN', '0'))
    if score_min > 0:
        selected = [s for s in selected if s.get('score', 0) >= score_min]
    max_positions = {'bull': 12, 'normal': 10, 'volatile': 8, 'bear': 5}.get(regime_name, 10)
    selected = selected[:max_positions]

    pick_output = {
        'pick_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'base_date': calendar.get_trading_days_before(THIS_DATE, 1),
        'target_trading_date': THIS_DATE,
        'market_pattern': regime_name,
        'regime_data': regime_data,
        'selected_stocks': selected,
    }
    with open(pick_file, 'w') as f:
        json.dump(pick_output, f)
    logger.info(f"Picked {len(selected)} stocks → {pick_file}")

    # ── Step 3: Generate BUY orders via cli analyze ──
    logger.info("═══ Step 3: Generate BUY orders ═══")
    smart_file = os.path.join(_daily_dir, f'smart_orders_{THIS_DATE}.json')

    subprocess.run(
        [VENV_PYTHON, '-m', 'backtest.cli', 'analyze',
         '--stocks-file', pick_file, '-o', smart_file,
         '--initial-cash', str(app_cash)],
        capture_output=True, timeout=120
    )

    with open(smart_file) as f:
        orders_data = json.load(f)
    cli_orders = orders_data.get('smart_orders', [])

    # ── Step 4: Cash-limit BUY orders ──
    logger.info("═══ Step 4: Cash-limit BUY orders ═══")
    buy_orders = []
    remaining_cash = app_cash

    # Build set of already-held stock codes (strip suffix for matching)
    held_codes = set()
    for pos in app_positions:
        code = pos.get('code', '')
        held_codes.add(code)
        held_codes.add(code.split('.')[0])  # strip .SZ/.SH suffix

    for o in cli_orders:
        sym = o['symbol']
        code_clean = sym.replace('.SZ','').replace('.SH','').replace('.BJ','')
        
        # Skip if already held — don't extend holding_days by re-buying
        if sym in held_codes or code_clean in held_codes:
            logger.info(f"  SKIP {sym} {o['name']}: already held")
            continue
        
        min_qty = 200 if (sym.startswith('3') or sym.startswith('688')) else 100
        min_cost = o['buy_price'] * min_qty

        if remaining_cash < min_cost:
            logger.info(f"  SKIP {sym} {o['name']}: cash=¥{remaining_cash:,.0f} < min=¥{min_cost:,.0f}")
            continue

        max_qty = int(remaining_cash / o['buy_price'] / 100) * 100
        qty = min(o['buy_quantity'], max_qty)
        if qty < min_qty:
            continue

        actual_cost = o['buy_price'] * qty
        remaining_cash -= actual_cost

        buy_orders.append({
            'symbol': sym,
            'name': o['name'],
            'buy_price': o['buy_price'],
            'sell_take_profit_price': round(o['buy_price'] * (1 + take_profit_pct), 2),
            'sell_stop_loss_price': round(o['buy_price'] * (1 - sl_pct), 2),
            'buy_quantity': qty,
        })
        logger.info(f"  BUY {sym} {o['name']}: ¥{o['buy_price']:.2f} ×{qty} = ¥{actual_cost:,.0f}, "
                    f"cash_left=¥{remaining_cash:,.0f}")

    # ── Step 5: TP/SL orders for ALL holdings ──
    logger.info("═══ Step 5: TP/SL orders for holdings ═══")
    last_trade_date = calendar.get_trading_days_before(THIS_DATE, 1).replace('-', '')

    tpsl_orders = []
    for pos in app_positions:
        code = pos['code']
        name = pos['name']
        h_cost = pos['cost']
        h_qty = pos['holdings']
        h_price = pos['current_price']

        # All legacy positions are FORCE-SELL — they predate real trading.
        # TP > current_price (won't trigger), SL < current_price (will trigger sell).
        profit_price = round(h_price * 1.10, 2)
        lose_price = round(h_price * 0.97, 2)

        tpsl_orders.append({
            'symbol': code,
            'name': name,
            'sell_take_profit_price': profit_price,
            'sell_stop_loss_price': lose_price,
            'buy_quantity': h_qty,
            'cost': h_cost,
            'current_price': h_price,
            'status': 'EXPIRED (force-sell)',
        })
        logger.info(f"  ⚠️ FORCE-SELL {code} {name}: TP=¥{profit_price:.2f} SL=¥{lose_price:.2f} ×{h_qty} "
                    f"(cost=¥{h_cost:.2f}, price=¥{h_price:.2f})")

    # ── Step 6: Submit to app (if --submit) ──
    if submit:
        logger.info("═══ Step 6: Submit orders to app ═══")
        from trading.create_order_buy import create_buy_order
        from trading.create_order_tp_sl import create_tp_sl_order

        for o in buy_orders:
            code = o['symbol'].split('.')[0]
            create_buy_order(code=code, price=str(o['buy_price']),
                           quantity=str(o['buy_quantity']), submit=True)
            logger.info(f"  ✅ BUY: {code} @{o['buy_price']} ×{o['buy_quantity']}")

        for o in tpsl_orders:
            code = o['symbol'].split('.')[0] if '.' in o['symbol'] else o['symbol']
            if code == '000000':
                continue
            create_tp_sl_order(code=code, tp_price=str(o['sell_take_profit_price']),
                             sl_price=str(o['sell_stop_loss_price']),
                             quantity=str(o['buy_quantity']), submit=True)
            logger.info(f"  ✅ TP/SL: {code} TP={o['sell_take_profit_price']} SL={o['sell_stop_loss_price']}")

        from trading.sync_app_to_db import cron_sync_app_to_db
        await cron_sync_app_to_db(check_trading_day_and_time=False)
        logger.info("  ✅ Synced app → DB")
    else:
        logger.info("═══ Step 6: Skipped (no --submit, dry run) ═══")

    # ── Step 7: Generate pre-market report ──
    logger.info("═══ Step 7: Generate pre-market report ═══")

    total_buy_cost = sum(o['buy_price'] * o['buy_quantity'] for o in buy_orders)

    lines = [
        f"# Pre-Market Report — {THIS_DATE[:4]}-{THIS_DATE[4:6]}-{THIS_DATE[6:]}",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Strategy:** ts_7AZ (CANSLIM)",
        f"**Market Regime:** {regime_name.upper()}",
        f"**Submit to app:** {'YES' if submit else 'NO (dry run)'}",
        "",
        "## Account Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Cash Available | ¥{app_cash:,.2f} |",
        f"| Holdings (legacy) | {len(app_positions)} |",
        f"| Regime | {regime_name.upper()} / TP=200% / SL={sl_pct*100:.1f}% / MaxHold={effective_hold}d |",
        f"| Max Positions | {max_positions} ({len(app_positions)} legacy + {max_positions - len(app_positions)} slots free) |",
        "",
    ]

    # BUY Orders
    lines.append(f"## BUY Orders ({len(buy_orders)})")
    lines.append("")
    if buy_orders:
        lines.extend([
            "| # | Symbol | Name | Buy Price | TP | SL | Qty | Cost |",
            "|---|--------|------|-----------|-----|-----|-----|------|",
        ])
        for i, o in enumerate(buy_orders):
            cost = o['buy_price'] * o['buy_quantity']
            lines.append(
                f"| {i+1} | {o['symbol']} | {o['name']} | "
                f"¥{o['buy_price']:.2f} | ¥{o['sell_take_profit_price']:.2f} | "
                f"¥{o['sell_stop_loss_price']:.2f} | "
                f"{o['buy_quantity']} | ¥{cost:,.0f} |"
            )
        over = total_buy_cost - app_cash
        status = "✅ WITHIN BUDGET" if over <= 0 else f"⚠️ OVER by ¥{over:,.0f}"
        lines.append(
            f"| **Total** | | ¥{app_cash:,.2f} avail | | | | | **¥{total_buy_cost:,.0f}** ({status}) |"
        )
    else:
        lines.append("*No BUY orders (insufficient cash or slots).*")

    # TP/SL Orders
    lines.extend(["", f"## TP/SL Orders — Holdings ({len(tpsl_orders)} total)", ""])
    if tpsl_orders:
        lines.extend([
            "| # | Symbol | Name | Cost | Price | TP | SL | Qty | Status |",
            "|---|--------|------|------|-------|-----|-----|-----|--------|",
        ])
        for i, o in enumerate(tpsl_orders):
            lines.append(
                f"| {i+1} | {o['symbol']} | {o['name']} | "
                f"¥{o['cost']:.2f} | ¥{o['current_price']:.2f} | "
                f"¥{o['sell_take_profit_price']:.2f} | ¥{o['sell_stop_loss_price']:.2f} | "
                f"{o['buy_quantity']} | ⚠️ {o['status']} |"
            )

    # Order Summary
    lines.extend(["", "## Order Summary", ""])
    lines.extend([
        "| Type | Count | Detail |",
        "|------|-------|--------|",
        f"| BUY (CANSLIM picks) | {len(buy_orders)} | Total ¥{total_buy_cost:,.0f} / ¥{app_cash:,.2f} cash |",
        f"| TP/SL (force-sell legacy) | {len(tpsl_orders)} | TP=pricex1.10 above, SL=pricex0.97 below → triggers sell |",
        f"| **TOTAL ORDERS** | **{len(buy_orders) + len(tpsl_orders)}** | **{len(buy_orders)} BUY + {len(tpsl_orders)} TP/SL** |",
        "",
        "> **Note:** When using `--submit`, orders are pushed to the broker app first, then `sync_app_to_db()` persists them to DB. Without `--submit`, this is a dry run — no DB or app changes.",
    ])

    report = "\n".join(lines)
    report_file = os.path.join(REPORT_PATH, f'daily/pre_market_{THIS_DATE}.md')
    with open(report_file, 'w') as f:
        f.write(report)
    logger.info(f"✅ Report saved: {report_file}")
    print(report)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--submit', action='store_true', help='Submit orders to broker app')
    args = parser.parse_args()
    asyncio.run(main(submit=args.submit))
