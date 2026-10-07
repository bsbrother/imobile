"""Price-limit (涨跌停) board states, and the fill policy that follows from them.

WHY THIS EXISTS
---------------
A-share daily price limits are hard exchange bounds, and they change what is *executable*:

  * A board **sealed at limit-up** (一字涨停) has buyers queueing and no sellers. A BUY cannot fill
    at the open — there is nothing to buy. A backtest that books "buy at the open" is inventing a
    trade. A board sealed at limit-up is also the worst possible entry price of the day.
  * A board **sealed at limit-down** (一字跌停) has sellers queueing and no buyers. A SELL cannot
    fill — the position is trapped for the session, no matter what the stop says.
  * An **open at limit-up** but not sealed (it trades off the limit later) means the open IS the top
    of the band: paying it is a +10%/+20% entry versus the previous close, far outside any entry
    discount the strategy computed.
  * An **open at limit-down** but not sealed is a crash open: the momentum premise behind the pick
    is void, and the fill is a knife-catch.

The sell half of this already existed as an inline check in `backtest/engine.py`
(`locked_limit_down`); this module makes the rule explicit, symmetric, shared by the backtest and
the live path, and testable without a database.

BANDS
-----
    10%   main board: 000/001/002/003 (SZ), 600/601/603/605 (SH), and anything unrecognised
    20%   ChiNext 300/301, STAR 688/689
    30%   北交所 4xxxxx / 8xxxxx

NOT detectable from a symbol: ST names run a 5% band, and newly listed names have no limit on day
one. Both are treated as the board band. The error direction is conservative for the gap-up case
(the regime open-gap cap still applies) and is called out here rather than silently assumed away.

Pure functions only — no I/O, no database, no clock. The caller supplies the day's OHLC and the
previous close.
"""
from __future__ import annotations

# Board bands, in the order they are tested.
_WIDE_PREFIXES = ("300", "301", "688", "689")     # 创业板 / 科创板  -> 20%
_BSE_PREFIXES = ("4", "8")                        # 北交所           -> 30%
_MAIN_BAND = 0.10
_WIDE_BAND = 0.20
_BSE_BAND = 0.30

_EPS = 1e-6

# Board states
NORMAL = "normal"
UP_OPEN = "up_open"           # opened at the limit-up price, but traded off it
UP_SEALED = "up_sealed"       # traded at the limit-up price all session: no sellers
DOWN_OPEN = "down_open"       # opened at the limit-down price, but traded off it
DOWN_SEALED = "down_sealed"   # traded at the limit-down price all session: no buyers

LIMIT_BOUND_STATES = (UP_OPEN, UP_SEALED, DOWN_OPEN, DOWN_SEALED)


def board_band(symbol: str) -> float:
    """Daily price-limit band for the board a 6-digit code trades on."""
    c = (symbol or "").split(".")[0]
    if c.startswith(_WIDE_PREFIXES):
        return _WIDE_BAND
    if c.startswith(_BSE_PREFIXES):
        return _BSE_BAND
    return _MAIN_BAND


def limit_prices(prev_close: float, symbol: str) -> tuple[float, float]:
    """(limit_down, limit_up) — rounded to the 0.01 tick, as the exchange publishes them."""
    band = board_band(symbol)
    return (round(prev_close * (1 - band), 2), round(prev_close * (1 + band), 2))


def board_state(open_price: float, high: float, low: float, prev_close: float | None,
                symbol: str) -> str:
    """Classify the session. `open_price/high/low` are the day's real printed prices.

    Sealed is decided by the extremes, not the open: a board is sealed when *no trade* happened
    away from the limit, i.e. the low sits at/above limit-up, or the high at/below limit-down.
    """
    if not prev_close or prev_close <= 0:
        return NORMAL
    limit_down, limit_up = limit_prices(prev_close, symbol)

    if low >= limit_up - _EPS:
        return UP_SEALED
    if high <= limit_down + _EPS:
        return DOWN_SEALED
    if open_price >= limit_up - _EPS:
        return UP_OPEN
    if open_price <= limit_down + _EPS:
        return DOWN_OPEN
    return NORMAL


def buy_block_reason(state: str) -> str | None:
    """Why a BUY must not be booked at the open in this state, or None when it may.

    Every non-normal state blocks: at limit-up you cannot buy (or you buy the day's top), at
    limit-down you are catching a crash the pick's premise never contemplated.
    """
    return {
        UP_SEALED: "limit-up sealed: no sellers, the open fill cannot happen",
        UP_OPEN: "opened at limit-up: the open is the top of the band",
        DOWN_SEALED: "limit-down sealed: crash open, momentum premise void",
        DOWN_OPEN: "opened at limit-down: crash open, momentum premise void",
    }.get(state)


def sell_block_reason(state: str) -> str | None:
    """Why an exit must not be booked in this state, or None when it may.

    Only a sealed limit-down traps the position. Note a sealed limit-UP is the opposite case for a
    seller: buyers queue at the limit, so an exit fills immediately and *better* than its trigger —
    the caller should fill at the limit-up price, not block.
    """
    return {
        DOWN_SEALED: "limit-down sealed: no buyers, the exit cannot fill",
    }.get(state)


