"""MAX_POS_<REGIME> must reach BOTH the engine's limit and the CLI's sizing.

The engine logged `[date] Dynamic position limit: N positions (Regime: ...)` from
`MAX_POS_BULL/NORMAL/VOLATILE/BEAR`, while `cli.py` (which computes
`per_slot_cash = sizing_base / max_positions`) kept its own hardcoded map 12/10/8/5.
So a run with `MAX_POS_BULL=4` logged "4 positions" and still bought 600k/12 ≈ 50k
per name — the knob was inert, and a concentration arm could not be run at all.

These tests are source-level on purpose: both maps live in 3000-line modules that
can only be exercised with a live data stack, and the failure mode being guarded
is exactly "the two maps drifted apart".
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGIMES = ("BULL", "NORMAL", "VOLATILE", "BEAR")
DEFAULTS = {"BULL": "12", "NORMAL": "10", "VOLATILE": "8", "BEAR": "5"}


def _map(text: str) -> dict[str, str]:
    """{REGIME: default} from `int(os.getenv('MAX_POS_<REGIME>', '<default>'))`."""
    found = dict(re.findall(r"MAX_POS_([A-Z]+)'\s*,\s*'(\d+)'", text))
    return found


def _cli_source() -> str:
    return (REPO / "backtest" / "cli.py").read_text(encoding="utf-8")


def _engine_source() -> str:
    return (REPO / "backtest" / "engine.py").read_text(encoding="utf-8")


def test_cli_sizing_reads_the_env_knobs():
    m = _map(_cli_source())
    assert set(m) >= set(REGIMES), f"cli.py no longer reads MAX_POS_* for every regime: {m}"


def test_cli_and_engine_defaults_agree():
    cli, engine = _map(_cli_source()), _map(_engine_source())
    assert cli == DEFAULTS, f"cli defaults drifted: {cli}"
    assert engine == DEFAULTS, f"engine defaults drifted: {engine}"


def test_per_slot_cash_divides_by_the_resolved_limit():
    """The arithmetic the two maps feed: 600k over 4 slots is 150k, not 50k."""
    src = _cli_source()
    assert "per_slot_cash = sizing_base / slots" in src
    assert "slots = max(1, int(max_positions or 1))" in src
    # a 25%-of-base guard still caps concentration
    assert "max_position_cash = sizing_base * 0.25" in src
