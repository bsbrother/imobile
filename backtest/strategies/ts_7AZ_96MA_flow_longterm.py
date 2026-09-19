#!/usr/bin/env python3
"""
ts_7AZ_96MA_flow_longterm — NLP-augmented flow filter (Long-term Project).

Extends the ts_7AZ_96MA_flow_v2 flow filter with natural-language analysis of
financial text, per the article "当 AI 开始读财报：超额收益藏在'预计'和'有望'里"
(2026-09-17, Python金融量化).

Architecture (the article's "third-generation" pattern):
  1. LLM reads & expands — 5 perspectives per text item:
     - title / headline summary
     - catalyst / trigger
     - subtext / unstated implications
     - unstated risks
     - forward guidance
  2. Fine-tuned small model judges — FinBERT2-style domain specialist
     classifies each perspective as positive/neutral/negative
  3. XGBoost/classifier selects — weights perspectives into a final
     signal that is near-uncorrelated with price-volume factors

STATUS: Keyword-based NLP hook v1 (2026-09-18) — _forecast_text_analysis is
        implemented using negative/hazard/hedging keyword detection on the
        forecast CSV's summary and change_reason fields. No LLM required;
        follows the article's finding that wording precision (precision →
        vague = alarming) and explicit red flags (商誉减值 etc.) carry
        independent predictive signal.

Data sources (per article, in implementation order):
  - 业绩预告 (Tushare forecast endpoint) — structural gate already built
  - 互动易/E互动 (深圳证券交易所互动易 / 上证E互动) — needs scraping or
    pro-level Tushare permission (irm_qa_sz/irm_qa_sh currently blocked)
  - 年度报告 — needs report-type-aware fetching per 知乎 article on
    financial data discipline (report_type parameter handles adjustments)
  - 业绩快报 (Tushare express endpoint) — quick pre-annual signals

Data-time discipline (CRITICAL — the article's #1 failure mode):
  - Align ALL signals on the disclosure TIMESTAMP (公告日期/发布时间),
    never on the reporting period (报告期).
  - 业绩预告 revision announcements (修正公告) must use first_ann_date
    not ann_date, or the model sees corrected data before its release.
  - 研报 publish time ≠ writing time — scrape the page publish date,
    not the report date embedded in the document.

Env gates (all default OFF — zero change from base flow filter):
  NLP_SENTIMENT_GATE     = false  # enable NLP sentiment checks
  NLP_IRM_EVASION_GATE   = false  # enable 互动易 evasion detection
  NLP_FORECAST_TEXT_GATE = false  # enable 业绩预告 full-text analysis
  NLP_MODEL_CACHE_DIR    = ''     # path to FinBERT2 model cache

See also:
  - /tmp/wx/2026-09-17_Python金融量化_当 AI 开始读财报：超额收益藏在预计和有望里.md
  - https://tushare.pro/document/2?doc_id=45  (forecast)
  - https://tushare.pro/document/2?doc_id=367 (irm_qa_sz)
  - https://tushare.pro/document/2?doc_id=366 (irm_qa_sh)
  - https://zhuanlan.zhihu.com/p/7276442988 (financial data discipline)
"""

import os
import logging
from typing import Optional
import pandas as pd
from loguru import logger

from backtest.strategies.ts_7AZ_96MA_flow_v2 import (
    _apply_flow_filter_v2,  # base flow filter
    _load_forecast, _forecast_is_bad,
    V2_FORECAST_GATE, V2_FORECAST_LOOKBACK,
)

# NOTE: logger comes from loguru (imported above) so gate firings land in
# logs/app.log. The engine runs each strategy as a subprocess with captured
# stdout/stderr, discarding it on success — stdlib logging would be invisible.

# ── NLP Gates (env) ────────────────────────────────────────────────────────
# All OFF by default. NLP_FORECAST_TEXT_GATE failed discrimination — see below.
NLP_SENTIMENT_GATE = os.getenv('NLP_SENTIMENT_GATE', 'false').lower() in ('true', '1', 'yes')
NLP_IRM_EVASION_GATE = os.getenv('NLP_IRM_EVASION_GATE', 'false').lower() in ('true', '1', 'yes')
NLP_FORECAST_TEXT_GATE = os.getenv('NLP_FORECAST_TEXT_GATE', 'false').lower() in ('true', '1', 'yes')
# ^ DEFAULT OFF (2026-09-19). The v1 keyword gate FAILED its discrimination test:
#   flagged (would-reject) n=216 mean fwd10 -0.91% (win 40.7%)
#   unflagged (would-keep) n=166 mean fwd10 -3.35% (win 38.6%)
#   kept minus rejected = -2.43pp -> anti-predictive, destroys value.
# Root cause: keyword presence != sentiment polarity. It rejects 扭亏 (mean
# -0.65%, better than average) at 43% while keeping 略增 (mean -4.27%, worst
# bucket) at 92%. "商誉减值" in a 扭亏 explanation refers to LAST year's
# impairment (a positive setup); positive sentences contain "减少" ("成本减少").
# This reproduces the failure the article warns about (KDD 2025: LLM F1 <0.523
# vs FinBERT2 0.925) — financial text is context-dependent and keywords cannot
# resolve it. The informative signal is the forecast TYPE, already used by the
# base structural gate. Do NOT enable without a real model.
NLP_FORECAST_TEXT_THRESHOLD = int(os.getenv('NLP_FORECAST_TEXT_THRESHOLD', '2'))
                                          # reject if yellow+hedge flags >= N
