#!/usr/bin/env python3
"""
Test file to verify that the Gemini API workflow from the backtesting system
can run on the newest gemini-3.5-flash model.
"""
import os
import json
import dotenv
from typing import Dict, Any, List

# Load environment variables
dotenv.load_dotenv(os.path.expanduser('.env'), verbose=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def analyze_stock_with_gemini(
    stock_info: Dict[str, Any],
    news_context: str,
    model_name: str = "gemini-3.5-flash",
    market_regime: str = "normal",
    hot_sectors: str = ""
) -> Dict[str, Any]:
    """
    Simulates the same stock analysis function used in the backtest workflow (ts_ai_pick.py / ts_daily.py).
    Queries the Google Gemini API with the specified model.
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not found in environment variables.")

    from google import genai
    from google.genai import types

    # Initialize client
    client = genai.Client(api_key=GEMINI_API_KEY)

    # Replicate the prompt construction from the backtest system (ts_ai_pick.py)
    regime_warning = ""
    if market_regime == 'bear':
        regime_warning = """
## ⚠️ 重要提示: 当前为熊市环境
- 必须更加保守,宁可错过也不追高
- 只推荐真正优质的防御型股票
- 涨幅过大的股票风险极高,应该评为"卖出"
- 评分应明显低于牛市标准
"""
    elif market_regime == 'volatile':
        regime_warning = """
## 注意: 当前为震荡市
- 控制仓位,选择走势稳定的股票
- 避免追涨杀跌
"""

    hot_sector_section = ""
    if hot_sectors:
        hot_sector_section = f"""
{hot_sectors}

> 如果股票所属行业与热门板块相关,应适当加分; 如果行业走弱则减分。
"""

    prompt = f"""你是一个专业的A股短期动量交易分析师。请分析以下股票并给出评分和建议。
{regime_warning}
{hot_sector_section}

## 股票信息
- 代码: {stock_info.get('ts_code', 'N/A')}
- 名称: {stock_info.get('name', 'N/A')}
- 所属行业: {stock_info.get('industry', 'N/A')}
- 当前价格: {stock_info.get('close', 'N/A')}
- 涨跌幅: {stock_info.get('pct_chg', 'N/A')}%
- 成交量: {stock_info.get('vol', 'N/A')}
- 换手率: {stock_info.get('turnover_rate', 'N/A')}%
- 3日涨幅: {stock_info.get('return_3d', 'N/A')}%
- 量比: {stock_info.get('volume_ratio', 'N/A')}

## 最新消息
{news_context}

## 评估要求
请基于以下维度评估该股票的短期（1-5天）投资价值:
1. 动量趋势 - 价格 and 成交量的趋势是否向上
2. 市场热度 - 行业板块是否处于热点
3. 风险控制 - 是否存在明显风险信号
4. 新闻面 - 近期消息是否利好

## 输出格式 (严格JSON)
请只输出以下JSON格式,不要有其他内容:
```json
{{
    "score": <0-100的整数,代表投资价值评分>,
    "recommendation": "<买入|观望|卖出>",
    "summary": "<一句话核心结论,不超过50字>"
}}
```"""

    # Print prompt for debugging
    print(f"\n[Prompt for {model_name}]:\n{prompt}\n")

    # Generate Content using the SDK
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=1024,
        )
    )

    print(f"[Raw Response from {model_name}]:\n{response.text}\n")

    # Parse Response (same parser as ts_ai_pick.py)
    try:
        response_text = response.text
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            json_str = response_text.split("```")[1].split("```")[0].strip()
        else:
            json_str = response_text.strip()

        result = json.loads(json_str)

        # Validate and normalize
        score = max(0, min(100, int(result.get("score", 50))))
        recommendation = result.get("recommendation", "观望")
        if recommendation not in ["买入", "观望", "卖出"]:
            recommendation = "观望"
        summary = str(result.get("summary", ""))[:100]

        return {
            "score": score,
            "recommendation": recommendation,
            "summary": summary
        }
    except Exception as e:
        print(f"Failed to parse response: {e}")
        return {"score": 50, "recommendation": "观望", "summary": "解析失败", "error": str(e)}

def test_gemini_35_flash_analysis():
    """
    Test function that validates stock analysis using gemini-3.5-flash.
    """
    mock_stock = {
        "ts_code": "600519.SH",
        "name": "贵州茅台",
        "industry": "白酒",
        "close": 1850.5,
        "pct_chg": 2.5,
        "vol": 12500,
        "turnover_rate": 0.8,
        "return_3d": 1.2,
        "volume_ratio": 1.1
    }
    mock_news = """
    【贵州茅台：一季度净利润同比增长15.2%】贵州茅台公布最新季报，公司营收和净利润稳步上升，行业龙头地位稳固。白酒行业近期受到资金关注，北向资金持续流入。
    """

    print("=== Testing Stock Analysis with 'gemini-3.5-flash' ===")
    result = analyze_stock_with_gemini(
        stock_info=mock_stock,
        news_context=mock_news,
        model_name="gemini-3.5-flash"
    )

    print("Parsed result:", result)
    assert "score" in result, "Result must contain 'score'"
    assert "recommendation" in result, "Result must contain 'recommendation'"
    assert "summary" in result, "Result must contain 'summary'"
    assert isinstance(result["score"], int), "Score must be an integer"
    assert result["recommendation"] in ["买入", "观望", "卖出"], "Recommendation must be standard"
    print("✅ test_gemini_35_flash_analysis passed successfully!")

if __name__ == "__main__":
    test_gemini_35_flash_analysis()