def board_guard_enabled() -> bool:
    """Single reader for the `LIMIT_BOARD_GUARD` switch (default on).

    `LIMIT_BOARD_GUARD=0` restores the pre-change inline behaviour everywhere, so a run can be A/B'd
    against a pinned result. Every guard in the engine reads it, so there is one place to flip.
    """
    import os
    return os.getenv('LIMIT_BOARD_GUARD', 'true').strip().lower() in ('true', '1', 'yes')


def fill_block_reason(side: str, fill_price: float | None, prev_close: float | None,
                      symbol: str) -> str | None:
    """THE market rule, stated at the price a fill would print at.

    A BUY fill needs a seller AT that price; a SELL fill needs a buyer. At the limit-up there are no
    sellers, at the limit-down no buyers, so a fill printed there is impossible — whatever order,
    trigger or stop asked for it.

    It is a property of the PRICE, not of the session, and that distinction is the whole point:

      * a stop that gaps to a limit-down open cannot fill (its price IS the limit-down);
      * a take-profit that the stock reaches later in the same session CAN fill, even though the
        session opened at the limit-down — the price lifted off the floor first.

    So this is checked against the price each branch would actually book, never against a session
    label. Returns None when the fill may stand.
    """
    if not prev_close or prev_close <= 0 or fill_price is None:
        return None
    limit_down, limit_up = limit_prices(prev_close, symbol)
    band = board_band(symbol)
    if side == 'buy' and fill_price >= limit_up - _EPS:
        return (f"no sellers at the limit-up: fill {fill_price:.2f} >= limit-up {limit_up:.2f} "
                f"(prev close {prev_close:.2f}, {band:.0%} band)")
    if side == 'sell' and fill_price <= limit_down + _EPS:
        return (f"no buyers at the limit-down: fill {fill_price:.2f} <= limit-down {limit_down:.2f} "
                f"(prev close {prev_close:.2f}, {band:.0%} band)")
    return None


# ─── Pre-open gap exit (the 09:25 print) ──────────────────────────────────────
GAP_EXIT_NEAR_PCT_DEFAULT = 0.005


def gap_exit_enabled() -> bool:
    """`GAP_EXIT=0` switches the force-exit rule off; the plain fill rules still apply."""
    import os
    return os.getenv('GAP_EXIT', 'true').strip().lower() in ('true', '1', 'yes')


def gap_exit_near_pct() -> float:
    """How close to the stop an open counts as 'nearly there' (fraction: 0.005 = 0.5%)."""
    import os
    try:
        return max(0.0, float(os.getenv('GAP_EXIT_NEAR_PCT', str(GAP_EXIT_NEAR_PCT_DEFAULT))))
    except (TypeError, ValueError):
        return GAP_EXIT_NEAR_PCT_DEFAULT


def gap_exit_reason(open_price: float | None, stop_loss: float | None, prev_close: float | None,
                    symbol: str, near_pct: float | None = None) -> str | None:
    """Why a held position must be exited AT THE AUCTION PRICE rather than carried, or None.

    The 09:25 print is the first price the day offers. When it already sits at or below the stop,
    the stop's own trigger price is fiction — the fill would happen at the open, or worse. So exit
    there, deliberately, instead of letting a stop that the market gapped past decide it.

    Two cases reach beyond a plain `open <= stop` test:

      * **NEARLY** — the open sits within `near_pct` above the stop. The position is one tick from
        being stopped and the session has not started; what the stop fills at is whatever the day
        offers, which on a bad tape is far below. Exit at the auction price instead.
      * **LIMIT-DOWN** — the open is at/below the band floor. There are no buyers, so the exit is
        requested and *cannot* fill: the caller must book nothing and carry the position (see
        `fill_block_reason`). The reason says which of those it is rather than pretending.
    """
    if open_price is None or open_price <= 0:
        return None
    if prev_close and prev_close > 0:
        limit_down, _ = limit_prices(prev_close, symbol)
        if open_price <= limit_down + _EPS:
            return (f"opened at/below the limit-down {limit_down:.2f} — exit requested but "
                    f"unfillable: no buyers at the floor")
    if not stop_loss or stop_loss <= 0:
        return None
    if open_price <= stop_loss + _EPS:
        return (f"opened {open_price:.2f} at/below the stop-loss {stop_loss:.2f} — exit at the "
                f"auction price")
    near = gap_exit_near_pct() if near_pct is None else near_pct
    if near > 0 and open_price <= stop_loss * (1 + near) + _EPS:
        return (f"opened {open_price:.2f} within {near:.1%} of the stop-loss {stop_loss:.2f} — "
                f"exit at the auction price")
    return None


def is_buy_blocked(state: str) -> bool:
    return buy_block_reason(state) is not None


def is_sell_blocked(state: str) -> bool:
    return sell_block_reason(state) is not None
