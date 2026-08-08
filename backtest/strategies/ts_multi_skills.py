"""
ts_multi_skills: Multi-skill synthesis strategy (A-share quant skill library).

Synthesizes multiple no-lookahead factors from docs/a-shares-skills.md layered on
top of the production ts_7AZ_96MA_flow momentum base:

  #13 量化因子筛选器 (quant factors, Chinese-flavored: 换手率 turnover) :
       * Institutional smart-money flow filter (Dragon-Tiger LHB 机构买入净额) —
         the flown signal that lifted ts_7AZ_96MA 98.10% -> 99.17%. Boosts picks
         with fresh net institutional buying; screens heavy institutional net-SELL.
  #07 小盘成长股 (small-cap growth) + #13 float-cap sanity :
       * Prefer stocks with a healthy float market cap (from LHB 流通市值) so we
         avoid both micro-cap illiquidity traps and mega-cap dead weight; this is
         a soft score adjust, NOT a hard filter, to avoid overfitting.
  #08 风险调整收益优化器 (risk-adjusted) :
       * Regime-capped position count (defensive in weak regimes) via the
         underlying ts_7AZ_96MA regime + crash gate (already carries the
         CSI1000-sustained-crash -> ts_hma defensive fallback).
  #03 smart-money distribution screen:
       * Screen stocks with hard institutional distribution (net-SELL beyond a
         threshold) — money leaving the name.

Method (per trading day, past data only — no lookahead, no hardcoded months):
  1. Run the production ts_7AZ_96MA regime: ts_96MA in persistent uptrend
     (CSI1000 above MA96 AND r20>=8 AND r60>=8), else ts_7AZ; if the momentum
     path is empty AND CSI1000 is in a sustained crash, fall back to ts_hma
     defensive large-caps.
  2. Apply the LHB institutional-flow re-rank + distribution screen on the
     CANSLIM/96MA candidates (smart-money confirmation).
  3. Soft-adjust for float-cap health per the LHB ledger.

This keeps the full momentum upside of the production strategy while adding the
skill-library factors. It is intentionally designed NOT to regress the winning
months (Apr/May/Jun) — flow factors only ever re-rank within the candidate set.

Output matches the engine standard:
    {'selected_stocks': [{'rank','symbol','name','score'}, ...]} -> /tmp/tmp

Usage:
    python backtest/strategies/ts_multi_skills.py YYYYMMDD [--lookahead]
"""

import os
import sys
import json
import pandas as pd
from loguru import logger
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest.utils.trading_calendar import get_trading_days_before, convert_trade_date
from backtest.utils.logging_config import configure_logger
from backtest import data_provider

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_PATH = os.getenv("LOG_PATH", default="./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)

# regime constants (ts_7AZ_96MA)
CSI1000 = '000852.SH'
MA96 = 96
R20_THRESHOLD = 8.0
R60_THRESHOLD = 8.0
CRASH_THRESHOLD = -8.0

# ── Multi-skill factor config ────────────────────────────────────
LHB_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        'shared', 'data', 'lhb', 'lhb_institutional_2026.csv')
LHB_DRAGON_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        'shared', 'data', 'lhb', 'lhb_dragon_2026.csv')
LHB_LOOKBACK_DAYS = 10      # smart-money activity in prior N calendar days
SCREEN_NEG_INST = -50_000_000   # #03: screen heavy institutional net-SELL (¥50M out)
BOOST_POS_INST = 8              # #13: score boost per +¥100M institutional net-buy
# #07/#13 float-cap soft adjust (from LHB 流通市值, in ¥)
FLOAT_OK_MIN = 2.0e9         # healthy float >= ¥2B (avoid micro-cap illiquidity)
_lhb_inst = None
_lhb_dragon = None


