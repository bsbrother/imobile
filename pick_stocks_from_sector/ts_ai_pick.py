"""
AI Stock Picker using Gemini API, Tushare data, and News integration.

This module implements an AI-powered stock picker (ts_ai) that:
1. Fetches stock candidates using Tushare API
2. Gathers news context using Tavily/SerpAPI
3. Uses Gemini AI to analyze and score each stock
4. Outputs top picks in the standard format for backtest_orders.py

Usage:
    python pick_stocks_from_sector/ts_ai_pick.py <target_date> [--lookahead]
    
Output:
    Writes JSON to /tmp/tmp with selected_stocks array
"""

import os
import sys
import json
import time
import argparse
import hashlib
import sqlite3
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

from dotenv import load_dotenv
from loguru import logger

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import data_provider
from backtest.utils.trading_calendar import get_trading_days_before
from backtest.utils.market_regime import detect_market_regime
from pick_stocks_from_sector.ts_ths_dc import is_late_trend

load_dotenv()

# API Configuration from .env
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TAVILY_API_KEYS = os.getenv("TAVILY_API_KEYS", "").split(",") if os.getenv("TAVILY_API_KEYS") else []
SERPAPI_API_KEYS = os.getenv("SERPAPI_API_KEYS", "").split(",") if os.getenv("SERPAPI_API_KEYS") else []
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

# Initialize Tushare
import tushare as ts
PRO = ts.pro_api(TUSHARE_TOKEN) if TUSHARE_TOKEN else None

# Configuration
MAX_CANDIDATES = 50
MAX_PICKS = 15
OUTPUT_FILE = None

# Cache configuration
CACHE_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'db', 'ai_analysis_cache.db')


def get_hot_sectors(target_date: str, top_n: int = 5) -> str:
    """Get top hot sectors for the target date.
    
    Returns:
        String description of today's hot sectors for AI context
    """
    if not PRO:
        return "热门板块数据不可用"
    
    try:
        # Get sector daily data using dc_daily (concept sectors)
        sector_data = PRO.ths_daily(trade_date=target_date)
        
        if sector_data is None or sector_data.empty:
            return "当日无板块数据"
        
        # Sort by pct_change to get top gainers
        if 'pct_change' in sector_data.columns:
            sector_data = sector_data.sort_values('pct_change', ascending=False)
        elif 'pct_chg' in sector_data.columns:
            sector_data = sector_data.sort_values('pct_chg', ascending=False)
        
        top_sectors = sector_data.head(top_n)
        
        # Get sector names
        concept_list = PRO.ths_index(exchange='A', type='N')
        sector_names = dict(zip(concept_list['ts_code'], concept_list['name']))
        
        results = ["## 当日热门板块 (涨幅前5):"]
        for _, row in top_sectors.iterrows():
            name = sector_names.get(row['ts_code'], row['ts_code'])
            pct = row.get('pct_change', row.get('pct_chg', 0))
            results.append(f"- {name}: +{pct:.2f}%")
        
        return "\n".join(results)
    except Exception as e:
        logger.warning(f"Failed to get hot sectors: {e}")
        return "热门板块数据获取失败"


@dataclass
class StockPick:
    """Represents a picked stock with AI analysis."""
    rank: int
    symbol: str
    score: float
    name: str = ""
    ai_summary: str = ""
    

