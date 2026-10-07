"""Account state for the simulation: cash + holdings from the broker app, with a DB fallback.

The live path reads the app over ADB (`trading.sync_app_to_db.check_app_vs_db`), and that is
what this does first — the request was explicitly "holdings read from mobile app". Three things
to know before trusting the output:

  * `app_positions` entries carry a NAME but no symbol (name, market_cap, holdings, available,
    current_price, cost). Symbol resolution therefore goes through the same helper the live sync
    uses, and any position that cannot be resolved is REPORTED, never dropped silently.
  * The app leg needs the phone and ADB up. If it fails, the fallback is `shared/db/imobile.db`,
    which is only as fresh as its last sync — that file's last sync was 2026-07-14 when this was
    written, so a DB-sourced state must be labelled stale in the report rather than presented as
    live.
  * Provenance is always returned and printed: `app`, `db`, or `empty`.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Iterable

def _find_repo_root() -> str:
    """Walk up until the directory that holds both `backtest/` and `trading/` (see bars.py)."""
    d = os.path.dirname(os.path.abspath(__file__))
    while d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, "backtest")) and os.path.isdir(os.path.join(d, "trading")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


REPO_ROOT = _find_repo_root()
DB_PATH = os.path.join(REPO_ROOT, "shared", "db", "imobile.db")


def normalize_code(code: str) -> str:
    """`300308` -> `300308.SZ`.

    The live DB stores bare 6-digit codes in `holding_stocks.code` while the strategy, the order
    plans and the bar cache all use exchange-suffixed symbols. Without this the cache lookup
    silently misses every account holding — the first run of this sim did exactly that and valued
    six real positions at cost because their bars 'did not exist'.
    """
    c = (code or "").strip().upper()
    if not c or "." in c:
        return c
    if c.startswith("6"):
        return f"{c}.SH"
    if c.startswith(("0", "3")):
        return f"{c}.SZ"
    if c.startswith(("4", "8")):
        return f"{c}.BJ"
    return c


def read_db_state(user_id: int = 1) -> dict[str, Any]:
    """Holdings + cash as recorded in the live DB, with its freshness."""
    state: dict[str, Any] = {"source": "db", "cash": None, "holdings": [], "unresolved": [],
                             "note": "", "last_updated": None}
    if not os.path.exists(DB_PATH):
        state["source"] = "empty"
        state["note"] = f"no database at {DB_PATH}"
        return state
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = list(con.execute(
            "SELECT code, name, holdings, available_shares, cost_basis_diluted, cost_basis_total "
            "FROM holding_stocks WHERE holdings > 0 AND user_id = ?", (user_id,)))
        for r in rows:
            state["holdings"].append({
                "code": normalize_code(r["code"]), "name": r["name"], "qty": int(r["holdings"] or 0),
                "sellable": int(r["available_shares"] or 0),
                "cost": float(r["cost_basis_diluted"] or r["cost_basis_total"] or 0.0),
            })
        try:
            row = con.execute("SELECT cash, total_assets, last_updated FROM summary_account "
                              "ORDER BY last_updated DESC LIMIT 1").fetchone()
            if row:
                state["cash"] = float(row["cash"]) if row["cash"] is not None else None
                state["last_updated"] = row["last_updated"]
        except sqlite3.Error:
            pass
        if state["holdings"] and state["last_updated"] is None:
            try:
                row = con.execute("SELECT MAX(last_updated) AS ts FROM holding_stocks").fetchone()
                state["last_updated"] = row["ts"] if row else None
            except sqlite3.Error:
                pass
    finally:
        con.close()
    state["note"] = (f"{len(state['holdings'])} holdings from imobile.db"
                     f"{' (last sync ' + str(state['last_updated']) + ')' if state['last_updated'] else ''}")
    return state


async def read_app_state(user_id: int = 1, timeout_s: float = 120.0) -> dict[str, Any]:
    """Holdings + cash straight from the broker app over ADB. Raises nothing; degrades to db."""
    import asyncio

    try:
        from trading.sync_app_to_db import check_app_vs_db
        result = await asyncio.wait_for(check_app_vs_db(user_id), timeout=timeout_s)
    except Exception as e:  # noqa: BLE001 - any failure degrades, but is reported
        out = read_db_state(user_id)
        out["note"] = f"app read failed ({type(e).__name__}: {e}); fell back to db"
        out["source"] = "db(fallback)"
        return out

    holdings, unresolved = [], []
    for p in result.get("app_positions", []) or []:
        code = normalize_code(_resolve_code(p, user_id) or "")
        if not code:
            unresolved.append(p.get("name"))
            continue
        holdings.append({
            "code": code, "name": p.get("name", code),
            "qty": int(p.get("holdings") or 0), "sellable": int(p.get("available") or 0),
            "cost": float(p.get("cost") or 0.0),
        })
    return {"source": "app", "cash": result.get("app_cash"), "holdings": holdings,
            "unresolved": unresolved, "last_updated": None,
            "note": f"app read: {len(holdings)} holdings, cash {result.get('app_cash')}"
                    + (f", {len(unresolved)} unresolved ({', '.join(map(str, unresolved))})"
                       if unresolved else "")}


def _resolve_code(pos: dict, user_id: int) -> str | None:
    """App positions are name-keyed; map to a symbol with the same helper the live sync uses."""
    code = (pos.get("code") or "").strip()
    if code and code != "000000":
        return code
    try:
        from trading.sync_app_to_db import get_stock_code_by_name
        return get_stock_code_by_name(pos.get("name", ""), user_id)
    except Exception:  # noqa: BLE001
        return None


def load_state(prefer_app: bool = True, user_id: int = 1) -> dict[str, Any]:
    """Blocking wrapper used by the CLI. `prefer_app=False` forces the DB leg."""
    if not prefer_app:
        return read_db_state(user_id)
    import asyncio
    try:
        return asyncio.run(read_app_state(user_id))
    except RuntimeError:
        # already inside a loop (notebook / pytest-asyncio) — degrade rather than explode
        out = read_db_state(user_id)
        out["note"] = "app read unavailable in this context; used db"
        out["source"] = "db(fallback)"
        return out


def names_for(codes: Iterable[str], date: str | None = None, user_id: int = 1) -> dict[str, str]:
    """Best-effort code -> name. The pick file holds only symbol/rank/score, so names come from
    the DB holdings and from the day's order-plan files, which do carry `name`."""
    import glob
    import json

    want = {normalize_code(c) for c in codes if c}
    out: dict[str, str] = {}

    db = read_db_state(user_id)
    for h in db.get("holdings", []):
        if h["code"] in want and h.get("name"):
            out[h["code"]] = h["name"]

    if date:
        for g in (os.path.join(REPO_ROOT, "backtest", "results", "daily", f"smart_orders_{date}.json"),
                  os.path.join(REPO_ROOT, "backtest", "results", "*", f"smart_orders_{date}.json")):
            for path in glob.glob(g):
                if "results_backups" in path:
                    continue
                try:
                    for o in json.load(open(path, encoding="utf-8")).get("smart_orders", []) or []:
                        code = normalize_code(o.get("symbol") or "")
                        if code in want and o.get("name"):
                            out.setdefault(code, str(o["name"]).replace("_expired", "").strip())
                except Exception:  # noqa: BLE001
                    continue
    return out
