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


# ── re-add defence ───────────────────────────────────────────────────────────
# Every `backtest/strategies/*.py` calls load_dotenv() at import, and dotenv re-adds any
# key it does not find in the environment. So a key scoped out at startup comes back the
# moment the engine or a child imports a strategy module.

BOTH = """\
SHARED=true
# ── ts_turning_point ──
MAX_POS_BULL=2
TAKE_PROFIT_PCT=0.04
SHARED=true
# ── end ──
# ── ts_switch_by_regime ──
SHARED=true
EXTRA_ONLY=1
# ── end ──
"""


def test_reenforce_removes_a_key_that_came_back(tmp_path):
    from backtest.utils.strategy_env import reenforce_strategy_env

    p = tmp_path / '.env'
    p.write_text(BOTH, encoding='utf-8')
    env = {'MAX_POS_BULL': '2', 'TAKE_PROFIT_PCT': '0.04'}     # re-added by an import
    touched = reenforce_strategy_env('ts_switch_by_regime', env, env_path=str(p))
    assert env.get('MAX_POS_BULL') is None, "another section's sizing must not survive"
    assert env.get('TAKE_PROFIT_PCT') is None
    assert '-MAX_POS_BULL' in touched


def test_reenforce_does_not_thrash_a_key_both_sections_set(tmp_path):
    """SHARED is in both sections; the target's value must win without churn."""
    from backtest.utils.strategy_env import reenforce_strategy_env

    p = tmp_path / '.env'
    p.write_text(BOTH, encoding='utf-8')
    env = {'SHARED': 'true'}
    touched = reenforce_strategy_env('ts_turning_point', env, env_path=str(p))
    assert not any(k.endswith('SHARED') for k in touched), f"SHARED churned: {touched}"
    assert env['SHARED'] == 'true'


def test_child_drops_what_the_parent_scoped_out(tmp_path):
    """The marker is how a strategy subprocess undoes its own flat load_dotenv()."""
    from backtest.utils.strategy_env import SCOPED_OUT_MARK, drop_scoped_out

    env = {SCOPED_OUT_MARK: 'MAX_POS_BULL,TAKE_PROFIT_PCT', 'MAX_POS_BULL': '2', 'TAKE_PROFIT_PCT': '0.04'}
    assert set(drop_scoped_out(env)) == {'MAX_POS_BULL', 'TAKE_PROFIT_PCT'}
    assert 'MAX_POS_BULL' not in env and 'TAKE_PROFIT_PCT' not in env


def test_export_merges_with_what_the_parent_already_had():
    from backtest.utils.strategy_env import SCOPED_OUT_MARK, export_scoped_out

    env = {SCOPED_OUT_MARK: 'TP_SKIP_REGIMES'}
    export_scoped_out(['MAX_POS_BEAR', '+MAX_POS_BULL'], env)
    # '+' entries are section values being FORCED IN, not removals to propagate
    assert env[SCOPED_OUT_MARK] == 'MAX_POS_BEAR,TP_SKIP_REGIMES'