class NewsService:
    """News service with Tavily primary and SerpAPI fallback."""
    
    def __init__(self):
        self._tavily_client = None
        self._serpapi_key = SERPAPI_API_KEYS[0] if SERPAPI_API_KEYS and SERPAPI_API_KEYS[0] else None
        self._tavily_exhausted = False
        self._init_tavily()
    
    def _init_tavily(self):
        """Initialize Tavily client if API key is available."""
        if not TAVILY_API_KEYS or not TAVILY_API_KEYS[0]:
            logger.warning("No Tavily API key configured")
            return
            
        try:
            from tavily import TavilyClient
            self._tavily_client = TavilyClient(api_key=TAVILY_API_KEYS[0])
            logger.info("Tavily client initialized")
        except ImportError:
            logger.warning("Tavily package not installed")
        except Exception as e:
            logger.warning(f"Failed to initialize Tavily: {e}")
    
    def _search_serpapi(self, query: str, max_results: int = 3) -> str:
        """Fallback search using SerpAPI."""
        if not self._serpapi_key:
            return "SerpAPI未配置"
        
        try:
            import requests
            params = {
                'q': query,
                'api_key': self._serpapi_key,
                'engine': 'google',
                'num': max_results,
                'hl': 'zh-cn',
                'gl': 'cn'
            }
            response = requests.get('https://serpapi.com/search', params=params, timeout=10)
            data = response.json()
            
            results = []
            for r in data.get('organic_results', [])[:max_results]:
                title = r.get('title', '')
                snippet = r.get('snippet', '')[:200]
                results.append(f"- {title}: {snippet}")
            
            return "\n".join(results) if results else "无相关新闻"
        except Exception as e:
            logger.warning(f"SerpAPI search failed: {e}")
            return "新闻搜索不可用"
    
    def search(self, stock_name: str, stock_code: str, target_date: str = '', max_results: int = 3) -> str:
        """Search for news, with Tavily primary and SerpAPI fallback.
        
        Args:
            stock_name: Stock name (e.g., '贵州茅台')
            stock_code: Stock code (e.g., '600519.SH')
            target_date: Target date in YYYYMMDD format for historical context
            max_results: Maximum number of news results
        """
        # Format date for search
        date_str = ''
        if target_date:
            try:
                from datetime import datetime
                dt = datetime.strptime(target_date, '%Y%m%d')
                date_str = dt.strftime('%Y年%m月')
            except:
                pass
        
        query = f"{stock_name} {stock_code.split('.')[0]} 股票 {date_str} 最新消息"
        
        # Try Tavily first (unless exhausted)
        if self._tavily_client and not self._tavily_exhausted:
            try:
                response = self._tavily_client.search(
                    query=query,
                    search_depth="basic",
                    max_results=max_results,
                    include_answer=True
                )
                
                results = []
                if response.get("answer"):
                    results.append(f"摘要: {response['answer']}")
                
                for r in response.get("results", [])[:max_results]:
                    title = r.get("title", "")
                    content = r.get("content", "")[:200]
                    results.append(f"- {title}: {content}")
                
                return "\n".join(results) if results else "无相关新闻"
            except Exception as e:
                error_msg = str(e).lower()
                # Check if rate limit exceeded
                if 'limit' in error_msg or 'quota' in error_msg or 'exceeded' in error_msg or '429' in error_msg:
                    logger.warning(f"Tavily limit exceeded, switching to SerpAPI: {e}")
                    self._tavily_exhausted = True
                else:
                    logger.warning(f"Tavily search failed: {e}")
        
        # Fallback to SerpAPI
        if self._serpapi_key:
            logger.info(f"Using SerpAPI fallback for {stock_name}")
            return self._search_serpapi(query, max_results)
        
        return "新闻搜索未配置"


