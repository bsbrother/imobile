"""
ts_7AZ_grok — CANSLIM ts_7AZ enhanced with Grok news+analysis re-ranking.

Pipeline:
  1. Run the same CANSLIM screener as ts_7AZ (identical stock pool → fair comparison)
  2. Fetch market index data + HS300 valuation for context
  3. Fetch breaking market/macro news via Grok web search
  4. Call Grok to re-rank the CANSLIM candidates with news+market context
  5. Output top picks to /tmp/tmp in standard format

The Grok layer acts as a "news-aware filter" on top of the pure-technical CANSLIM
screener. It can:
  - Downgrade stocks in sectors facing regulatory headwinds
  - Upgrade stocks benefiting from policy catalysts
  - Reduce position count when market regime is bearish
  - Boost stocks with positive breaking news

Usage (invoked by engine.py):
  python backtest/strategies/ts_7AZ_grok.py <date YYYYMMDD> ts_7AZ_grok
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
from loguru import logger
import pandas as pd
import warnings

import tushare as ts
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest.utils.trading_calendar import get_trading_days_before
from backtest.utils.util import convert_trade_date
from backtest.utils.market_regime import detect_market_regime
from backtest.strategies.ts_7AZ import (
    canslim_screener,
    C_EPS_GROWTH_THRESHOLD,
    A_ROE_THRESHOLD,
    N_52W_HIGH_RATIO,
    S_MARKET_CAP_MAX,
    L_RPS_THRESHOLD,
    I_TURNOVER_MIN,
    I_TURNOVER_MAX,
    LOOKBACK_DAYS,
)

warnings.filterwarnings("ignore", category=UserWarning, module='py_mini_racer')
load_dotenv()

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GROK_MODEL = os.getenv("GROK_MODEL", "x-ai/grok-4.5")
PRO = ts.pro_api(TUSHARE_TOKEN) if TUSHARE_TOKEN else None

# How many CANSLIM candidates to send to Grok for re-ranking
GROK_CANDIDATE_POOL = 20
# How many final picks to output (MAX_POSITIONS in engine is 12, but we can be more selective)
GROK_FINAL_PICKS = 12
# Min CANSLIM score to send to Grok
MIN_CANSLIM_SCORE = 4
# Skip news in backtest mode (avoids lookahead bias — Grok web search returns
# current news, not historical)
SKIP_NEWS = os.getenv("GROK_SKIP_NEWS", "1") == "1"

INDEX_CODES = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "000300.SH": "沪深300",
    "399006.SZ": "创业板指",
}


# ── Market data for Grok context ──────────────────────────────

def fetch_index_snapshot(end_date: str) -> dict:
    """Fetch major index data for Grok market context."""
    start = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d")
    result = {}
    for code, name in INDEX_CODES.items():
        try:
            df = PRO.index_daily(ts_code=code, start_date=start, end_date=end_date)
            if df is None or df.empty:
                continue
            df = df.sort_values("trade_date")
            latest = df.iloc[-1]
            pct_5d = (df["close"].iloc[-1] / df["close"].iloc[-6] - 1) * 100 if len(df) >= 6 else None
            pct_20d = (df["close"].iloc[-1] / df["close"].iloc[-21] - 1) * 100 if len(df) >= 21 else None
            ma20 = df["close"].rolling(20).mean().iloc[-1] if len(df) >= 20 else None
            result[code] = {
                "name": name,
                "close": round(float(latest["close"]), 2),
                "pct_1d": round(float(latest["pct_chg"]), 2),
                "pct_5d": round(pct_5d, 2) if pct_5d is not None else None,
                "pct_20d": round(pct_20d, 2) if pct_20d is not None else None,
                "above_ma20": bool(latest["close"] > ma20) if ma20 else None,
            }
        except Exception:
            continue
    return result


def fetch_hs300_valuation(end_date: str) -> dict:
    """Fetch HS300 PE/PB for valuation context."""
    start = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=60)).strftime("%Y%m%d")
    try:
        df = PRO.index_dailybasic(ts_code="000300.SH", start_date=start, end_date=end_date)
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    latest = df.sort_values("trade_date").iloc[-1]
    return {
        "pe_ttm": float(latest.get("pe_ttm", 0)) or None,
        "pb": float(latest.get("pb", 0)) or None,
    }


# ── Grok LLM layer ─────────────────────────────────────────────

_news_cache = {}

def fetch_market_news(industries: list) -> str:
    """Fetch breaking A-share market + macro news via Grok web search plugin."""
    import urllib.request

    if not OPENROUTER_API_KEY:
        return "(OPENROUTER_API_KEY not set, skipping news)"

    # Cache key by industries signature (same industries → reuse within session)
    cache_key = "|".join(sorted(industries[:5]))
    if cache_key in _news_cache:
        logger.info("Reusing cached market news")
        return _news_cache[cache_key]

    ind_str = "、".join(industries[:5]) if industries else "综合"
    query = f"A股市场最新动态、宏观政策、资金流向。行业关注: {ind_str}。搜索最近3天的相关新闻。"

    payload = json.dumps({
        "model": GROK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a news research assistant for A-share market. "
                    "Search for the latest market and macro news. "
                    "Return 8-12 concise bullet points. Focus on: "
                    "(1) major market moves, (2) PBOC/government policy changes, "
                    "(3) industry regulatory changes, (4) capital flows. "
                    "Reply in Chinese. Be brief — one line per news item."
                ),
            },
            {"role": "user", "content": query},
        ],
        "plugins": [{"id": "web"}],
        "max_tokens": 1000,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        result = body["choices"][0]["message"]["content"]
        _news_cache[cache_key] = result
        return result
    except Exception as e:
        logger.warning(f"News fetch failed: {e}")
        return f"(news fetch error: {e})"


def grok_rerank_candidates(
    candidates: list,
    indexes: dict,
    hs300_val: dict,
    news_block: str,
    regime: str,
    end_date: str,
) -> list:
    """
    Call Grok to re-rank CANSLIM candidates with news+market context.

    Returns list of {symbol, grok_score, reason} sorted by grok_score desc.
    """
    from openai import OpenAI

    if not OPENROUTER_API_KEY:
        logger.warning("OPENROUTER_API_KEY not set — skipping Grok re-rank, using CANSLIM order")
        return [{"symbol": c["ts_code"], "grok_score": c["score"], "reason": "no-grok"} for c in candidates]

    # Build candidate summary (compact to save tokens)
    cand_summary = []
    for c in candidates:
        cand_summary.append({
            "symbol": c["ts_code"],
            "name": c.get("name", ""),
            "industry": c.get("industry", ""),
            "canslim_score": c["score"],
            "price": round(c.get("price", 0), 2),
            "pct_5d": c.get("return_250", 0),  # we don't have 5d here, use return_250 as momentum
            "rsi": None,
            "pe": c.get("eps_growth"),
            "roe": c.get("roe"),
            "turnover": round(c.get("turnover_rate", 0), 2),
            "market_cap": round(c.get("total_mv", 0) / 1e8, 1),  # 亿
            "flags": {
                "C": c.get("c_eps", False),
                "A": c.get("a_roe", False),
                "N": c.get("n_near_high", False),
                "S": c.get("s_small_cap", False),
                "L": c.get("l_rps_pass", False),
                "I": c.get("i_turnover_ok", False),
                "M": c.get("m_above_ma", False),
            },
        })

    prompt = f"""日期: {end_date}
