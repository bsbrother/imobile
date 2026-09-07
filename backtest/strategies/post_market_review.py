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
    ICE_WIN_RATE       = float(os.getenv('REVIEW_ICE_WIN_RATE',      '0.25'))
    RECOVERY_WIN_RATE  = float(os.getenv('REVIEW_RECOVERY_WIN_RATE', '0.45'))
    FERMENT_WIN_RATE   = float(os.getenv('REVIEW_FERMENT_WIN_RATE',  '0.60'))

    ADVANCE_LOW        = float(os.getenv('REVIEW_ADVANCE_LOW',       '0.20'))
    ADVANCE_HIGH       = float(os.getenv('REVIEW_ADVANCE_HIGH',      '0.50'))
  
    # ── Adjustment multipliers ──────────────────────────────────────────
    ADJUST = {
        'ice':       {'pos_scale': 0.5,  'hold_mult': 0.5,  'tp_mult': 0.7,  'sl_mult': 0.5,  'lhb_loosen': False},
        'recovery':  {'pos_scale': 0.7,  'hold_mult': 0.7,  'tp_mult': 0.85, 'sl_mult': 0.6,  'lhb_loosen': False},
        'fermenting':{'pos_scale': 1.0,  'hold_mult': 1.0,  'tp_mult': 1.0,  'sl_mult': 1.0,  'lhb_loosen': True},
        'frenzy':    {'pos_scale': 1.3,  'hold_mult': 1.3,  'tp_mult': 1.2,  'sl_mult': 1.5,  'lhb_loosen': True},
        'cooling':   {'pos_scale': 0.85, 'hold_mult': 0.8,  'tp_mult': 0.9,  'sl_mult': 0.7,  'lhb_loosen': False},
    }

    # ── Vibe-astock: sentiment persistence → escalation ──────────────────
    # Day 1-2 of ice: pos×0.5 as usual. Day 3-4: pos×0.3. Day 5+: STOP.
    ICE_ESCALATION = {1: 0.5, 2: 0.5, 3: 0.3, 4: 0.3}
    ICE_STOP_DAY = 5  # day 5+ of continuous ice → 0 positions

    # ── Vibe-astock: drawdown velocity emergency stop ────────────────────
    # If single-day realized-loss exceeds this % of current NAV, force 0
    # positions the NEXT trading day regardless of sentiment state.
    DD_VELOCITY_THRESHOLD = float(os.getenv('REVIEW_DD_VELOCITY', '-0.008'))  # -0.8%

    # ── Vibe-astock: recovery confirmation gating ────────────────────────
    # After an ice event, require N consecutive recovery-sentiment days
    # before restoring positions above 50%.
    RECOVERY_CONFIRM_DAYS = int(os.getenv('REVIEW_RECOVERY_CONFIRM', '2'))

    # ── State (persists across daily_review calls within a backtest) ─────
    _sentiment_history = []          # [(date, sentiment, win_rate, advance, daily_pnl)]
    _ice_day_counter = 0
    _recovery_day_counter = 0
    _last_nav = 0.0

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

        # Use the exact run directory set by engine.py (REVIEW_RESULTS_DIR).
        # Fall back to scanning only the most-recently-modified subdir (not
        # every subdir — that can silently pick up a different run's files
        # and cause non-reproducible sentiment drift).
        results_dir = os.environ.get('REVIEW_RESULTS_DIR')
        if not results_dir or not os.path.isdir(results_dir):
            base = os.path.join(os.environ.get('BACKTEST_PATH', './backtest'), 'results')
            if os.path.isdir(base):
                subdirs = sorted(
                    [os.path.join(base, d) for d in os.listdir(base)],
                    key=lambda p: os.path.getmtime(p) if os.path.isdir(p) else 0,
                    reverse=True)
                results_dir = subdirs[0] if subdirs else None
            if not results_dir:
                return {'advance_rate': 0.5, 'yesterday_count': 0,
                        'repick_count': 0, 'note': 'no results dir'}

        yf = os.path.join(results_dir, f'pick_stocks_{yesterday}.json')
        tf = os.path.join(results_dir, f'pick_stocks_{today}.json')
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
        """Classify into ice/recovery/fermenting/frenzy/cooling.

        Vibe-astock 情绪周期 — 5 states with verification-condition gates.
        """
        mme = self._money_making_effect(today)
        ar = self._advance_rate(today)
        eg = self._echelon_gap(today)

        win_rate = mme['win_rate']
        advance = ar['advance_rate']
        fragmenting = eg['fragmenting']
        avg_hold = eg['avg_hold_days']

        # ── 0. Sentiment velocity (win_rate rate-of-change) ────────────────
        # vibe-astock: 情绪曲线 — not just current state, but direction.
        # If win_rate is dropping fast, downgrade BEFORE rolling window catches up.
        prev_wins = [w for _, _, w, _, _ in self._sentiment_history[-3:] if w > 0]
        win_rate_dropping = False
        if len(prev_wins) >= 2 and prev_wins[-1] < 0.6:
            # Check if win_rate is falling >40% from peak in last 3 readings
            peak = max(prev_wins)
            if peak > 0 and (peak - win_rate) / peak > 0.40:
                win_rate_dropping = True

        # ── 1. Advance-rate crash detector ─────────────────────────────────
        # vibe-astock: 晋级率断崖 — when repick rate suddenly collapses
        prev_ar_vals = [a for _, _, _, a, _ in self._sentiment_history[-3:] if a > 0]
        ar_crash = False
        if len(prev_ar_vals) >= 2 and prev_ar_vals[-2] > 0:
            ar_drop = (prev_ar_vals[-2] - advance) / prev_ar_vals[-2]
            # Crash: >50% relative drop, OR absolute below 0.20
            if ar_drop > 0.5 or (advance < self.ADVANCE_LOW and ar_drop > 0.3):
                ar_crash = True

        # ── 2. Base classification ────────────────────────────────────────
        if win_rate <= self.ICE_WIN_RATE or fragmenting:
            base = 'ice'
        elif win_rate <= self.RECOVERY_WIN_RATE:
            if advance > self.ADVANCE_HIGH:
                base = 'recovery'
            else:
                base = 'ice'
        elif win_rate <= self.FERMENT_WIN_RATE:
            if advance > self.ADVANCE_LOW:
                base = 'fermenting'
            else:
                base = 'recovery'
        else:  # win_rate > 0.60
            if advance <= self.ADVANCE_LOW:
                base = 'cooling'
            else:
                base = 'frenzy'

        # ── 3. Verification-condition downgrade ────────────────────────────
        # vibe-astock: 明日验证条件 — "I predicted frenzy/recovery, market
        # failed to confirm → downgrade now, don't wait for rolling window."
        if base in ('frenzy', 'fermenting') and self._sentiment_history:
            prev_sentiment = self._sentiment_history[-1][1]
            # Was in a strong state but indicators collapsed?
            if prev_sentiment in ('frenzy', 'fermenting', 'cooling'):
                if ar_crash:
                    # 晋级率断崖 = 连板梯队断层 — immediate downgrade
                    logger.info(f"[Sentiment] advance crash {prev_ar_vals[-2]:.0%}->{advance:.0%}: {base}->cooling")
                    return 'cooling'
                if win_rate_dropping and advance <= self.ADVANCE_LOW:
                    logger.info(f"[Sentiment] win_rate velocity drop + low advance: {base}->recovery")
                    return 'recovery'

        return base

    # ── Core: daily review → strategy adjustments ────────────────────────
    def daily_review(self, today: str) -> Dict[str, Any]:
        """Run post-market review for `today` and return strategy adjustments.

        Vibe-astock enhancements over V1:
        1. Sentiment persistence tracking (情绪周期第几天)
        2. Drawdown velocity emergency stop (单日回撤速度)
        3. Recovery confirmation gating (修复确认门禁)
        """
        sentiment = self._sentiment_cycle(today)
        mme = self._money_making_effect(today)
        ar = self._advance_rate(today)
        eg = self._echelon_gap(today)
        daily_pnl = self._daily_realized_pnl(today)

        # ── 1. Sentiment persistence tracking ──────────────────────────
        was_ice = self._ice_day_counter
        if sentiment == 'ice':
            self._ice_day_counter += 1
            self._recovery_day_counter = 0
        elif sentiment == 'recovery':
            self._ice_day_counter = 0
            self._recovery_day_counter += 1
        else:
            self._ice_day_counter = 0
            self._recovery_day_counter = 0
        
        self._sentiment_history.append((today, sentiment, mme['win_rate'], ar['advance_rate'], daily_pnl))
        if len(self._sentiment_history) > 20:
            self._sentiment_history = self._sentiment_history[-20:]

        # ── 2. Drawdown velocity emergency stop ──────────────────────────
        # vibe-astock: 判断 vs 执行归因 — separate what was analysis from
        # what was execution. Here: if yesterday's P&L was a sharp loss, the
        # market is moving against us faster than sentiment can adjust.
        dd_emergency = False
        current_nav = self._compute_nav()
        if self._last_nav > 0 and daily_pnl < 0:
            dd_pct = daily_pnl / self._last_nav
            if dd_pct < self.DD_VELOCITY_THRESHOLD:
                dd_emergency = True
        self._last_nav = current_nav

        # ── 3. ICE escalation ────────────────────────────────────────────
        ice_day = self._ice_day_counter
        adj = self.ADJUST['fermenting'].copy()  # default, all paths overwrite
        sentiment_effective = sentiment  # default, overwritten below
        if ice_day >= self.ICE_STOP_DAY:
            # vibe-astock: sustained ice day 5+ → full STOP
            pos_scale = 0.0
            adj = self.ADJUST['ice'].copy()
            sentiment_effective = f'ice_d{ice_day}_STOP'
        elif ice_day >= 3:
            pos_scale = 0.3
            adj = self.ADJUST['ice'].copy()
            sentiment_effective = f'ice_d{ice_day}'
        elif ice_day >= 1:
            pos_scale = 0.5
            adj = self.ADJUST['ice'].copy()
        else:
            # Non-ice: check recovery gating
            need_recovery_confirm = (was_ice >= 2 and self._recovery_day_counter < self.RECOVERY_CONFIRM_DAYS)
            if need_recovery_confirm:
                # vibe-astock: after ice, require N recovery days before full resume
                pos_cap = 0.5 if self._recovery_day_counter == 1 else 0.7
                adj = self.ADJUST.get(sentiment, self.ADJUST['fermenting']).copy()
                if adj['pos_scale'] > pos_cap:
                    adj['pos_scale'] = pos_cap
                sentiment_effective = f'{sentiment}_rc{self._recovery_day_counter}'
            else:
                adj = self.ADJUST.get(sentiment, self.ADJUST['fermenting']).copy()
                sentiment_effective = sentiment
            pos_scale = adj['pos_scale']

        # ── 4. Drawdown velocity override ─────────────────────────────────
        if dd_emergency:
            # vibe-astock: 亏钱效应急速恶化 → 强制空仓
            pos_scale = 0.0
            if sentiment_effective != 'ice_d5_STOP':
                sentiment_effective = f'{sentiment_effective}_DDVEL'
            adj = self.ADJUST.get('ice', self.ADJUST['ice']).copy()
            adj['pos_scale'] = 0.0

        # ── 5. Build adjustments ─────────────────────────────────────────
        regime_bias = None
        if sentiment in ('ice', 'recovery') or ice_day >= 2:
            regime_bias = 'bear'
        elif sentiment == 'frenzy':
            regime_bias = 'bull'

        max_pos_override = None
        try:
            base_max = int(os.environ.get('BACKTEST_MAX_POSITIONS', '10'))
            
            # ── Regime-aware cap (vibe-astock: 短线情绪不能凌驾于大盘) ──
            # The engine's detect_market_regime already says 'bear' when the
            # index is below MA60/MA120. Sentiment can be 'fermenting' while
            # regime is bear (backward-looking 30-sell win_rate lags 3-5 days).
            # When regime != sentiment, trust regime first.
            try:
                from backtest.engine import _detect_market_regime_cached
                reg = _detect_market_regime_cached(today).get('regime', 'normal')
            except Exception:
                reg = 'normal'
            
            if reg == 'bear' and sentiment not in ('ice',):
                # Regime says bear but sentiment hasn't caught up → cap to 3
                regime_cap = 3
                pos_from_scale = int(base_max * pos_scale)
                capped_pos = min(pos_from_scale, regime_cap)
                if capped_pos < pos_from_scale:
                    logger.info(f"[Review] regime_bear cap: {pos_from_scale}→{capped_pos} pos (sentiment={sentiment})")
                    max_pos_override = max(0, capped_pos)
                    sentiment_effective = f'{sentiment_effective}_BEAR_CAP'
                else:
                    max_pos_override = max(0, int(base_max * pos_scale))
            else:
                max_pos_override = max(0, int(base_max * pos_scale))
        except Exception:
            pass

        # ── 2b. Consecutive loss detection (vibe-astock: 连亏降档) ──
        # If the last 2 trading days both had realized losses, cut to 50%
        # positions regardless of sentiment. This catches the July pattern:
        # Jul 1 +13K, Jul 2 -10K, Jul 3 -7.6K. After Jul 2's loss, Jul 3
        # would have been at 50% positions, halving the -7.6K loss.
        recent_2_sells = [p for _, _, _, _, p in self._sentiment_history[-2:] if p < 0]
        if len(recent_2_sells) >= 2 and max_pos_override and max_pos_override > 0:
            capped = max(0, int(max_pos_override * 0.5))
            if capped < max_pos_override:
                logger.info(f"[Review] CONSEC2: 2 consecutive losses, cutting pos {max_pos_override} -> {capped}")
                max_pos_override = capped
                sentiment_effective = f'{sentiment_effective}_CONSEC2'

        # Rolling 5-day P&L for trend context
        recent_5 = [p for _, _, _, _, p in self._sentiment_history[-5:]]
        rolling_5d_pnl = sum(recent_5) if recent_5 else 0

        result = {
            'sentiment': sentiment_effective,
            'sentiment_raw': sentiment,
            'win_rate': mme['win_rate'],
            'advance_rate': ar['advance_rate'],
            'avg_hold_days': eg['avg_hold_days'],
            'fragmenting': eg['fragmenting'],
            'ice_day': ice_day,
            'recovery_day': self._recovery_day_counter,
            'dd_emergency': dd_emergency,
            'daily_pnl': daily_pnl,
            'rolling_5d_pnl': rolling_5d_pnl,

            'max_positions_override': max_pos_override,
            'holding_days_mult': adj['hold_mult'],
            'tp_aggressiveness': adj['tp_mult'],
            'sl_tightness': adj['sl_mult'],
            'lhb_loosen': adj['lhb_loosen'],
            'regime_bias': regime_bias,
        }
        logger.info(
            f"[Review] {today} raw={sentiment} eff={sentiment_effective} "
            f"win={mme['win_rate']:.0%} adv={ar['advance_rate']:.0%} "
            f"ice_d{ice_day} rec_d{self._recovery_day_counter} "
            f"dd_emerg={dd_emergency} pnl={daily_pnl:+,.0f} "
            f"5d={rolling_5d_pnl:+,.0f} → pos={max_pos_override} "
            f"hold×{adj['hold_mult']:.1f} sl×{adj['sl_mult']:.1f}"
        )
        return result

    # ── Helper: daily realized P&L from DB ─────────────────────────────
    def _daily_realized_pnl(self, today: str) -> float:
        """Read realized P&L from the MOST RECENT trading day with sells.
        
        In backtest, the review runs BEFORE today's trading, so
        transaction_date=today always returns 0. The fix: read the
        most recent prior trading day's sells to detect drawdown velocity.
        """
        conn = self._connect()
        try:
            cur = conn.execute("""
                SELECT notes, transaction_date FROM transactions
                WHERE user_id = 1 AND transaction_type = 'sell'
                  AND transaction_date < ?
                ORDER BY transaction_date DESC
                LIMIT 50
            """, (today,))
            sells = cur.fetchall()
            if not sells:
                return 0.0
            
            # Group by the most recent date that has sells
            latest_date = sells[0][1]
            total = 0.0
            for row in sells:
                if row[1] != latest_date:
                    break  # only the most recent trading day
                notes = row[0] or ''
                if 'P&L: ¥' in notes:
                    try:
                        pnl = float(notes.split('P&L: ¥')[1].split(' ')[0])
                        total += pnl
                    except (ValueError, IndexError):
                        pass
            return total
        finally:
            conn.close()

    # ── Helper: compute current NAV ────────────────────────────────────
    def _compute_nav(self) -> float:
        initial = float(os.environ.get('INITIAL_CASH', '600000'))
        conn = self._connect()
        try:
            cur = conn.execute("""
                SELECT notes FROM transactions
                WHERE user_id = 1 AND transaction_type = 'sell'
            """)
            total_pnl = 0.0
            for row in cur.fetchall():
                notes = row[0] or ''
                if 'P&L: ¥' in notes:
                    try:
                        total_pnl += float(notes.split('P&L: ¥')[1].split(' ')[0])
                    except (ValueError, IndexError):
                        pass
            return initial + total_pnl
        finally:
            conn.close()


# ── Singleton for backtest reuse ─────────────────────────────────────
_reviewer_instance: Optional[PostMarketReviewer] = None


def get_reviewer() -> PostMarketReviewer:
    global _reviewer_instance
    if _reviewer_instance is None:
        _reviewer_instance = PostMarketReviewer()
    return _reviewer_instance