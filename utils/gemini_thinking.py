"""
Google Gemini with Thinking Mode Support for DroidRun.
Provides GoogleGenAI configuration with native thinking mode support.
Uses free tier API key (GEMINI_API_KEY) by default.
"""

import logging
import os
from typing import Optional

from google.genai import types
from llama_index.llms.google_genai import GoogleGenAI

logger = logging.getLogger("droidrun")

# ── Free Gemini API (gemini-3.1-flash-lite) ──
# Get API key from .env: GEMINI_API_KEY=***
# https://aistudio.google.com/app/apikey
# Paid models (commented): gemini-2.5-flash, gemini-2.5-pro, gpt-4o


def create_gemini_with_thinking(
    thinking_budget: int = -1,
    model: str = "gemini-2.5-flash",
    api_key: Optional[str] = None,
    **kwargs
) -> GoogleGenAI:
    """
    Create GoogleGenAI with native thinking mode support.
    Default: gemini-3.1-flash-lite (free tier).

    Args:
        thinking_budget: Budget for thinking tokens (-1 = unlimited, 0 = disabled)
        model: Gemini model to use
        api_key: Google API key (uses GEMINI_API_KEY env var if not provided)
        **kwargs: Additional arguments passed to GoogleGenAI

    Returns:
        Configured GoogleGenAI instance with thinking mode
    """
    if api_key is None:
        api_key = os.getenv('GEMINI_API_KEY')

    thinking_config = types.ThinkingConfig(thinking_budget=thinking_budget)

    generation_config = types.GenerateContentConfig(
        thinking_config=thinking_config
    )

    return GoogleGenAI(
        model=model,
        api_key=api_key,
        generation_config=generation_config,
        **kwargs
    )


def analyze_stock_with_gemini(
    stock_info: dict,
    news_context: str,
    model_name: str = "gemini-3.1-flash-lite",
    market_regime: str = "normal",
    hot_sectors: str = ""
) -> dict:
    """
    Stock analysis using free Gemini API for backtest AI strategies.
    Used by ts_ai_pick.py and ts_daily.py.
    """
    import json
    from google import genai
    from google.genai import types as genai_types

    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables.")

    client = genai.Client(api_key=api_key)

    regime_warning = ""
    if market_regime == 'bear':
        regime_warning = "⚠️ 熊市环境 - 保守评估，只推荐防御型股票"
    elif market_regime == 'volatile':
        regime_warning = "震荡市 - 控制仓位，避免追涨杀跌"

    hot_sector_section = f"{hot_sectors}\n股票所属行业与热门板块相关则加分" if hot_sectors else ""

    prompt = f"""你是一个专业的A股短期动量交易分析师。请分析以下股票并给出评分和建议。
{regime_warning}
{hot_sector_section}

## 股票信息
- 代码: {stock_info.get('ts_code', 'N/A')}
- 名称: {stock_info.get('name', 'N/A')}
- 当前价格: {stock_info.get('close', 'N/A')}
- 涨跌幅: {stock_info.get('pct_chg', 'N/A')}%

## 最新消息
{news_context}

## 输出格式 (严格JSON，不要有其他内容)
```json
{{"score": <0-100整数>, "recommendation": "<买入|观望|卖出>", "summary": "<一句话结论>"}}
```"""

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=genai_types.GenerateContentConfig(temperature=0.0, max_output_tokens=1024)
    )

    response_text = response.text
    if "```json" in response_text:
        json_str = response_text.split("```json")[1].split("```")[0].strip()
    elif "```" in response_text:
        json_str = response_text.split("```")[1].split("```")[0].strip()
    else:
        json_str = response_text.strip()

    try:
        result = json.loads(json_str)
        score = max(0, min(100, int(result.get("score", 50))))
        rec = result.get("recommendation", "观望")
        if rec not in ["买入", "观望", "卖出"]:
            rec = "观望"
        return {"score": score, "recommendation": rec, "summary": str(result.get("summary", ""))[:100]}
    except Exception:
        return {"score": 50, "recommendation": "观望", "summary": "解析失败"}