NLP_MODEL_CACHE_DIR = os.getenv('NLP_MODEL_CACHE_DIR', '')

# ── Keyword sets for forecast text analysis ───────────────────────────────
# Red flags: any single match → reject (explicit danger signals).
# Yellow flags: accumulate — operational deterioration indicators.
# Hedge flags: vague/precision-shift language per the article's finding that
#   "较大幅度增长" (+70.67%) ≠ "大幅度增长" (+849%) — vaguer = worse.
TEXT_RED_FLAGS = [
    '商誉减值',         # goodwill impairment — classic red flag
    '大幅下降',         # sharp decline
    '严重亏损',         # severe loss
    '持续亏损',         # continued losses
    '退市风险',         # delisting risk
    '立案调查',         # regulatory investigation
    '无法表示意见',     # disclaimer of opinion (audit)
]

TEXT_YELLOW_FLAGS = [
    '下降',             # decline
    '减少',             # reduction
    '下滑',             # slide
    '压力',             # pressure
    '亏损',             # loss
    '不确定性',         # uncertainty
    '风险',             # risk
    '减值',             # impairment
    '非经常性损益',     # non-recurring gains/losses
    '政府补助',         # government subsidy
]

TEXT_HEDGE_FLAGS = [
    '一定幅度',         # vague magnitude
    '预计将',           # hedging future prediction
    '尚需',             # still requires
    '取决于',           # depends on
    '可能存在',         # may exist
    '不排除',           # does not rule out
    '请关注',           # "please pay attention to" — deflection
]

# TODO: add 互动易 access when credential is obtained (needs Tushare pro
# permission or direct CNINFO scraping). Placeholder:
# IRM_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
#     os.path.abspath(__file__)))), 'shared', 'data', 'irm_qa_2026.csv')


# ═══════════════════════════════════════════════════════════════════════════
# Placeholder NLP functions — fill in as the pipeline matures
# ═══════════════════════════════════════════════════════════════════════════

def _sentiment_check(text: str) -> Optional[dict]:
    """Placeholder: FinBERT2-style financial sentiment classification.
    
    When implemented, returns a dict with:
      - sentiment: 'positive'|'neutral'|'negative'
      - confidence: 0-1 score
      - keywords: list of salient triggering phrases
    
    The article's key finding: negative sentiment is the predictive direction.
    Positive text is already priced in; negative text leaks gradually and
    predicts future declines. Use domain-specific model (FinBERT2, NOT GPT)
    because financial text labelling conventions are not learnable from
    general corpora (KDD 2025, F1 0.925 vs LLM <0.523).
    """
    if not NLP_SENTIMENT_GATE or not NLP_MODEL_CACHE_DIR:
        return None
    # TODO: load FinBERT2 model from NLP_MODEL_CACHE_DIR
    # TODO: tokenize, classify, return structured result
    return None


def _irm_evasion(code6: str, ref_date: str) -> Optional[str]:
    """Placeholder: 互动易/E互动 evasion detection.
    
    Structural check (no NLP required per the article): investor Q&A
    responses containing evasion keywords indicate the company is hiding
    bad news. The article quotes:
      - 北大研究: 负面问题+模糊回复 → 显著负向预测超额收益
      - 央财研究: 机构在互动易回复语气负面后买入更少、卖出更多
    
    Evasion keywords (from the article + empirical samples):
      "请关注公司公告", "以公告为准", "详见定期报告",
      "暂未披露", "不便透露", "请以公司公告为准"
    
    When implemented, returns the most recent evasion response text
    or None if no evasion detected within LOOKBACK days.
    """
    if not NLP_IRM_EVASION_GATE:
        return None
    # TODO: load IRM cache (Tushare irm_qa_sz/irm_qa_sh when permitted,
    #       or direct CNINFO scraping with anti-bot proxy)
    # TODO: filter by code6 + date window
    # TODO: check 'a' (answer) field for evasion keywords
    # TODO: return evading response text or None
    return None