class AIAnalysisCache:
    """SQLite cache for AI analysis results to ensure deterministic backtests."""
    
    def __init__(self, db_path: str = CACHE_DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database and table."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_analysis_cache (
                    cache_key TEXT PRIMARY KEY,
                    ts_code TEXT,
                    target_date TEXT,
                    score INTEGER,
                    recommendation TEXT,
                    summary TEXT,
                    created_at TEXT
                )
            """)
            conn.commit()
    
    def _make_key(self, ts_code: str, target_date: str, market_regime: str) -> str:
        """Create a unique cache key."""
        key_str = f"{ts_code}_{target_date}_{market_regime}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get(self, ts_code: str, target_date: str, market_regime: str) -> Optional[Dict[str, Any]]:
        """Get cached analysis result."""
        cache_key = self._make_key(ts_code, target_date, market_regime)
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT score, recommendation, summary FROM ai_analysis_cache WHERE cache_key = ?",
                    (cache_key,)
                )
                row = cursor.fetchone()
                if row:
                    logger.debug(f"Cache hit for {ts_code} on {target_date}")
                    return {
                        "score": row[0],
                        "recommendation": row[1],
                        "summary": row[2]
                    }
        except Exception as e:
            logger.warning(f"Cache read error: {e}")
        return None
    
    def set(self, ts_code: str, target_date: str, market_regime: str, analysis: Dict[str, Any]):
        """Store analysis result in cache."""
        cache_key = self._make_key(ts_code, target_date, market_regime)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO ai_analysis_cache 
                    (cache_key, ts_code, target_date, score, recommendation, summary, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    cache_key, ts_code, target_date,
                    analysis['score'], analysis['recommendation'], analysis['summary'],
                    datetime.now().isoformat()
                ))
                conn.commit()
        except Exception as e:
            logger.warning(f"Cache write error: {e}")


class GeminiStockAnalyzer:
    """Gemini AI analyzer for stock picking with caching for deterministic results."""
    
    def __init__(self):
        self._model = None
        self._cache = AIAnalysisCache()
        self._init_model()
    
    def _init_model(self):
        """Initialize Gemini model with temperature=0 for deterministic output."""
        if not GEMINI_API_KEY:
            logger.error("No Gemini API key configured")
            return
            
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            
            self._model = genai.GenerativeModel(
                model_name=GEMINI_MODEL,
                generation_config={
                    "temperature": 0.0,  # Deterministic output
                    "max_output_tokens": 64000,
                }
            )
            logger.info(f"Gemini model initialized (deterministic): {GEMINI_MODEL}")
        except ImportError:
            logger.error("google-generativeai package not installed")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini: {e}")
    
    def is_available(self) -> bool:
        return self._model is not None
    
    def analyze(self, stock_info: Dict[str, Any], news_context: str, market_regime: str = 'normal', hot_sectors: str = '', target_date: str = '') -> Dict[str, Any]:
        """
        Analyze a stock using Gemini AI with caching for deterministic results.
        
        Returns:
            Dict with 'score' (0-100), 'recommendation' (买入/观望/卖出), 'summary'
        """
        ts_code = stock_info.get('ts_code', '')
        
        # Check cache first for deterministic results
        cached = self._cache.get(ts_code, target_date, market_regime)
        if cached:
            logger.info(f"Using cached AI analysis for {ts_code}")
            return cached
        
        if not self.is_available():
            return {"score": 50, "recommendation": "观望", "summary": "AI分析不可用"}
        
        prompt = self._build_prompt(stock_info, news_context, market_regime, hot_sectors)
        
        try:
            response = self._model.generate_content(prompt)
            result = self._parse_response(response.text)
            
            # Cache the result for future runs
            self._cache.set(ts_code, target_date, market_regime, result)
            
            return result
        except Exception as e:
            logger.error(f"Gemini analysis failed: {e}")
            return {"score": 50, "recommendation": "观望", "summary": f"分析失败: {str(e)[:50]}"}
    
    def _build_prompt(self, stock_info: Dict[str, Any], news_context: str, market_regime: str = 'normal', hot_sectors: str = '') -> str:
        """Build analysis prompt for Gemini."""
        # Bear market warning
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
        
        # Add hot sector context
        hot_sector_section = ""
        if hot_sectors:
            hot_sector_section = f"""
{hot_sectors}

> 如果股票所属行业与热门板块相关,应适当加分; 如果行业走弱则减分。
"""
        
        return f"""你是一个专业的A股短期动量交易分析师。请分析以下股票并给出评分和建议。
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
1. 动量趋势 - 价格和成交量的趋势是否向上
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
    
    def _parse_response(self, response_text: str) -> Dict[str, Any]:
        """Parse Gemini response to extract score and recommendation."""
        try:
            # Extract JSON from markdown code block if present
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
            logger.warning(f"Failed to parse Gemini response: {e}")
            return {"score": 50, "recommendation": "观望", "summary": "解析失败"}


