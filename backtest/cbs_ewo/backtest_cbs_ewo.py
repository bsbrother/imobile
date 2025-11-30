#!/usr/bin/env python3
"""CBS+EWO portfolio CLI with daily stock picks and benchmark comparison."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Literal, Sequence

import pandas as pd
from dotenv import load_dotenv

from backtest import data_provider, global_cm
from backtest.utils.logging_config import configure_logger
from backtest.utils.trading_calendar import (
    get_trading_days_before,
    get_trading_days_between,
)
from backtest.utils.util import convert_trade_date
from cbs_ewo import (
    SIGNAL_BUY_COLUMN,
    SIGNAL_SELL_COLUMN,
    build_signal_panel,
    simulate_cbs_ewo_portfolio,
    _compute_metrics,
)

load_dotenv()

CONFIG_FILE = os.getenv("CONFIG_FILE", default="/backtest/config.json")
BACKTEST_PATH = os.getenv("BACKTEST_PATH", "./backtest")
REPORT_PATH = Path(os.getenv("REPORT_PATH", os.path.join(BACKTEST_PATH, "backtest_results")))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_PATH = os.getenv("LOG_PATH", "./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)

MAX_POSITIONS = global_cm.get("trading_rules.position.sizeing.max_positions", 10)
INITIAL_CASH = global_cm.get("portfolio_config.initial_cash", 600000.0)
BENCHMARK_CODE = global_cm.get("init_info.benchmark_codes", {})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run CBS+EWO stock picking backtest with T+1 handling.",
    )
    parser.add_argument("start", help="Start date (YYYYMMDD or YYYY-MM-DD)")
    parser.add_argument("end", help="End date (YYYYMMDD or YYYY-MM-DD)")
    parser.add_argument(
        "--symbols",
        help="Comma-separated ts_codes for the universe (default: auto large-cap list)",
    )
    parser.add_argument(
        "--symbols-file",
        help="Path to a text file containing one ts_code per line.",
    )
    parser.add_argument(
        "--max-universe",
        type=int,
        default=120,
        help="Maximum number of symbols to auto-select when no list provided.",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        default=MAX_POSITIONS,
        help="Maximum concurrent holdings.",
    )
    parser.add_argument(
        "--benchmarks",
        default="SSE,CSI300",
        help="Comma-separated benchmark keys or ts_code values.",
    )
    parser.add_argument(
        "--report-prefix",
        help="Optional prefix for generated CSV/JSON reports.",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Skip writing report files (outputs remain on stdout).",
    )
    parser.add_argument(
        "--tplus0",
        action="store_true",
        help="Allow same-day selling (disables T+1 hold requirement).",
    )
    parser.add_argument(
        "--buy-mode",
        choices=["strict", "relaxed"],
        default="strict",
        help=(
            "Choose how CBS+EWO entries are generated: 'strict' requires both "
            "divergence and zero-cross, 'relaxed' allows either signal."
        ),
    )
    parser.add_argument(
        "--show-signal-counts",
        action="store_true",
        help="Print daily counts of buy/sell signals before running the simulator.",
    )
    return parser.parse_args()


def resolve_symbols(args: argparse.Namespace, max_universe: int) -> List[str]:
    if args.symbols:
        symbols = [sym.strip() for sym in args.symbols.split(",") if sym.strip()]
        if not symbols:
            raise ValueError("--symbols provided but no valid entries found")
        return symbols

    if args.symbols_file:
        path = Path(args.symbols_file)
        if not path.exists():
            raise FileNotFoundError(f"Symbols file not found: {path}")
        symbols = [line.strip() for line in path.read_text().splitlines() if line.strip()]
        if not symbols:
            raise ValueError(f"Symbols file {path} is empty")
        return symbols

    info_df = data_provider.get_basic_information()
    if info_df.empty:
        raise RuntimeError("Basic information retrieval failed; please provide --symbols")

    candidates = info_df.copy()
    candidates = candidates[candidates["list_status"] == "L"]
    candidates = candidates[~candidates["name"].str.contains("ST", na=False)]
    candidates = candidates[~candidates["ts_code"].str.startswith(("4", "8", "9"), na=False)]
    candidates = candidates.sort_values("list_date")
    universe = candidates["ts_code"].head(max_universe).tolist()
    if not universe:
        raise RuntimeError("Unable to build symbol universe automatically")
    return universe


def prepare_signal_panel(
    symbols: Sequence[str],
    start: str,
    end: str,
    buffer_days: int = 240,
    buy_mode: Literal["strict", "relaxed"] = "strict",
) -> pd.DataFrame:
    analysis_start = get_trading_days_before(start, buffer_days)
    raw = data_provider.get_stock_data(symbols=list(symbols), start_date=analysis_start, end_date=end)
    if raw.empty:
        raise RuntimeError("No stock data fetched for the requested universe and date range")
    panel = build_signal_panel(raw, buy_mode=buy_mode)
    panel = panel.loc[(panel.index.get_level_values(0) >= pd.to_datetime(start))]
    return panel


def compute_daily_signal_counts(panel: pd.DataFrame) -> pd.DataFrame:
    if panel is None or panel.empty:
        return pd.DataFrame(columns=[SIGNAL_BUY_COLUMN, SIGNAL_SELL_COLUMN])
    counts = (
        panel.reset_index()
        .groupby("trade_date")[[SIGNAL_BUY_COLUMN, SIGNAL_SELL_COLUMN]]
        .sum(min_count=1)
        .fillna(0.0)
    )
    counts.index = pd.to_datetime(counts.index)
    return counts.astype(int)


def fetch_benchmarks(
    benchmark_names: Sequence[str],
    trading_index: pd.DatetimeIndex,
    start: str,
    end: str,
) -> Dict[str, pd.Series]:
    curves: Dict[str, pd.Series] = {}
    for name in benchmark_names:
        code = BENCHMARK_CODE.get(name, name)
        try:
            df = data_provider.get_index_data(code, start_date=start, end_date=end)
        except Exception as exc:  # pragma: no cover - data provider guard
            print(f"Warning: failed to fetch benchmark {name} ({code}): {exc}")
            continue
        if df.empty:
            continue
        series = df.sort_values("trade_date").set_index("trade_date")["close"]
        series.index = pd.to_datetime(series.index)
        series = series.reindex(trading_index).ffill().bfill()
        if series.isna().all():
            continue
        ret = series.pct_change().fillna(0.0)
        curve = (1 + ret).cumprod() * INITIAL_CASH
        curve.name = f"{name}_equity"
        curves[name] = curve
    return curves


def summarize_daily_logs(result) -> pd.DataFrame:
    rows = []
    for log in result.daily_logs:
        rows.append(
            {
                "date": log.date.strftime("%Y-%m-%d"),
                "candidates": _fmt_candidates(log.ranked_candidates),
                "buys": _fmt_actions(log.buys),
                "sells": _fmt_actions(log.sells),
                "holdings": _fmt_holdings(log.holdings),
                "cash": log.cash,
                "equity": log.equity,
            }
        )
    return pd.DataFrame(rows)


def _fmt_candidates(candidates: Sequence[tuple[str, float]], limit: int = 5) -> str:
    top = candidates[:limit]
    return ", ".join(f"{sym}({score:.2f})" for sym, score in top)


def _fmt_actions(actions) -> str:
    parts = []
    for action in actions:
        text = f"{action.symbol}@{action.price:.2f}"
        if action.action == "SELL" and action.value:
            pnl_pct = (action.pnl / action.value) if action.value else 0.0
            text += f" pnl:{pnl_pct:.2%}"
        parts.append(text)
    return "; ".join(parts)


def _fmt_holdings(holdings: Dict[str, float]) -> str:
    if not holdings:
        return "-"
    return ", ".join(f"{sym}:{qty:.2f}" for sym, qty in holdings.items())


def save_reports(
    prefix: str,
    summary_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    metrics: Dict,
    benchmark_stats: Dict,
) -> None:
    REPORT_PATH.mkdir(parents=True, exist_ok=True)
    summary_path = REPORT_PATH / f"{prefix}_daily_summary.csv"
    trades_path = REPORT_PATH / f"{prefix}_trades.csv"
    metrics_path = REPORT_PATH / f"{prefix}_metrics.json"
    summary_df.to_csv(summary_path, index=False)
    trades_df.to_csv(trades_path, index=False)
    payload = {"strategy": metrics, "benchmarks": benchmark_stats}
    metrics_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"Saved reports to {summary_path}, {trades_path}, {metrics_path}")


def run_cli() -> None:
    args = parse_args()
    start = convert_trade_date(args.start)
    end = convert_trade_date(args.end)
    if not start or not end:
        raise ValueError("Unable to parse start or end date")
    if start > end:
        raise ValueError("Start date must be before end date")

    trading_days = get_trading_days_between(start, end)
    if not trading_days:
        raise RuntimeError("No trading days in the provided range")
    trading_index = pd.to_datetime(trading_days).to_list()

    symbols = resolve_symbols(args, args.max_universe)
    panel = prepare_signal_panel(symbols, start, end, buy_mode=args.buy_mode)

    if args.show_signal_counts:
        counts = compute_daily_signal_counts(panel)
        print("\n--- Signal Counts ---")
        lookup = {
            day: (
                int(row[SIGNAL_BUY_COLUMN]) if SIGNAL_BUY_COLUMN in row else 0,
                int(row[SIGNAL_SELL_COLUMN]) if SIGNAL_SELL_COLUMN in row else 0,
            )
            for day, row in counts.iterrows()
        }
        for day in pd.to_datetime(trading_index):
            buy_count, sell_count = lookup.get(day, (0, 0))
            print(f"{day.date()}: buys={buy_count:3d}, sells={sell_count:3d}")

    sim_result = simulate_cbs_ewo_portfolio(
        panel=panel,
        trading_days=trading_index,
        initial_capital=INITIAL_CASH,
        max_positions=args.max_positions,
        min_hold_days=0 if args.tplus0 else 1,
    )

    strategy_metrics = _compute_metrics(sim_result.equity_curve, None)
    benchmark_names = [name.strip() for name in args.benchmarks.split(",") if name.strip()]
    benchmark_curves = fetch_benchmarks(benchmark_names, pd.DatetimeIndex(trading_index), start, end)
    benchmark_stats = {}
    for name, curve in benchmark_curves.items():
        bench_metrics = _compute_metrics(curve, None)
        bench_metrics["excess_return_vs_strategy"] = (
            strategy_metrics["total_return"] - bench_metrics["total_return"]
        )
        benchmark_stats[name] = bench_metrics

    summary_df = summarize_daily_logs(sim_result)
    trades_df = sim_result.trades

    print("=== CBS+EWO Portfolio Backtest ===")
    print(f"Date range: {start} -> {end}")
    print(
        f"Universe size: {len(symbols)} | Max positions: {args.max_positions} | "
        f"buy_mode: {args.buy_mode}"
    )
    for key, value in strategy_metrics.items():
        if value is None:
            continue
        print(f"{key:28s}: {value: .4f}")

    if benchmark_stats:
        print("\n--- Benchmarks ---")
        for name, stats in benchmark_stats.items():
            print(
                f"{name:10s} total_return={stats['total_return']: .4f} "
                f"annualized={stats['annualized_return']: .4f} "
                f"excess_vs_strategy={stats['excess_return_vs_strategy']: .4f}"
            )

    print("\n--- Daily Plan ---")
    for _, row in summary_df.iterrows():
        print(
            f"{row['date']}: picks[{row['candidates']}] | buys[{row['buys']}] | "
            f"sells[{row['sells']}] | holdings[{row['holdings']}] | equity={row['equity']:.2f}"
        )

    if not args.no_report:
        prefix = args.report_prefix or f"cbs_ewo_{start}_{end}"
        save_reports(prefix, summary_df, trades_df, strategy_metrics, benchmark_stats)


if __name__ == "__main__":
    try:
        run_cli()
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"Error: {exc}")
        sys.exit(1)