def _forecast_text_analysis(code6: str, ref_date: str) -> Optional[dict]:
    """Keyword-based 业绩预告 text analysis (v1, 2026-09-18).

    Uses the forecast CSV's `summary` and `change_reason` fields — no LLM,
    no API calls beyond the already-cached CSV. Follows the article's
    empirical finding that wording precision carries signal:
      - "较大幅度增长" (+70.67%) is weaker than "大幅度增长" (+849%)
      - "商誉减值" in the change_reason is a red flag regardless of type
      - hedging language ("预计将", "取决于") signals hidden uncertainty

    Returns a dict with flagged keywords, or None if no forecast / no text.
    The caller rejects when red flags are present or yellow+hedge >= threshold.
    """
    if not NLP_FORECAST_TEXT_GATE:
        return None

    fc = _load_forecast()
    if fc.empty:
        return None

    from datetime import datetime, timedelta
    ref_dt = datetime.strptime(str(ref_date), '%Y%m%d')
    lo_dt = ref_dt - timedelta(days=V2_FORECAST_LOOKBACK)
    lo_s = lo_dt.strftime('%Y%m%d')

    sub = fc[(fc['code6'] == code6) & (fc['_d'] >= lo_s) & (fc['_d'] < str(ref_date))]
    if len(sub) == 0:
        return None

    latest = sub.sort_values('_d', ascending=False).iloc[0]
    summary = str(latest.get('summary', '') or '')
    change_reason = str(latest.get('change_reason', '') or '')
    text = (summary + ' ' + change_reason).strip()

    if not text:
        return None

    red_matches = [kw for kw in TEXT_RED_FLAGS if kw in text]
    yellow_matches = [kw for kw in TEXT_YELLOW_FLAGS if kw in text]
    hedge_matches = [kw for kw in TEXT_HEDGE_FLAGS if kw in text]

    has_red = len(red_matches) > 0
    total_flags = len(yellow_matches) + len(hedge_matches)
    should_reject = has_red or total_flags >= NLP_FORECAST_TEXT_THRESHOLD

    return dict(
        has_red=has_red,
        red_matches=red_matches,
        yellow_matches=yellow_matches,
        hedge_matches=hedge_matches,
        total_flags=total_flags,
        should_reject=should_reject,
        summary=summary[:200],
        change_reason=change_reason[:200],
        ann_date=str(latest['_d']),
    )


# ═══════════════════════════════════════════════════════════════════════════
# Main entry point — extends the base flow filter with NLP hooks
# ═══════════════════════════════════════════════════════════════════════════

def apply_longterm_flow_filter(df: pd.DataFrame, date: str) -> pd.DataFrame:
    """NLP-augmented flow filter — wraps base filter + adds NLP hooks.
    
    Pipeline:
      1. Run base flow filter (trend-age cap, forecast gate, LHB screens)
      2. Apply forecast text analysis on surviving picks (keyword-based)
      3. Return the further-filtered DataFrame
    
    NLP_FORECAST_TEXT_GATE defaults ON (keyword v1), so this strategy is
    genuinely different from the base for the same .env config.
    """
    # Step 1: apply base flow filter unchanged
    df = _apply_flow_filter_v2(df, date)

    # Step 2: forecast text analysis (keyword-based, no LLM)
    if not NLP_FORECAST_TEXT_GATE or df is None or df.empty:
        return df

    before = len(df)
    _kept = []
    removed = 0
    for _, row in df.iterrows():
        code6 = str(row['ts_code']).split('.')[0].zfill(6)
        result = _forecast_text_analysis(code6, date)
        if result is None:  # no forecast text → neutral, keep
            _kept.append(row)
            continue
        if result['should_reject']:
            removed += 1
            logger.debug(
                f"[NLP-text] rejected {row['ts_code']} "
                f"red={result['red_matches']} yellow={result['yellow_matches']} "
                f"hedge={result['hedge_matches']}"
            )
            continue
        _kept.append(row)

    if removed:
        logger.info(
            f"[NLP-text] keyword gate (threshold={NLP_FORECAST_TEXT_THRESHOLD}): "
            f"{before} -> {len(_kept)} ({removed} removed)"
        )

    if not _kept:
        return pd.DataFrame()
    return pd.DataFrame(_kept).reset_index(drop=True)


# ── Direct invocation (for testing / standalone use) ──────────────────────
if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if len(sys.argv) < 2:
        print("Usage: python ts_7AZ_96MA_flow_longterm.py YYYYMMDD")
        sys.exit(1)
    date = sys.argv[1]
    print(f"Long-term flow filter — {date} (all NLP gates OFF)")
    # Would need df from CANSLIM picker; for now, print status
    print(f"  NLP_SENTIMENT_GATE:     {NLP_SENTIMENT_GATE}")
    print(f"  NLP_IRM_EVASION_GATE:   {NLP_IRM_EVASION_GATE}")
    print(f"  NLP_FORECAST_TEXT_GATE: {NLP_FORECAST_TEXT_GATE}")
    print("  Ready: skeleton loaded, all gates default OFF.")
