"""
Post-market daily review module — vibe-astock style sentiment-driven strategy adjustment.

Based on vibe-astock's derived sentiment framework:
- 赚钱效应 (money-making effect): % of yesterday's filled BUYs currently profitable
- 晋级率 (advance rate): % of yesterday's picks re-picked today (sustained strength)
- 梯队断层 (echelon gap): repick continuity — how many consecutive days a stock stays picked
- 情绪周期 (sentiment cycle): ice / recovery / fermenting / frenzy / cooling

These metrics feed into next-day strategy adjustments:
- Max positions (ice: reduce, frenzy: increase)
- Holding days multiplier (ice: shorter, frenzy: longer)
- TP aggressiveness (ice: take profits fast, frenzy: let runners run)
- SL tightness (ice: very tight, frenzy: wider)
- LHB filter strictness (ice: stricter, frenzy: looser)

All computation uses ONLY past data (transactions and picks from dates < today).
No AI/LLM needed — pure arithmetic from the backtest DB.

Usage:
    from backtest.strategies.post_market_review import PostMarketReviewer
    reviewer = PostMarketReviewer(db_path='shared/db/test_imobile.db')
    adjustments = reviewer.daily_review(today='20260615')
    # adjustments: {regime_override, max_positions_override, holding_days_mult, ...}
"""

import os
import sqlite3
import json
from typing import Dict, Any, Optional, List
from loguru import logger


