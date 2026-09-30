"""Tests for per-strategy .env sections and DEFAULT_STRATEGY resolution."""

from backtest.utils import strategy_env as se

KNOWN = {"ts_7AZ_96MA_flow_review", "ts_7AZ_96MA_flow_v2", "ts_7AZ"}


def test_header_matches_exact_name_with_decoration():
    text = "# ── ts_7AZ_96MA_flow_review ──────────────\nREVIEW_COMPOUND_SIZING=true\n"
    assert se.parse_strategy_sections(text, KNOWN) == {
        "ts_7AZ_96MA_flow_review": {"REVIEW_COMPOUND_SIZING": "true"}
    }


def test_header_matches_bare_name():
    text = "# ts_7AZ\nSCORE_MIN=6\n"
    assert se.parse_strategy_sections(text, KNOWN) == {"ts_7AZ": {"SCORE_MIN": "6"}}


def test_prose_comment_is_not_a_header():
    # the real .env has prose mentioning strategy names - must not open a section
    text = ("# For V2 version backtest (ts_7AZ_96MA_flow_review / V2 family)\n"
            "V2_EXT_CAP=false\n")
    assert se.parse_strategy_sections(text, KNOWN) == {}


def test_global_assignments_outside_sections_are_ignored():
    text = "TUSHARE_TOKEN=abc\nSL_BULL=0.025\n# ts_7AZ\nSCORE_MIN=6\n"
    assert se.parse_strategy_sections(text, KNOWN) == {"ts_7AZ": {"SCORE_MIN": "6"}}


def test_section_ends_at_next_header():
    text = ("# ts_7AZ\nSCORE_MIN=6\n"
            "# ts_7AZ_96MA_flow_v2\nV2_EXT_CAP=false\n")
    got = se.parse_strategy_sections(text, KNOWN)
    assert got == {"ts_7AZ": {"SCORE_MIN": "6"},
                   "ts_7AZ_96MA_flow_v2": {"V2_EXT_CAP": "false"}}


def test_section_ends_at_end_marker():
    text = ("# ts_7AZ\nSCORE_MIN=6\n# ── end ──\n"
            "NOT_IN_SECTION=1\n")
    assert se.parse_strategy_sections(text, KNOWN) == {"ts_7AZ": {"SCORE_MIN": "6"}}


def test_section_ends_at_major_divider():
    text = ("# ts_7AZ\nSCORE_MIN=6\n"
            "# ══════════════════════════════\n"
            "AFTER_DIVIDER=1\n")
    assert se.parse_strategy_sections(text, KNOWN) == {"ts_7AZ": {"SCORE_MIN": "6"}}


def test_last_assignment_in_section_wins():
    text = "# ts_7AZ\nSCORE_MIN=5\nSCORE_MIN=6\n"
    assert se.parse_strategy_sections(text, KNOWN) == {"ts_7AZ": {"SCORE_MIN": "6"}}


def test_quoting_and_export_are_stripped():
    text = '# ts_7AZ\nexport SCORE_MIN="6"\nA=\'x y\'\n'
    assert se.parse_strategy_sections(text, KNOWN) == {"ts_7AZ": {"SCORE_MIN": "6", "A": "x y"}}


def test_blank_lines_and_comments_inside_section_are_skipped():
    text = "# ts_7AZ\n\n# a note\nSCORE_MIN=6\n"
    assert se.parse_strategy_sections(text, KNOWN) == {"ts_7AZ": {"SCORE_MIN": "6"}}


def test_section_ends_at_named_end_marker():
    text = ("# ts_7AZ\nSCORE_MIN=6\n# ── end ts_7AZ ──\n"
            "NOT_IN_SECTION=1\n")
    assert se.parse_strategy_sections(text, KNOWN) == {"ts_7AZ": {"SCORE_MIN": "6"}}


def test_dashed_divider_also_terminates():
    text = "# ts_7AZ\nSCORE_MIN=6\n# ----------\nAFTER=1\n"
    assert se.parse_strategy_sections(text, KNOWN) == {"ts_7AZ": {"SCORE_MIN": "6"}}


def test_malformed_lines_are_ignored():
    text = "# ts_7AZ\nthis is not an assignment\n1BAD=x\nSCORE_MIN=6\n"
    assert se.parse_strategy_sections(text, KNOWN) == {"ts_7AZ": {"SCORE_MIN": "6"}}


def test_unknown_name_does_not_open_a_section():
    text = "# not_a_strategy\nA=1\n"
    assert se.parse_strategy_sections(text, KNOWN) == {}


def _env_file(tmp_path, body):
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return p