def get_stock_candidates(target_date: str, lookahead: bool = False, market_regime: str = 'normal') -> List[Dict[str, Any]]:
    """
    Get stock candidates for AI analysis using BULK daily fetch.
    
    This is optimized to fetch ALL stocks per date in ONE API call instead of per-stock calls.
    
    Args:
        target_date: Target trading date (YYYYMMDD)
        lookahead: If True, use target date data; if False, use previous day
        market_regime: Market regime (bear/bull/normal/volatile)
        
    Returns:
        List of stock candidates with OHLCV data
    """
    # Get all listed stocks
    stock_basic = data_provider.get_basic_information_api()
    if stock_basic.empty:
        logger.error("Failed to fetch stock basic info")
        return []
    
    # Filter using unified pick_filter from config.json (shared with all strategies)
    from pick_stocks_from_sector.ts_ths_dc import no_risky_stocks
    risky_free_list = no_risky_stocks(stock_basic)
    stock_basic = stock_basic[stock_basic['ts_code'].isin(risky_free_list)].reset_index(drop=True)
    
    # In bear market, we might want to be even stricter, but all risky stocks now filtered by config
    if market_regime == 'bear':
        logger.info("Bear market: extra caution applied in scoring")
    
    # Build valid stock set for quick lookup
    valid_stocks = set(stock_basic['ts_code'].tolist())
    stock_info_map = {row['ts_code']: row for _, row in stock_basic.iterrows()}
    
    logger.info(f"Filtered to {len(stock_basic)} mainboard stocks")
    
    # Determine data date
    if lookahead:
        data_date = target_date
    else:
        data_date = get_trading_days_before(target_date, 1)
    
    start_date = get_trading_days_before(data_date, 5)
    logger.info(f"Fetching BULK data from {start_date} to {data_date}")
    
    # === BULK FETCH: Get all stocks' data for each date in ONE API call ===
    from backtest.utils.trading_calendar import get_trading_days_between
    trading_dates = get_trading_days_between(start_date, data_date)

    
    # Fetch bulk data for each date (with caching)
    all_daily_data = {}  # date -> DataFrame
    for date in trading_dates:
        df = data_provider.get_bulk_daily_by_date(date)
        if df is not None and not df.empty:
            all_daily_data[date] = df
        time.sleep(0.2)  # Small delay between API calls
    
    logger.info(f"Fetched bulk data for {len(all_daily_data)} trading dates")
    
    # Build per-stock historical data from bulk data
    stock_history = {}  # ts_code -> list of daily records
    for date in trading_dates:
        if date not in all_daily_data:
            continue
        df = all_daily_data[date]
        for _, row in df.iterrows():
            ts_code = row['ts_code']
            if ts_code in valid_stocks:
                if ts_code not in stock_history:
                    stock_history[ts_code] = []
                stock_history[ts_code].append(row.to_dict())
    
    logger.info(f"Built history for {len(stock_history)} stocks")
    
    # Filter stocks based on criteria
    candidates = []
    
    for ts_code, history in stock_history.items():
        if len(history) < 3:
            continue
        
        # Sort by trade_date descending
        history.sort(key=lambda x: x['trade_date'], reverse=True)
        latest = history[0]
        
        # Regime-aware filter criteria
        pct_chg = float(latest.get('pct_chg', 0) or 0)
        if market_regime == 'bear':
            if pct_chg < 1.0 or pct_chg > 5.0:
                continue
        elif market_regime == 'volatile':
            if pct_chg < 1.5 or pct_chg > 6.0:
                continue
        else:  # normal or bull
            if pct_chg < 2.0 or pct_chg > 9.5:
                continue
        
        # Calculate 3-day return
        if len(history) >= 4:
            return_3d = (float(latest['close']) / float(history[3]['close']) - 1) * 100
        else:
            return_3d = pct_chg
        
        # Calculate volume ratio (vs 5-day average)
        if len(history) >= 5:
            avg_vol = sum(float(h['vol'] or 0) for h in history[1:5]) / 4
            volume_ratio = float(latest['vol'] or 0) / avg_vol if avg_vol > 0 else 1.0
        else:
            volume_ratio = 1.0
        
        # Filter: volume_ratio > 1.0
        if volume_ratio < 1.0:
            continue
        
        stock_info = stock_info_map.get(ts_code, {})
        candidates.append({
            'ts_code': ts_code,
            'name': stock_info.get('name', ''),
            'industry': stock_info.get('industry', ''),
            'close': float(latest['close']),
            'pct_chg': pct_chg,
            'vol': float(latest['vol'] or 0),
            'return_3d': round(return_3d, 2),
            'volume_ratio': round(volume_ratio, 2),
            'turnover_rate': 0,  # Skip for speed
        })
        
        # Limit candidates
        if len(candidates) >= MAX_CANDIDATES:
            break
    
    # Sort by return_3d and take top candidates
    candidates.sort(key=lambda x: x['return_3d'], reverse=True)
    candidates = candidates[:MAX_CANDIDATES]
    
    logger.info(f"Found {len(candidates)} stock candidates")
    return candidates