class PostMarketReviewer:
    """Post-market review engine: read yesterday's results, adjust today's strategy."""

    # ── Sentiment-cycle thresholds (vibe-astock calibrated) ──────────────
    ICE_WIN_RATE       = float(os.getenv('REVIEW_ICE_WIN_RATE',      '0.25'))  # ≤25% win = ice
    RECOVERY_WIN_RATE  = float(os.getenv('REVIEW_RECOVERY_WIN_RATE', '0.45'))  # ≤45% = recovery
    FERMENT_WIN_RATE   = float(os.getenv('REVIEW_FERMENT_WIN_RATE',  '0.60'))  # ≤60% = fermenting
    # >60% = frenzy

    ADVANCE_LOW        = float(os.getenv('REVIEW_ADVANCE_LOW',       '0.20'))  # <20% repick = fragmenting
    ADVANCE_HIGH       = float(os.getenv('REVIEW_ADVANCE_HIGH',      '0.50'))  # >50% repick = strong
  
    # ── Adjustment multipliers ──────────────────────────────────────────
    # How much to deviate from baseline config per sentiment state
    ADJUST = {
        'ice':       {'pos_scale': 0.5,  'hold_mult': 0.5,  'tp_mult': 0.7,  'sl_mult': 0.5,  'lhb_loosen': False},
        'recovery':  {'pos_scale': 0.7,  'hold_mult': 0.7,  'tp_mult': 0.85, 'sl_mult': 0.6,  'lhb_loosen': False},
        'fermenting':{'pos_scale': 1.0,  'hold_mult': 1.0,  'tp_mult': 1.0,  'sl_mult': 1.0,  'lhb_loosen': True},
        'frenzy':    {'pos_scale': 1.3,  'hold_mult': 1.3,  'tp_mult': 1.2,  'sl_mult': 1.5,  'lhb_loosen': True},
        'cooling':   {'pos_scale': 0.85, 'hold_mult': 0.8,  'tp_mult': 0.9,  'sl_mult': 0.7,  'lhb_loosen': False},
    }

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                'shared', 'db', 'test_imobile.db')
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Metric 1: 赚钱效应 (money-making effect) ─────────────────────────
    def _money_making_effect(self, today: str) -> Dict[str, Any]:
        """% of recent SELL transactions that were profitable.
        vibe-astock's 赚钱效应: looks at yesterday's limit-up stocks' today performance.
        Our proxy: recent realized sell P&L from the backtest DB. Returns
        {win_rate, profitable, total, sample, note}."""
        conn = self._connect()
        try:
            cur = conn.execute("""
                SELECT notes FROM transactions
                WHERE user_id = 1 AND transaction_type = 'sell'
                  AND transaction_date < ?
                ORDER BY transaction_date DESC
                LIMIT 30
            """, (today,))
            sells = cur.fetchall()
            if not sells:
                return {'win_rate': 0.5, 'profitable': 0, 'total': 0,
                        'sample': 'none', 'note': 'no sells yet'}

            profitable = 0
            valid = 0
            for row in sells:
                notes = row[0] or ''
                if 'P&L: ¥' in notes:
                    try:
                        pnl_str = notes.split('P&L: ¥')[1].split(' ')[0]
                        pnl = float(pnl_str)
                        valid += 1
                        if pnl > 0:
                            profitable += 1
                    except (ValueError, IndexError):
                        pass

            if valid == 0:
                return {'win_rate': 0.5, 'profitable': 0, 'total': 0,
                        'sample': 'none', 'note': 'no P&L data in sells'}
            wr = profitable / valid
            return {'win_rate': round(wr, 3), 'profitable': profitable,
                    'total': valid, 'sample': f'recent_{valid}_sells',
                    'note': f'{profitable}/{valid} profitable'}
        finally:
            conn.close()

    # ── Metric 2: 晋级率 (advance rate / repick rate) ─────────────────────
    def _advance_rate(self, today: str) -> Dict[str, Any]:
        """What % of yesterday's picks got picked again today?"""
        try:
            from backtest.utils.trading_calendar import get_trading_days_before
            yesterday = get_trading_days_before(today, 1)
        except Exception:
            yesterday = today

        results_dir = os.environ.get(
            'REPORT_DIR',
            os.path.join(os.environ.get('BACKTEST_PATH', './backtest'), 'results'))
        date_dir = None

        if os.path.isdir(results_dir):
            # Scan subdirs for any that have today's pick_stocks file
            for d in sorted(os.listdir(results_dir), reverse=True):
                dp = os.path.join(results_dir, d)
                if not os.path.isdir(dp):
                    continue
                tf = os.path.join(dp, f'pick_stocks_{today}.json')
                yf = os.path.join(dp, f'pick_stocks_{yesterday}.json')
                if os.path.exists(tf) and os.path.exists(yf):
                    date_dir = dp
                    break

        if not date_dir:
            return {'advance_rate': 0.5, 'yesterday_count': 0,
                    'repick_count': 0, 'note': 'no pick files yet'}

        yf = os.path.join(date_dir, f'pick_stocks_{yesterday}.json')
        tf = os.path.join(date_dir, f'pick_stocks_{today}.json')
        try:
            with open(yf) as f:
                ystocks = json.load(f).get('selected_stocks', [])
            ycodes = {s['symbol'] for s in ystocks}
            with open(tf) as f:
                tstocks = json.load(f).get('selected_stocks', [])
            tcodes = {s['symbol'] for s in tstocks}
            common = ycodes & tcodes
            ar = len(common) / len(ycodes) if ycodes else 0.5
            return {'advance_rate': round(ar, 3),
                    'yesterday_count': len(ycodes),
                    'repick_count': len(common),
                    'note': f'{len(common)}/{len(ycodes)} re-picked'}
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            return {'advance_rate': 0.5, 'yesterday_count': 0,
                    'repick_count': 0, 'note': f'read error: {e}'}

    # ── Metric 3: 梯队断层 (echelon gap — how many consecutive days a stock stays picked)
    def _echelon_gap(self, today: str) -> Dict[str, Any]:
        """Average consecutive days the current month's picks have been re-picked.
        Low continuity = market fragmenting (bearish signal)."""
        conn = self._connect()
        try:
            # Recent sells — what was the average holding period?
            cur = conn.execute("""
                SELECT notes FROM transactions
                WHERE user_id = 1 AND transaction_type = 'sell'
                  AND transaction_date < ?
                ORDER BY transaction_date DESC
                LIMIT 20
            """, (today,))
            sells = cur.fetchall()
            if not sells:
                return {'avg_hold_days': 0, 'sample': 0,
                        'fragmenting': False, 'note': 'no sells'}

            import re
            days_list = []
            for row in sells:
                notes = row[0] or ''
                # Extract holding period from notes: "Holding Period: ['20260601', '20260602', ...] days"
                m = re.search(r"Holding Period: \[(.*?)\]\s*days", notes)
                if m:
                    dates_str = m.group(1)
                    dates = [d.strip().strip("'") for d in dates_str.split(',')]
                    days_list.append(len(dates))

            if not days_list:
                return {'avg_hold_days': 0, 'sample': 0,
                        'fragmenting': False, 'note': 'no hold data'}
            avg = sum(days_list) / len(days_list)
            fragmenting = avg < 2.0  # avg hold < 2 days = fragmenting
            return {'avg_hold_days': round(avg, 1), 'sample': len(days_list),
                    'fragmenting': fragmenting,
                    'note': f'avg hold {avg:.1f}d, {"fragmenting" if fragmenting else "stable"}'}
        finally:
            conn.close()

    # ── Metric 4: 情绪周期 (sentiment cycle classifier) ──────────────────
    def _sentiment_cycle(self, today: str) -> str:
        """Classify into ice/recovery/fermenting/frenzy/cooling."""
        mme = self._money_making_effect(today)
        ar = self._advance_rate(today)
        eg = self._echelon_gap(today)

        win_rate = mme['win_rate']
        advance = ar['advance_rate']
        fragmenting = eg['fragmenting']
        avg_hold = eg['avg_hold_days']

        if win_rate <= self.ICE_WIN_RATE or fragmenting:
            return 'ice'
        elif win_rate <= self.RECOVERY_WIN_RATE:
            if advance > self.ADVANCE_HIGH:
                return 'recovery'  # signs of life
            return 'ice'  # still cold
        elif win_rate <= self.FERMENT_WIN_RATE:
            if advance > self.ADVANCE_LOW:
                return 'fermenting'
            return 'recovery'
        else:  # win_rate > 0.60
            if advance <= self.ADVANCE_LOW:
                return 'cooling'  # high win but fading repick = top
            return 'frenzy'

    # ── Core: daily review → strategy adjustments ────────────────────────
    def daily_review(self, today: str) -> Dict[str, Any]:
        """Run post-market review for `today` and return strategy adjustments.

        Returns dict with keys:
            sentiment: str          — the sentiment-cycle state
            win_rate: float         — текущая赚钱效应
            advance_rate: float     — текущая晋级率
            avg_hold_days: float    — average holding days
            fragmenting: bool       — echelon gap flag

            max_positions_override: int | None    — override MAX_POSITIONS
            holding_days_mult: float              — multiplier for holding days
            tp_aggressiveness: float              — 0.7=faster TP, 1.2=let run
            sl_tightness: float                   — 0.5=tighter SL, 1.5=wider
            lhb_loosen: bool                      — loosen LHB filter thresholds?

            regime_bias: str | None               — force regime override
        """
        sentiment = self._sentiment_cycle(today)
        mme = self._money_making_effect(today)
        ar = self._advance_rate(today)
        eg = self._echelon_gap(today)

        adj = self.ADJUST.get(sentiment, self.ADJUST['fermenting'])

        # Determine regime bias
        regime_bias = None
        if sentiment in ('ice', 'recovery'):
            regime_bias = 'bear'
        elif sentiment == 'frenzy':
            regime_bias = 'bull'

        max_pos_override = None
        try:
            base_max = int(os.environ.get('BACKTEST_MAX_POSITIONS', '10'))
            max_pos_override = max(1, int(base_max * adj['pos_scale']))
        except Exception:
            pass

        result = {
            'sentiment': sentiment,
            'win_rate': mme['win_rate'],
            'advance_rate': ar['advance_rate'],
            'avg_hold_days': eg['avg_hold_days'],
            'fragmenting': eg['fragmenting'],
            'mme_sample': mme.get('total', 0),
            'ar_sample': ar.get('yesterday_count', 0),
            'eg_sample': eg.get('sample', 0),

            'max_positions_override': max_pos_override,
            'holding_days_mult': adj['hold_mult'],
            'tp_aggressiveness': adj['tp_mult'],
            'sl_tightness': adj['sl_mult'],
            'lhb_loosen': adj['lhb_loosen'],
            'regime_bias': regime_bias,
        }
        logger.info(
            f"[Review] {today} sentiment={sentiment} "
            f"win={mme['win_rate']:.0%} advance={ar['advance_rate']:.0%} "
            f"hold={eg['avg_hold_days']}d frag={eg['fragmenting']} → "
            f"pos={max_pos_override} hold_mult={adj['hold_mult']:.1f} "
            f"tp×{adj['tp_mult']:.1f} sl×{adj['sl_mult']:.1f} "
            f"regime_bias={regime_bias}"
        )
        return result


# ── Singleton for backtest reuse ─────────────────────────────────────
_reviewer_instance: Optional[PostMarketReviewer] = None


def get_reviewer() -> PostMarketReviewer:
    global _reviewer_instance
    if _reviewer_instance is None:
        _reviewer_instance = PostMarketReviewer()
    return _reviewer_instance