def test_default_strategy_reads_and_validates(tmp_path):
    p = _env_file(tmp_path, "DEFAULT_STRATEGY=ts_7AZ_96MA_flow_v2\n")
    assert se.default_strategy(p, known=KNOWN) == "ts_7AZ_96MA_flow_v2"


def test_default_strategy_falls_back_when_unknown(tmp_path):
    p = _env_file(tmp_path, "DEFAULT_STRATEGY=ts_typo\n")
    assert se.default_strategy(p, fallback="ts_7AZ", known=KNOWN) == "ts_7AZ"


def test_default_strategy_falls_back_when_absent(tmp_path):
    p = _env_file(tmp_path, "SL_BULL=0.025\n")
    assert se.default_strategy(p, fallback="ts_7AZ", known=KNOWN) == "ts_7AZ"


def test_default_strategy_falls_back_when_file_missing(tmp_path):
    assert se.default_strategy(tmp_path / "nope.env", fallback="ts_7AZ", known=KNOWN) == "ts_7AZ"


def test_apply_strategy_env_overrides_existing_value(tmp_path):
    """The section must REPLACE a globally-set value, not defer to dotenv order."""
    p = _env_file(tmp_path, "# ts_7AZ\nSCORE_MIN=6\n")
    env = {"SCORE_MIN": "5"}                     # e.g. set by an earlier global line
    applied = se.apply_strategy_env("ts_7AZ", env_path=p, environ=env, known=KNOWN)
    assert applied == ["SCORE_MIN"]
    assert env["SCORE_MIN"] == "6"


def test_apply_strategy_env_noop_for_strategy_without_section(tmp_path):
    p = _env_file(tmp_path, "# ts_7AZ\nSCORE_MIN=6\n")
    env = {}
    assert se.apply_strategy_env("ts_7AZ_96MA_flow_v2", env_path=p, environ=env, known=KNOWN) == []
    assert env == {}


def test_apply_strategy_env_returns_keys_not_values(tmp_path):
    p = _env_file(tmp_path, "# ts_7AZ\nTUSHARE_TOKEN=supersecret\n")
    env = {}
    applied = se.apply_strategy_env("ts_7AZ", env_path=p, environ=env, known=KNOWN)
    assert applied == ["TUSHARE_TOKEN"]          # key exposed, value never returned


def test_other_strategy_section_keys_are_neutralized(tmp_path):
    """dotenv exports every line, so a key owned by another section leaks in;
    applying a different strategy must remove it."""
    p = _env_file(tmp_path, "# ts_7AZ\nSCORE_MIN=6\n# ts_7AZ_96MA_flow_v2\nV2_EXT_CAP=false\n")
    env = {"SCORE_MIN": "6", "V2_EXT_CAP": "false"}      # as if leaked by load_dotenv()
    neutralized = []
    applied = se.apply_strategy_env("ts_7AZ_96MA_flow_v2", env_path=p, environ=env,
                                    known=KNOWN, neutralized=neutralized)
    assert applied == ["V2_EXT_CAP"]
    assert env == {"V2_EXT_CAP": "false"}                # SCORE_MIN removed
    assert neutralized == ["SCORE_MIN"]


def test_globally_set_key_is_not_neutralized(tmp_path):
    """A key that also exists outside any section is a real global: keep it."""
    p = _env_file(tmp_path,
                  "SCORE_MIN=5\n"
                  "# ts_7AZ\nSCORE_MIN=6\n"
                  "# ts_7AZ_96MA_flow_v2\nV2_EXT_CAP=false\n")
    env = {"SCORE_MIN": "5", "V2_EXT_CAP": "false"}
    se.apply_strategy_env("ts_7AZ_96MA_flow_v2", env_path=p, environ=env, known=KNOWN)
    assert env["SCORE_MIN"] == "5"                       # global survives


def test_active_strategy_section_wins_over_global(tmp_path):
    p = _env_file(tmp_path, "SCORE_MIN=5\n# ts_7AZ\nSCORE_MIN=6\n")
    env = {"SCORE_MIN": "5"}
    se.apply_strategy_env("ts_7AZ", env_path=p, environ=env, known=KNOWN)
    assert env["SCORE_MIN"] == "6"                       # section replaces the global


def test_redact_flags_secret_like_keys():
    assert se.redact("TUSHARE_TOKEN")
    assert se.redact("OPENROUTER_API_KEY")
    assert not se.redact("SCORE_MIN")


def test_real_env_file_parses_without_error():
    """The shipped .env must parse; sections, if present, must be real strategies."""
    if not se.DEFAULT_ENV_PATH.is_file():
        return
    known = se.known_strategies()
    sections = se.read_sections()
    assert set(sections) <= known
    for key, value in [(k, v) for s in sections.values() for k, v in s.items()]:
        assert key.isupper() or "_" in key
        assert isinstance(value, str)
