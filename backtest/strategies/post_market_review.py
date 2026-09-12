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
    DD_VELOCITY_THRESHOLD = float(os.getenv('REVIEW_DD_VELOCITY', '-0.02'))  # -2%

    # ── Vibe-astock: recovery confirmation gating ────────────────────────
    # After an ice event, require N consecutive recovery-sentiment days
    # before restoring positions above 50%.
    RECOVERY_CONFIRM_DAYS = int(os.getenv('REVIEW_RECOVERY_CONFIRM', '2'))

    # ── State (persists across daily_review calls within a backtest) ─────
    _sentiment_history = []          # [(date, sentiment, win_rate, daily_pnl)]
    _ice_day_counter = 0             # consecutive ice days (reset on non-ice)
    _recovery_day_counter = 0        # consecutive recovery days after ice
    _last_nav = 0.0                  # previous day's portfolio NAV

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

    @staticmethod
    def _db_date(yyyymmdd: str) -> str:
        """Convert YYYYMMDD to the DB's stored date format YYYY-MM-DDT00:00:00.

        The engine writes transactions with ISO timestamps. Querying with a bare
        '20260702' matched 0 rows (string compare: 'T' > '0' makes every stored
        date look LATER than the target), so every metric silently fell back to
        its neutral default: daily_pnl always 0 (dd-velocity stop never fired),
        win_rate always 0.5 (sentiment pinned to fermenting), hold-days 0.

        `REVIEW_DB_FIX` (default **false**) gates this fix. It is CORRECT, but
        enabling it switches the review layer from effectively-inert to fully
        live, which activates the ICE escalation / position cuts. Measured on
        20260101-20260831 (same entry-side config): 134.98% disabled vs 129.47%
        enabled -> the fix costs -5.51pp on this momentum strategy because the
        ICE thresholds (win_rate <= 0.25/0.45/0.60) fire often in strong months.
        Enable it only after recalibrating ICE for a momentum book.
        """
        if os.getenv('REVIEW_DB_FIX', 'false').lower() in ('false', '0', 'no'):
            return str(yyyymmdd)
        s = str(yyyymmdd)
        if '-' in s:
            return s
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}T00:00:00"

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
            """, (self._db_date(today),))
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
            """, (self._db_date(today),))
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
        
        self._sentiment_history.append((today, sentiment, mme['win_rate'], daily_pnl))
        if len(self._sentiment_history) > 20:
            self._sentiment_history = self._sentiment_history[-20:]

        # ── 2. Drawdown velocity emergency stop ──────────────────────────
        # vibe-astock: 判断 vs 执行归因 — separate what was analysis from
        # what was execution. Here: if today's P&L was a sharp loss, the
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

        # ── 4b. LEVER 5: first-losing-day de-risk (env REVIEW_FIRST_DOWN_POS) ──
        # React on DAY 1 of a losing streak instead of waiting for ice-day >= 3.
        # The -2% NAV velocity stop above already fires on day 1 of a BIG loss;
        # this adds a *smaller* first-day throttle (e.g. 0.5 = half size) for the
        # ordinary first red day. Unset/'' keeps baseline behaviour.
        # NOTE: prior attempts in this family that cut size on losing days cost
        # ~10pp of total return (see over-optimization-one-bad-month) — this is
        # deliberately opt-in and must be A/B measured before being made default.
        _fdp_raw = os.getenv('REVIEW_FIRST_DOWN_POS', '').strip()
        if _fdp_raw and daily_pnl < 0 and not dd_emergency:
            try:
                _fdp = float(_fdp_raw)
                if pos_scale > _fdp:
                    pos_scale = _fdp
                    sentiment_effective = f'{sentiment_effective}_D1CAP'
                    adj = dict(adj)
                    adj['pos_scale'] = _fdp
                    logger.info(
                        f"[Review] {today} LEVER5 first-down-day cap -> pos_scale={_fdp:.2f} "
                        f"(pnl={daily_pnl:+,.0f})"
                    )
            except (TypeError, ValueError):
                pass

        # ── 5. Build adjustments ─────────────────────────────────────────
        regime_bias = None
        if sentiment in ('ice', 'recovery') or ice_day >= 2:
            regime_bias = 'bear'
        elif sentiment == 'frenzy':
            regime_bias = 'bull'

        max_pos_override = None
        try:
            base_max = int(os.environ.get('BACKTEST_MAX_POSITIONS', '10'))
            max_pos_override = max(0, int(base_max * pos_scale))  # 0 = STOP
        except Exception:
            pass

        # Rolling 5-day P&L for trend context
        recent_5 = [p for _, _, _, p in self._sentiment_history[-5:]]
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
        """Realized P&L of the most recent trading day STRICTLY BEFORE `today`.

        Timing note: the review for day D runs inside the strategy subprocess,
        i.e. BEFORE the engine books day D's own sells later in its day-D loop.
        So `transaction_date = D` can never match here — not just because of the
        date format (also fixed via _db_date), but by construction. Reading the
        previous trading day's booked sells gives the dd-velocity signal its
        intended input while staying lookahead-free (the engine applies the
        resulting position override on day D).
        """
        conn = self._connect()
        try:
            if os.getenv('REVIEW_DB_FIX', 'false').lower() in ('false', '0', 'no'):
                # Legacy behaviour: query the SAME day, which (because the review
                # runs before the engine books day D's trades) always yields 0.
                cur = conn.execute("""
                    SELECT notes FROM transactions
                    WHERE user_id = 1 AND transaction_type = 'sell'
                      AND transaction_date = ?
                """, (today,))
                total = 0.0
                for r in cur.fetchall():
                    notes = r[0] or ''
                    if 'P&L: ¥' in notes:
                        try:
                            total += float(notes.split('P&L: ¥')[1].split(' ')[0])
                        except (ValueError, IndexError):
                            pass
                return total
            cutoff = self._db_date(today)
            row = conn.execute("""
                SELECT MAX(transaction_date) FROM transactions
                WHERE user_id = 1 AND transaction_type = 'sell'
                  AND transaction_date < ?
            """, (cutoff,)).fetchone()
            if not row or not row[0]:
                return 0.0
            cur = conn.execute("""
                SELECT notes FROM transactions
                WHERE user_id = 1 AND transaction_type = 'sell'
                  AND transaction_date = ?
            """, (row[0],))
            total = 0.0
            for r in cur.fetchall():
                notes = r[0] or ''
                if 'P&L: ¥' in notes:
                    try:
                        total += float(notes.split('P&L: ¥')[1].split(' ')[0])
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