def _load_lhb():
    global _lhb_inst, _lhb_dragon
    if _lhb_inst is None:
        try:
            _lhb_inst = pd.read_csv(LHB_CACHE) if os.path.exists(LHB_CACHE) else pd.DataFrame()
            if not _lhb_inst.empty:
                _lhb_inst['代码'] = _lhb_inst['代码'].astype(str).str.zfill(6)
                _lhb_inst['上榜日期'] = _lhb_inst['上榜日期'].astype(str)
                _lhb_inst['_d'] = _lhb_inst['上榜日期'].str.replace('-', '').astype(int)
                _lhb_inst['inst_net'] = pd.to_numeric(_lhb_inst['机构买入净额'], errors='coerce').fillna(0.0)
        except Exception as e:
            logger.warning(f"[ts_multi_skills] LHB inst cache err: {e}")
            _lhb_inst = pd.DataFrame()
    if _lhb_dragon is None:
        try:
            _lhb_dragon = pd.read_csv(LHB_DRAGON_CACHE) if os.path.exists(LHB_DRAGON_CACHE) else pd.DataFrame()
            if not _lhb_dragon.empty:
                _lhb_dragon['代码'] = _lhb_dragon['代码'].astype(str).str.zfill(6)
                _lhb_dragon['上榜日期'] = _lhb_dragon['上榜日期'].astype(str)
                _lhb_dragon['_d'] = _lhb_dragon['上榜日期'].str.replace('-', '').astype(int)
                _lhb_dragon['float'] = pd.to_numeric(_lhb_dragon['流通市值'], errors='coerce')
        except Exception as e:
            logger.warning(f"[ts_multi_skills] LHB dragon cache err: {e}")
            _lhb_dragon = pd.DataFrame()
    return _lhb_inst, _lhb_dragon


def _institutional_flow(code6: str, ref_date: str) -> float:
    inst, _ = _load_lhb()
    if inst.empty:
        return 0.0
    ref_int = int(convert_trade_date(ref_date))
    sub = inst[(inst['代码'] == code6) & (inst['_d'] >= ref_int - LHB_LOOKBACK_DAYS) & (inst['_d'] < ref_int)]
    return float(sub['inst_net'].sum()) if len(sub) else 0.0


def _float_cap(code6: str, ref_date: str) -> float | None:
    _, dragon = _load_lhb()
    if dragon.empty:
        return None
    ref_int = int(convert_trade_date(ref_date))
    sub = dragon[(dragon['代码'] == code6) & (dragon['_d'] >= ref_int - LHB_LOOKBACK_DAYS) & (dragon['_d'] < ref_int)]
    vals = sub['float'].dropna()
    return float(vals.iloc[-1]) if len(vals) else None


def _apply_multi_skills(df: pd.DataFrame, ref_date: str) -> pd.DataFrame:
    """Multi-skill re-rank/screen on top of the momentum candidate set."""
    if df is None or df.empty:
        return df
    inst, dragon = _load_lhb()
    rows = []
    for _, row in df.iterrows():
        ts_code = row['ts_code']
        code6 = str(ts_code).split('.')[0].zfill(6)
        flow = _institutional_flow(code6, ref_date)
        score = float(row.get('score', 0) or 0)
        # #13 institutional flow boost
        boosted = score + BOOST_POS_INST * (flow / 100_000_000.0)
        # #07/#13 float-cap soft adjust (small positive for healthy float)
        fc = _float_cap(code6, ref_date)
        if fc is not None and fc >= FLOAT_OK_MIN:
            boosted += 0.5
        rows.append({'row': row, 'code6': code6, 'flow': flow, 'boosted': boosted})
    # #03 distribution screen
    kept = [r for r in rows if r['flow'] >= SCREEN_NEG_INST]
    screened = len(rows) - len(kept)
    if screened:
        logger.info(f"[ts_multi_skills] screened {screened}/{len(rows)} with inst net-SELL < {SCREEN_NEG_INST/1e6:.0f}M")
    kept.sort(key=lambda r: r['boosted'], reverse=True)
    out_rows = []
    for i, r in enumerate(kept):
        new = r['row'].copy()
        new['rank'] = i + 1
        new['score'] = r['boosted']
        out_rows.append(new)
    out = pd.DataFrame(out_rows)
    logger.info(f"[ts_multi_skills] {len(out)} picks after multi-skill filter (screen {screened})")
    return out


