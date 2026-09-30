"""Per-strategy configuration sections in `.env`, plus `DEFAULT_STRATEGY`.

`.env` is otherwise flat: every assignment applies globally. Strategy-scoped
config lives under a section header naming the strategy::

    # ── ts_7AZ_96MA_flow_review ──────────────────────────────────────
    REVIEW_COMPOUND_SIZING=true
    # ── end ──

A section runs from its header until the next header, an explicit end marker
(`# end`, `# ── end ──`, `# end of ...`), a major divider line, or EOF.

At startup the engine resolves the strategy, then forces that strategy's section
into `os.environ`, so those values replace the same-named key wherever it came
from — an earlier global line in `.env`, or a `config.json` value the code reads.

Forcing (rather than relying on `load_dotenv`) is deliberate: python-dotenv keeps
the FIRST assignment of a key, so a globally-scoped line earlier in the file would
otherwise silently win over the strategy section.

dotenv also has no concept of these sections — it exports EVERY line as a global,
including lines inside a section owned by a different strategy. `apply_strategy_env`
therefore removes any key that appears only in another strategy's section (unless
it is also set globally), so sections stay scoped to the strategy that owns them.

Within a section the LAST assignment of a key wins, so appending a new line is
enough to change a value — unlike the global area, which needs the old line
commented out because of dotenv's first-wins rule.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, MutableMapping

# Decoration characters used to visually delimit the sections in .env.
_DECOR = " \t─━═=-–—>|<*#"

_DIVIDER_RE = re.compile(r"^[─━═=-]{8,}$")

_END_MARKERS = {"end", "end section", "end sections", "end-strategy-sections"}

_STRATEGY_FILE_SUFFIX = ".py"

DEFAULT_ENV_PATH = Path("/home/kasm-user/apps/imobile/.env")
DEFAULT_FALLBACK_STRATEGY = "ts_7AZ_96MA_flow_review"

# Keys whose VALUES must never be logged. Kept local so this module has no import
# cycle with the rest of the package.
_SECRET_HINT = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|PASSWD|COOKIE|SESSION", re.I)


def known_strategies(strategies_dir: str | Path | None = None) -> set[str]:
    """Strategy names, taken from the files in backtest/strategies/."""
    if strategies_dir is None:
        strategies_dir = Path(__file__).resolve().parent.parent / "strategies"
    d = Path(strategies_dir)
    if not d.is_dir():
        return set()
    return {p.stem for p in d.glob(f"*{_STRATEGY_FILE_SUFFIX}") if not p.stem.startswith("_")}


def _payload(line: str) -> str | None:
    """For a comment line, the text after the leading '#'s, stripped. Else None."""
    s = line.strip()
    if not s.startswith("#"):
        return None
    return s.lstrip("#").strip()


def _is_header(payload: str, known: Iterable[str]) -> str | None:
    """The strategy name if this comment payload is exactly a section header."""
    bare = payload.strip(_DECOR)
    return bare if bare in set(known) else None


def _is_terminator(payload: str) -> bool:
    low = payload.strip(_DECOR).lower()
    return low in _END_MARKERS or low.startswith("end ") or low.startswith("end-")


def _is_divider(payload: str) -> bool:
    """A run of decoration characters carrying no words — the file's major dividers."""
    stripped = payload.strip()
    return bool(stripped) and bool(_DIVIDER_RE.match(stripped))


def _assignment(line: str) -> tuple[str, str] | None:
    """(key, value) for a KEY=VALUE line, else None. Mirrors dotenv's quoting."""
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        return None
    if s.lower().startswith("export "):
        s = s[7:].lstrip()
    key, _, val = s.partition("=")
    key = key.strip()
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return None
    return key, val


def _scan(text: str, names: set[str]) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Single pass returning (sections, global_assignments_outside_sections)."""
    sections: dict[str, dict[str, str]] = {}
    globals_: dict[str, str] = {}
    current: str | None = None

    for line in text.splitlines():
        payload = _payload(line)
        if payload is not None:
            header = _is_header(payload, names)
            if header is not None:
                current = header
                sections.setdefault(current, {})
                continue
            if current is not None and (_is_terminator(payload) or _is_divider(payload)):
                current = None
            continue  # a comment never contributes a value

        pair = _assignment(line)
        if pair is None:
            continue
        if current is None:
            globals_[pair[0]] = pair[1]
        else:
            sections[current][pair[0]] = pair[1]  # last assignment within a section wins

    return sections, globals_


def parse_strategy_sections(
    text: str, known: Iterable[str] | None = None
) -> dict[str, dict[str, str]]:
    """Parse strategy-scoped KEY=VALUE blocks out of .env text.

    Returns {strategy_name: {KEY: VALUE}}. Global assignments and ordinary
    comments are ignored, so the flat part of the file stays untouched.
    """
    names = set(known) if known is not None else known_strategies()
    return _scan(text, names)[0]


def parse_env(
    env_path: str | Path | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """(sections, global assignments) for the .env file."""
    p = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
    if not p.is_file():
        return {}, {}
    return _scan(p.read_text(encoding="utf-8", errors="replace"), known_strategies())


def read_sections(env_path: str | Path | None = None) -> dict[str, dict[str, str]]:
    p = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
    if not p.is_file():
        return {}
    return parse_strategy_sections(p.read_text(encoding="utf-8", errors="replace"))


def default_strategy(
    env_path: str | Path | None = None,
    fallback: str | None = None,
    known: Iterable[str] | None = None,
) -> str:
    """DEFAULT_STRATEGY from .env, validated against the real strategy list."""
    fb = fallback or DEFAULT_FALLBACK_STRATEGY
    p = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
    value = None
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            pair = _assignment(line)
            if pair and pair[0] == "DEFAULT_STRATEGY":
                value = pair[1]
                break
    if not value:
        return fb
    names = set(known) if known is not None else known_strategies()
    if names and value not in names:
        return fb
    return value


def apply_strategy_env(
    strategy: str,
    env_path: str | Path | None = None,
    environ: MutableMapping[str, str] | None = None,
    known: Iterable[str] | None = None,
    neutralized: list[str] | None = None,
) -> list[str]:
    """Force the strategy's .env section into the environment.

    Also undoes python-dotenv's flat reading of this file. dotenv has no concept
    of sections, so it exports EVERY line as a global - including lines inside a
    section belonging to some other strategy. Any key that appears only inside a
    different strategy's section is therefore removed here (unless it is also set
    globally), so each section stays scoped to the strategy that owns it.

    Returns the keys applied (never their values). Keys removed are appended to
    `neutralized` when that list is supplied.
    """
    import os

    env = environ if environ is not None else os.environ
    if env_path is None and known is None:
        sections, globals_ = parse_env()
    else:
        p = Path(env_path) if env_path is not None else DEFAULT_ENV_PATH
        names = set(known) if known is not None else known_strategies()
        text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        sections, globals_ = _scan(text, names)

    for other, values in sections.items():
        if other == strategy:
            continue
        for key in values:
            if key not in globals_ and key in env:
                del env[key]
                if neutralized is not None:
                    neutralized.append(key)

    applied: list[str] = []
    for key, value in sections.get(strategy, {}).items():
        env[key] = value
        applied.append(key)
    return applied


def redact(key: str) -> bool:
    """True when a key's value must not be printed."""
    return bool(_SECRET_HINT.search(key))