def pick_stocks(target_date: str, lookahead: bool = False) -> List[StockPick]:
    """
    Main stock picking logic using AI analysis.
    
    Args:
        target_date: Target trading date (YYYYMMDD)
        lookahead: Whether to use lookahead data
        
    Returns:
        List of StockPick objects
    """
    logger.info(f"=== AI Stock Picker (ts_ai) ===")
    logger.info(f"Target date: {target_date}, Lookahead: {lookahead}")
    
    # Detect market regime
    regime_data = detect_market_regime(target_date)
    market_regime = regime_data.get('regime', 'normal')
    logger.info(f"Market Regime: {market_regime}")
    
    # Get candidates with regime awareness
    candidates = get_stock_candidates(target_date, lookahead, market_regime)
    if not candidates:
        logger.warning("No candidates found")
        return []
    
    # === Late-trend filter moved to backtest_orders.py centrally ===
    
    # Initialize services
    news_service = NewsService()
    analyzer = GeminiStockAnalyzer()
    
    # Get hot sectors for the target date
    hot_sectors_context = get_hot_sectors(target_date)
    logger.info(f"Hot Sectors: {hot_sectors_context[:100]}...")
    
    if not analyzer.is_available():
        logger.error("Gemini analyzer not available, using fallback scoring")
        # Fallback: use momentum-based scoring
        picks = []
        for i, c in enumerate(candidates[:MAX_PICKS]):
            score = 50 + min(30, c['return_3d'] * 5) + min(20, c['volume_ratio'] * 5)
            picks.append(StockPick(
                rank=i + 1,
                symbol=c['ts_code'],
                score=round(score, 1),
                name=c['name'],
                ai_summary="AI不可用,使用动量评分"
            ))
        return picks
    
    # Analyze top candidates with AI
    analyzed_stocks = []
    
    for stock in candidates[:20]:  # Analyze top 20 for efficiency
        ts_code = stock['ts_code']
        
        # Check AI cache FIRST before any expensive operations
        cached = analyzer._cache.get(ts_code, target_date, market_regime)
        
        if cached:
            # Fast path: use cached result, skip news fetch and delay
            logger.info(f"[CACHED] {stock['name']} ({ts_code}): score={cached['score']}")
            analyzed_stocks.append({
                **stock,
                'ai_score': cached['score'],
                'recommendation': cached['recommendation'],
                'ai_summary': cached['summary']
            })
            continue
        
        # Slow path: need to fetch news and call AI
        logger.info(f"Analyzing {stock['name']} ({ts_code})...")
        
        # Fetch news with date context for historical accuracy
        news_context = news_service.search(stock['name'], ts_code, target_date)
        
        # AI analysis with regime and hot sector context
        analysis = analyzer.analyze(stock, news_context, market_regime, hot_sectors_context, target_date)
        
        analyzed_stocks.append({
            **stock,
            'ai_score': analysis['score'],
            'recommendation': analysis['recommendation'],
            'ai_summary': analysis['summary']
        })
        
        # Rate limiting delay ONLY for actual API calls
        time.sleep(1.0)
    
    # Filter out "卖出" recommendations and apply score threshold
    # In bear market, require higher confidence (score >= 55)
    min_score = 55 if market_regime == 'bear' else 50
    buy_candidates = [
        s for s in analyzed_stocks 
        if s['recommendation'] in ['买入', '观望'] and s['ai_score'] >= min_score
    ]
    
    # If no high-confidence picks in bear market, skip the day entirely
    if not buy_candidates:
        if market_regime == 'bear':
            logger.warning(f"No stocks with score >= {min_score} in bear market - skipping day for safety")
        else:
            logger.warning(f"No stocks with score >= {min_score} - skipping day")
        return []
    
    # Sort by AI score
    buy_candidates.sort(key=lambda x: x['ai_score'], reverse=True)
    
    # Take top picks
    picks = []
    for i, stock in enumerate(buy_candidates[:MAX_PICKS]):
        picks.append(StockPick(
            rank=i + 1,
            symbol=stock['ts_code'],
            score=stock['ai_score'],
            name=stock['name'],
            ai_summary=stock['ai_summary']
        ))
    
    logger.info(f"Selected {len(picks)} stocks (min score: {min_score})")
    return picks


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='AI Stock Picker (ts_ai)')
    parser.add_argument('date', help='Target trading date (YYYYMMDD)')
    parser.add_argument('--lookahead', action='store_true', help='Use lookahead data')
    parser.add_argument('--output', default=OUTPUT_FILE, help='Output file path')
    args = parser.parse_args()
    
    if not args.output:
        args.output = '/tmp/tmp'
    
    # Validate date
    try:
        datetime.strptime(args.date, '%Y%m%d')
    except ValueError:
        logger.error(f"Invalid date format: {args.date}, expected YYYYMMDD")
        sys.exit(1)
    
    # Pick stocks
    picks = pick_stocks(args.date, args.lookahead)
    
    # Format output
    output = {
        "selected_stocks": [
            {
                "rank": p.rank,
                "symbol": p.symbol,
                "score": p.score,
                "name": p.name
            }
            for p in picks
        ]
    }
    
    # Write to output file
    with open(args.output, 'w') as f:
        json.dump(output, f)
    
    logger.info(f"Output saved to {args.output}")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