市场环境: {regime}

## 市场指数 (历史数据)
{json.dumps(indexes, ensure_ascii=False, indent=2)}

## 沪深300估值
{json.dumps(hs300_val, ensure_ascii=False)}

## 市场新闻
{news_block}

## CANSLIM候选股票 (已通过技术+基本面筛选)
{json.dumps(cand_summary, ensure_ascii=False, indent=2)}

## 任务
基于以上数据，对每只候选股票进行市场环境维度的重新评分。

评分规则 (0-100分):
- CANSLIM基础分占70% (canslim_score/7 * 70)
- 市场环境适配度 ±15分 (牛市加分，熊市减分)
- 行业周期位置 ±15分

输出JSON格式 (不要其他文字):
```json
[
  {{"symbol": "xxxxxx.SZ", "score": 85, "action": "BUY", "reason": "简要理由"}},
  ...
]
```

重要规则:
- 只输出JSON，不要解释
- action为 BUY 或 AVOID
- 只在股票有明显风险时才AVOID (如:ROE为负、行业明确下行、估值极高)
- 大部分股票应该是BUY，只是评分高低不同
- CANSLIM 5分的股票默认BUY，不应AVOID
- 市场牛市时减少AVOID
"""

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

    try:
        resp = client.chat.completions.create(
            model=GROK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a quant trading analyst. Output ONLY valid JSON, no explanation.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=2048,
            temperature=0.3,
        )
        raw = resp.choices[0].message.content.strip()

        # Extract JSON from response (handle markdown code blocks)
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        scored = json.loads(raw)
        logger.info(f"Grok re-ranked {len(scored)} candidates")
        return scored

    except Exception as e:
        logger.warning(f"Grok re-rank failed: {e} — using CANSLIM order")
        return [{"symbol": c["ts_code"], "grok_score": c["score"], "reason": f"grok-error: {e}"} for c in candidates]


# ── Main entry point ───────────────────────────────────────────

def _load_baseline_picks(end_date: str) -> list | None:
    """Load CANSLIM candidates from baseline backup (avoids re-running screener)."""
    import glob
    backup_patterns = [
        "backtest/results_backups/20260101_20260619_ts_7AZ_70.60_baseline",
        "backtest/results_backups/*ts_7AZ*",
    ]
    for pat in backup_patterns:
        for bdir in glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), pat)):
            bfile = os.path.join(bdir, f"pick_stocks_{end_date}.json")
            if os.path.exists(bfile):
                with open(bfile) as f:
                    data = json.load(f)
                stocks = data.get("selected_stocks", [])
                if stocks:
                    logger.info(f"Reused {len(stocks)} CANSLIM picks from {os.path.basename(bdir)}")
                    return stocks
    return None


_stock_basic_cache = None

def _enrich_picks_with_details(picks: list, end_date: str) -> list:
    """Enrich baseline picks (symbol+score) with name/industry for Grok context."""
    global _stock_basic_cache
    if _stock_basic_cache is None:
        try:
            _stock_basic_cache = PRO.stock_basic(fields="ts_code,name,industry,market,list_date")
        except Exception:
            _stock_basic_cache = pd.DataFrame()
    basic_map = {}
    if not _stock_basic_cache.empty:
        basic_map = {r["ts_code"]: r for _, r in _stock_basic_cache.iterrows()}

    # Fetch daily_basic for latest turnover/market_cap
    lookback_start = get_trading_days_before(end_date, LOOKBACK_DAYS - 1)
    enriched = []
    for p in picks:
        sym = p["symbol"]
        basic = basic_map.get(sym, {})
        enriched.append({
            "ts_code": sym,
            "name": basic.get("name", sym),
            "industry": basic.get("industry", ""),
            "score": p.get("score", 4),
            "price": 0,
            "return_250": 0,
            "turnover_rate": 0,
            "total_mv": 0,
            "c_eps": False,
            "a_roe": False,
            "n_near_high": False,
            "s_small_cap": False,
            "l_rps_pass": False,
            "i_turnover_ok": False,
            "m_above_ma": False,
        })
    return enriched


def pick_strong_stocks_grok(end_date: str) -> pd.DataFrame:
    """
    Main entry: CANSLIM screener + Grok news re-ranking.
    Outputs to /tmp/tmp in standard format.
    """
    # 1. Try to reuse baseline CANSLIM picks (fast path)
    baseline = _load_baseline_picks(end_date)
    if baseline:
        candidates = _enrich_picks_with_details(baseline, end_date)
        logger.info(f"Using {len(candidates)} baseline CANSLIM picks for Grok re-ranking")
    else:
        # Fallback: run CANSLIM screener fresh
        df = canslim_screener(end_date)
        if df.empty:
            return df
        df = df[df["score"] >= MIN_CANSLIM_SCORE].reset_index(drop=True)
        candidates = df.head(GROK_CANDIDATE_POOL).to_dict("records")
        logger.info(f"CANSLIM screener: {len(df)} stocks, sending top {len(candidates)} to Grok")

    # 2. Fetch market context
    indexes = fetch_index_snapshot(end_date)
    hs300_val = fetch_hs300_valuation(end_date)

    # 3. Collect industries for news query
    industries = list(set(c.get("industry", "") for c in candidates if c.get("industry")))

    # 3. Fetch news (skip in backtest mode — Grok web search returns current
    #    news, not historical, which would be lookahead bias)
    if SKIP_NEWS:
        news_block = "(news skipped — backtest mode)"
    else:
        news_block = fetch_market_news(industries)

    # 5. Grok re-rank
    regime_data = detect_market_regime(end_date)
    regime = regime_data.get("regime", "normal")

    scored = grok_rerank_candidates(candidates, indexes, hs300_val, news_block, regime, end_date)

    # 6. Merge Grok scores back
    grok_map = {}
    for s in scored:
        sym = s.get("symbol", "")
        grok_map[sym] = s

    final_picks = []
    for c in candidates:
        sym = c["ts_code"]
        g = grok_map.get(sym, {})
        grok_score = g.get("score", c["score"] * 10)  # fallback: scale CANSLIM to 0-100
        action = g.get("action", "BUY")
        reason = g.get("reason", "")

        # Skip AVOID stocks (Grok says avoid)
        if action == "AVOID":
            logger.info(f"  Grok AVOID: {sym} ({reason})")
            continue

        final_picks.append({
            "ts_code": sym,
            "name": c.get("name", ""),
            "canslim_score": c["score"],
            "grok_score": grok_score,
            "reason": reason,
        })

    # Sort by Grok score, take top N
    final_picks.sort(key=lambda x: x["grok_score"], reverse=True)

    # Ensure minimum picks — if Grok filtered too aggressively,
    # add back top CANSLIM candidates that were AVOIDed
    min_picks = max(8, GROK_FINAL_PICKS - 4)  # at least 8 picks
    if len(final_picks) < min_picks:
        # Re-add AVOIDed stocks sorted by CANSLIM score
        avoided = []
        for c in candidates:
            sym = c["ts_code"]
            g = grok_map.get(sym, {})
            if g.get("action") == "AVOID":
                avoided.append({
                    "ts_code": sym,
                    "name": c.get("name", ""),
                    "canslim_score": c["score"],
                    "grok_score": g.get("score", c["score"] * 10),
                    "reason": g.get("reason", "re-added for min picks"),
                })
        avoided.sort(key=lambda x: x["canslim_score"], reverse=True)
        needed = min_picks - len(final_picks)
        final_picks.extend(avoided[:needed])
        logger.info(f"Re-added {needed} AVOIDed stocks to reach min_picks={min_picks}")

    final_picks = final_picks[:GROK_FINAL_PICKS]

    if not final_picks:
        # Fallback: if Grok avoided everything, use top CANSLIM picks
        logger.warning("Grok avoided all candidates — falling back to CANSLIM order")
        for c in candidates[:GROK_FINAL_PICKS]:
            final_picks.append({
                "ts_code": c["ts_code"],
                "name": c.get("name", ""),
                "canslim_score": c["score"],
                "grok_score": c["score"] * 10,
                "reason": "fallback",
            })

    # 7. Output to /tmp/tmp in standard format
    selected_stocks = []
    for i, p in enumerate(final_picks):
        selected_stocks.append({
            "rank": i + 1,
            "symbol": p["ts_code"],
            "score": float(p["grok_score"]) / 10.0,  # scale 0-100 → 0-10 for engine compat
        })

    output_file = "/tmp/tmp"
    if os.path.isdir(output_file):
        output_file = "/tmp/ts_7AZ_grok_tmp.json"
    with open(output_file, "w") as f:
        json.dump({"selected_stocks": selected_stocks}, f)

    logger.info(f"Saved {len(selected_stocks)} Grok-ranked picks to {output_file}")

    # Log summary
    for p in final_picks:
        logger.info(f"  {p['ts_code']} {p['name']} CS={p['canslim_score']} Grok={p['grok_score']} {p['reason'][:40]}")

    return pd.DataFrame(final_picks)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) >= 1:
        date = convert_trade_date(argv[0])
    else:
        date = datetime.now().strftime("%Y%m%d")

    src = argv[1] if len(argv) >= 2 else "ts_7AZ_grok"

    date = get_trading_days_before(date, 1)
    end_date = date

    pick_strong_stocks_grok(end_date)
