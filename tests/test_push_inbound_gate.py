"""The push auto-flush gate must read a source that actually keeps updating.

`push_weixin.py --flush` only delivers queued messages when the peer has sent a
NEW inbound since the message was queued. That "newest inbound" timestamp came
from `~/.hermes/logs/gateway.log`. After the 2026-10-19 gateway restart the log
stopped receiving `inbound message: platform=weixin` lines entirely (frozen at
09:53:23) while messages kept arriving and landing in `state.db`. The gate then
compared against a stale September timestamp and never fired again, so queued
notifications piled up undelivered.

Fix: read the session DB first (`messages` JOIN `sessions` where
source='weixin'), falling back to the gateway log for older layouts.

Run with a python that can import the push script (it loads the weixin platform
adapter at import time):

  /opt/hermes-agent/venv/bin/python3 -m pytest tests/test_push_inbound_gate.py -q
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, "/home/kasm-user")

push = pytest.importorskip(
    "push_weixin",
    reason="push_weixin needs the /opt hermes venv (weixin adapter + aiohttp)")


def _make_db(path, rows):
    """rows: list of (session_source, role, timestamp)."""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    con.execute("CREATE TABLE messages (session_id TEXT, role TEXT, timestamp REAL)")
    for i, (source, role, ts) in enumerate(rows):
        sid = f"s{i}"
        con.execute("INSERT INTO sessions VALUES (?,?)", (sid, source))
        con.execute("INSERT INTO messages VALUES (?,?,?)", (sid, role, ts))
    con.commit()
    con.close()


def test_reads_newest_weixin_inbound_from_the_db(tmp_path):
    db = tmp_path / "state.db"
    _make_db(db, [
        ("weixin", "user", 1000.0),
        ("weixin", "assistant", 9000.0),   # assistant msgs must be ignored
        ("weixin", "user", 5000.0),        # newest INBOUND
        ("cli", "user", 9999.0),           # other platforms must be ignored
    ])
    push.STATE_DBS["default"] = str(db)

    assert push._last_inbound_from_db("default") == 5000.0


def test_falls_back_to_the_log_when_no_db_exists(tmp_path):
    push.STATE_DBS["default"] = str(tmp_path / "missing.db")
    # No DB -> last_inbound_ts must delegate to the log reader, not return 0
    # from the DB reader alone.
    assert push._last_inbound_from_db("default") == 0.0
    assert push.last_inbound_ts("default") == push._last_inbound_from_log("default")


def test_missing_db_file_is_not_fatal(tmp_path):
    push.STATE_DBS["default"] = str(tmp_path / "nope.db")
    assert push._last_inbound_from_db("default") == 0.0


def test_corrupt_db_is_not_fatal(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a sqlite database at all")
    push.STATE_DBS["default"] = str(bad)
    assert push._last_inbound_from_db("default") == 0.0


def test_live_db_reports_a_recent_inbound():
    """On this host the real DB must yield an inbound newer than the frozen log."""
    real = "/home/kasm-user/.hermes/state.db"
    if not os.path.exists(real):
        pytest.skip("no live session DB on this host")
    push.STATE_DBS["default"] = real
    db_ts = push._last_inbound_from_db("default")
    assert db_ts > 0, "live DB should contain weixin inbounds"
    # The DB timestamp must be at least as new as the (frozen) log's.
    assert db_ts >= push._last_inbound_from_log("default")