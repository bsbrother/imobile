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

STATUS: SKELETON — all NLP hooks are placeholders. The strategy imports
        and runs the base flow filter unchanged (gate OFF by default).
        Fill in the placeholder functions as the NLP pipeline matures.

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

from backtest.strategies.ts_7AZ_96MA_flow_v2 import (
    _apply_flow_filter_v2,  # base flow filter
    _load_forecast, _forecast_is_bad,
    V2_FORECAST_GATE,
)

logger = logging.getLogger(__name__)

# ── NLP Gates (env, all OFF — zero change from base) ───────────────────────
NLP_SENTIMENT_GATE = os.getenv('NLP_SENTIMENT_GATE', 'false').lower() in ('true', '1', 'yes')
NLP_IRM_EVASION_GATE = os.getenv('NLP_IRM_EVASION_GATE', 'false').lower() in ('true', '1', 'yes')
NLP_FORECAST_TEXT_GATE = os.getenv('NLP_FORECAST_TEXT_GATE', 'false').lower() in ('true', '1', 'yes')
NLP_MODEL_CACHE_DIR = os.getenv('NLP_MODEL_CACHE_DIR', '')

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
    """Placeholder: 业绩预告 full-text NLP analysis (the article's core pipeline).
    
    Uses the forecast endpoint's `summary` and `change_reason` text fields
    (available in the existing forecast_2026.csv). Follows the article's
    "third-generation" architecture:
      1. LLM reads and expands (5 perspectives: title, catalyst, subtext,
         risks, guidance)
      2. Fine-tuned small model judges each perspective
      3. Combines into a signal
    
    When implemented, returns:
      - negative_signal: bool  (True = refuse this pick)
      - perspectives: list of (label, text) pairs
      - confidence: 0-1
    
    Text fields available:
      - summary: "业绩预告摘要" (forecast summary — e.g., "预计2026年1-6月
        归属于上市公司股东的净利润盈利:3,000万元至5,000万元,同比上年增长:50%
        至80%")
      - change_reason: "业绩变动原因" (reason for change — qualitative text like
        "主要系本报告期公司主营业务收入增加,产品结构调整,毛利率提升所致")
    """
    if not NLP_FORECAST_TEXT_GATE:
        return None
    # TODO: load forecast cache, find matching record for code6+date
    # TODO: extract summary and change_reason
    # TODO: LLM expand (5 perspectives) via API call
    # TODO: small model judge → negative_signal flag
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Main entry point — extends the base flow filter with NLP hooks
# ═══════════════════════════════════════════════════════════════════════════

def apply_longterm_flow_filter(df: pd.DataFrame, date: str) -> pd.DataFrame:
    """NLP-augmented flow filter — wraps base filter + adds NLP hooks.
    
    Currently behaves IDENTICALLY to _apply_flow_filter_v2 (all NLP gates OFF).
    When NLP hooks are filled in and env gates enabled, this will:
      1. Run base flow filter (trend-age cap, forecast gate, LHB screens)
      2. Then apply NLP sentiment checks on surviving picks
      3. Return the further-filtered DataFrame
    """
    # Step 1: apply base flow filter unchanged
    df = _apply_flow_filter_v2(df, date)

    # Step 2: NLP hooks (all skip when gates are OFF)
    if not any([NLP_SENTIMENT_GATE, NLP_IRM_EVASION_GATE, NLP_FORECAST_TEXT_GATE]):
        return df  # fast path — zero overhead

    # TODO: for each surviving pick, run NLP checks and filter
    # For now, return unchanged
    return df


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