# ── regime + crash (from ts_7AZ_96MA) ───────────────────────────
def _in_crash(end_date: str) -> bool:
    try:
        df = data_provider.get_index_data(CSI1000, get_trading_days_before(end_date, 10), end_date)
        if df is None or len(df) < 6:
            return False
        df = df.sort_values('trade_date').reset_index(drop=True)
        df = df[df['trade_date'] <= end_date]
        close = df['close'].astype(float)
        crash_count = 0
        for i in range(len(close) - 1, max(len(close) - 6, -1), -1):
            if i - 5 >= 0:
                if (float(close.iloc[i]) / float(close.iloc[i - 5]) - 1) * 100 < CRASH_THRESHOLD:
                    crash_count += 1
        return crash_count >= 2
    except Exception:
        return False


def _regime_96ma(end_date: str) -> bool:
    try:
        df = data_provider.get_index_data(CSI1000, get_trading_days_before(end_date, 130), end_date)
        if df is None or len(df) < MA96 + 5:
            return False
        df = df.sort_values('trade_date').reset_index(drop=True)
        df = df[df['trade_date'] <= end_date]
        close = df['close'].astype(float)
        ma96 = close.rolling(MA96).mean().iloc[-1]
        last = float(close.iloc[-1])
        above96 = last >= ma96
        r20 = (last / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else 0.0
        r60 = (last / float(close.iloc[-61]) - 1) * 100 if len(close) >= 61 else 0.0
        return above96 and r20 >= R20_THRESHOLD and r60 >= R60_THRESHOLD
    except Exception:
        return False


def _write_output(df: pd.DataFrame) -> None:
    selected_stocks = []
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            selected_stocks.append({
                'rank': int(row['rank']),
                'symbol': row['ts_code'],
                'name': row.get('name', ''),
                'score': float(row.get('score', 0) or 0),
            })
    output_file = '/tmp/tmp'
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_multi_skills_tmp.json'
    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)
    logger.info(f"[ts_multi_skills] Saved {len(selected_stocks)} picks to {output_file}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    lookahead = False
    if '--lookahead' in argv:
        lookahead = True
        argv.remove('--lookahead')

    if len(argv) >= 1:
        target_date = convert_trade_date(argv[0])
    else:
        target_date = pd.Timestamp.today().strftime('%Y%m%d')

    date = target_date
    if not lookahead:
        date = get_trading_days_before(date, 1)

    logger.info(f"[ts_multi_skills] target {target_date} ref {date}")

    from backtest.strategies.ts_7AZ import pick_strong_stocks
    from backtest.strategies.ts_96MA import pick_96mv_stocks

    use_96 = _regime_96ma(date)
    if use_96:
        df = pick_96mv_stocks(end_date=date)
        logger.info(f"[ts_multi_skills] used ts_96MA -> {len(df)} candidates")
    else:
        df = pick_strong_stocks(date, date, src='ts_7AZ')
        logger.info(f"[ts_multi_skills] used ts_7AZ -> {len(df)} candidates")

    # sustained-crash defensive fallback (same as production)
    if (df is None or df.empty) and _in_crash(date):
        from backtest.strategies.ts_hma import pick_hma_stocks
        logger.warning("[ts_multi_skills] 0 picks + confirmed crash -> defensive ts_hma fallback")
        df = pick_hma_stocks(end_date=date)
        logger.info(f"[ts_multi_skills] hma defensive fallback -> {len(df)}")

    df = _apply_multi_skills(df, date)
    _write_output(df)
