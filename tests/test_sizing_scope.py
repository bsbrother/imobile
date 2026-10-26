"""`MAX_POS_*` may be strategy-scoped in `.env`, but must never size a live book.

python-dotenv has no concept of the `# ── <strategy> ──` sections and exports every
line, so `pre_market_run.py` -> `python -m backtest.cli analyze` inherits them. A
two-slot concentration written for a backtest would then divide the real book by two
(25% per position instead of ~8.5%), tripling every order. engine.py marks the
environment when it resolves a section on purpose; a standalone cli run strips the
section-scoped sizing keys instead.
"""
import pytest

from backtest.utils.strategy_env import (
    ENV_APPLIED_MARK,
    SIZING_KEYS,
    parse_env,
    scope_out_section_sizing,
)

SAMPLE = """\
# ── ts_turning_point ──
MAX_POS_BULL=2
MAX_POS_NORMAL=2
TAKE_PROFIT_PCT=0.04
# ── end ──
"""


@pytest.fixture()
def env_file(tmp_path):
    p = tmp_path / '.env'
    p.write_text(SAMPLE, encoding='utf-8')
    return str(p)


def test_section_sizing_is_stripped_without_the_engine_mark(env_file):
    env = {'MAX_POS_BULL': '2', 'MAX_POS_NORMAL': '2'}
    removed = scope_out_section_sizing(env, env_path=env_file)
    assert set(removed) == {'MAX_POS_BULL', 'MAX_POS_NORMAL'}
    assert 'MAX_POS_BULL' not in env and 'MAX_POS_NORMAL' not in env


def test_section_sizing_survives_when_the_engine_resolved_it(env_file):
    env = {ENV_APPLIED_MARK: 'ts_turning_point', 'MAX_POS_BULL': '2', 'MAX_POS_NORMAL': '2'}
    assert scope_out_section_sizing(env, env_path=env_file) == []
    assert env['MAX_POS_BULL'] == '2'


def test_only_sizing_keys_are_touched(env_file):
    """Blast radius check: TAKE_PROFIT_PCT is also section-owned and must survive."""
    env = {'MAX_POS_BULL': '2', 'TAKE_PROFIT_PCT': '0.04'}
    scope_out_section_sizing(env, env_path=env_file)
    assert env['TAKE_PROFIT_PCT'] == '0.04'


def test_a_global_sizing_key_is_never_stripped(tmp_path):
    p = tmp_path / '.env'
    p.write_text('MAX_POS_BULL=6\n# ── ts_turning_point ──\nMAX_POS_NORMAL=2\n# ── end ──\n', encoding='utf-8')
    env = {'MAX_POS_BULL': '6', 'MAX_POS_NORMAL': '2'}
    removed = scope_out_section_sizing(env, env_path=str(p))
    assert removed == ['MAX_POS_NORMAL']
    assert env['MAX_POS_BULL'] == '6', "a global line is an explicit operator choice"


def test_the_real_env_scopes_the_turning_point_concentration():
    """The shipped .env must carry the winning 2-slot config in the section, not globally."""
    sections, globals_ = parse_env()
    turning = sections.get('ts_turning_point', {})
    for key in SIZING_KEYS:
        assert turning.get(key) == '2', f"{key} missing from the ts_turning_point section"
        assert key not in globals_, f"{key} must not be global — it would resize live trading"


def test_flow_section_does_not_own_sizing_keys():
    """Live runs flow_review; its section must not carry a book size either."""
    sections, _ = parse_env()
    assert not (set(sections.get('ts_7AZ_96MA_flow_review', {})) & set(SIZING_KEYS))
