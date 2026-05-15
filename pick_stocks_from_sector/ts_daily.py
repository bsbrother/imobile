"""
Daily News-Driven AI Stock Picker (ts_daily)

This module implements an AI-powered stock picker (ts_daily) that:
1. Fetches stock candidates using Tushare API
2. Gathers historical news context for the specific trading date
3. Uses Gemini AI to analyze and score each stock heavily based on daily news
4. Outputs top picks in the standard format for backtest_orders.py

Usage:
    python pick_stocks_from_sector/ts_daily.py <target_date> [--lookahead]
"""

import os
import sys
import json
import time
import argparse
import hashlib
import sqlite3
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime

from dotenv import load_dotenv
from loguru import logger

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import data_provider
from backtest.utils.trading_calendar import get_trading_days_before
from backtest.utils.market_regime import detect_market_regime
from backtest.analysis.indicators import TechnicalIndicators

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

# Cache configuration - Use a dedicated cache for ts_daily to avoid collisions with ts_ai
CACHE_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'db', 'ts_daily_cache.db')


def get_hot_sectors(target_date: str, top_n: int = 5) -> str:
    """Get top hot sectors for the target date."""
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


def get_market_dashboard(target_date: str) -> str:
    """Get macro market dashboard for the target date."""
    try:
        # 1. Get market stats (A/D ratio) from bulk daily data
        df_bulk = data_provider.get_bulk_daily_by_date(target_date)
        if df_bulk.empty:
            # Try previous day
            prev_date = get_trading_days_before(target_date, 1)
            df_bulk = data_provider.get_bulk_daily_by_date(prev_date)
        
        stats_str = "大盘数据不可用"
        if not df_bulk.empty:
            up_count = len(df_bulk[df_bulk['pct_chg'] > 0])
            down_count = len(df_bulk[df_bulk['pct_chg'] < 0])
            flat_count = len(df_bulk[df_bulk['pct_chg'] == 0])
            limit_up_count = len(df_bulk[df_bulk['pct_chg'] >= 9.8])
            total_amount = df_bulk['amount'].sum() / 100000  # Convert to 100M
            
            stats_str = (
                f"- 上涨: {up_count} | 下跌: {down_count} | 平盘: {flat_count} | 涨停: {limit_up_count}\n"
                f"- 成交额: {total_amount:.0f}亿"
            )
        
        # 2. Get Index trends (SSE & CSI300)
        indices = ['000001.SH', '000300.SH']
        index_str = ""
        for code in indices:
            df_idx = data_provider.get_index_data(code, get_trading_days_before(target_date, 5), target_date)
            if not df_idx.empty:
                latest = df_idx.iloc[-1]
                name = "上证指数" if code == '000001.SH' else "沪深300"
                pct = latest.get('pct_chg', 0)
                index_str += f"- {name}: {pct:+.2f}%\n"
        
        return f"## 市场大盘概况 ({target_date}):\n{stats_str}\n{index_str}"
    except Exception as e:
        logger.warning(f"Failed to get market dashboard: {e}")
        return "市场大盘数据获取失败"


@dataclass
class StockPick:
    """Represents a picked stock with AI analysis."""
    rank: int
    symbol: str
    score: float
    name: str = ""
    ai_summary: str = ""
    tp_pct: float = 0.10
    sl_pct: float = 0.05


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
    
    def search(self, stock_name: str, stock_code: str, target_date: str = '', max_results: int = 5) -> str:
        """Search for news, targeting the exact date for historical testing."""
        # Format exact dates for search query to be very specific to the target date
        date_str = ''
        if target_date:
            try:
                dt = datetime.strptime(target_date, '%Y%m%d')
                # Use a specific format that search engines index well for stock news
                date_str = dt.strftime('%Y年%m月%d日')
            except:
                pass
        
        # Explicitly ask for news on that exact date
        query = f"{stock_name} {stock_code.split('.')[0]} 股票 {date_str} 最新消息 利好"
        
        if self._tavily_client and not self._tavily_exhausted:
            try:
                response = self._tavily_client.search(
                    query=query,
                    search_depth="advanced", # use advanced for more exhaustive historical search
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
                if 'limit' in error_msg or 'quota' in error_msg or 'exceeded' in error_msg or '429' in error_msg:
                    logger.warning(f"Tavily limit exceeded, switching to SerpAPI: {e}")
                    self._tavily_exhausted = True
                else:
                    logger.warning(f"Tavily search failed: {e}")
        
        if self._serpapi_key:
            return self._search_serpapi(query, max_results)
        
        return "新闻搜索未配置"


class DailyAnalysisCache:
    """SQLite cache for Daily AI analysis results."""
    
    def __init__(self, db_path: str = CACHE_DB_PATH):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ts_daily_cache (
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
        key_str = f"ts_daily_{ts_code}_{target_date}_{market_regime}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get(self, ts_code: str, target_date: str, market_regime: str) -> Optional[Dict[str, Any]]:
        cache_key = self._make_key(ts_code, target_date, market_regime)
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT score, recommendation, summary FROM ts_daily_cache WHERE cache_key = ?",
                    (cache_key,)
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "score": row[0],
                        "recommendation": row[1],
                        "summary": row[2]
                    }
        except Exception as e:
            logger.warning(f"Cache read error: {e}")
        return None
    
    def set(self, ts_code: str, target_date: str, market_regime: str, analysis: Dict[str, Any]):
        cache_key = self._make_key(ts_code, target_date, market_regime)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO ts_daily_cache 
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


class GeminiDailyAnalyzer:
    """Gemini AI analyzer for daily stock picking logic."""
    
    def __init__(self):
        self._model = None
        self._cache = DailyAnalysisCache()
        self._init_model()
    
    def _init_model(self):
        if not GEMINI_API_KEY:
            logger.error("No Gemini API key configured")
            return
            
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            
            self._model = genai.GenerativeModel(
                model_name=GEMINI_MODEL,
                generation_config={
                    "temperature": 0.0,
                    "max_output_tokens": 1024,
                }
            )
            logger.info(f"Gemini model initialized for ts_daily: {GEMINI_MODEL}")
        except ImportError:
            logger.error("google-generativeai package not installed")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini: {e}")
    
    def is_available(self) -> bool:
        return self._model is not None
    
    def analyze(self, stock_info: Dict[str, Any], news_context: str, market_regime: str = 'normal', hot_sectors: str = '', market_dashboard: str = '', target_date: str = '') -> Dict[str, Any]:
        ts_code = stock_info.get('ts_code', '')
        
        cached = self._cache.get(ts_code, target_date, market_regime)
        if cached:
            logger.info(f"Using cached ts_daily analysis for {ts_code}")
            return cached
        
        if not self.is_available():
            return {"score": 50, "recommendation": "观望", "summary": "AI分析不可用", "tp_pct": 0.10, "sl_pct": 0.05}
        
        prompt = self._build_prompt(stock_info, news_context, market_regime, hot_sectors, market_dashboard, target_date)
        
        try:
            response = self._model.generate_content(prompt)
            result = self._parse_response(response.text)
            self._cache.set(ts_code, target_date, market_regime, result)
            return result
        except Exception as e:
            logger.error(f"Gemini analysis failed: {e}")
            return {"score": 50, "recommendation": "观望", "summary": f"分析失败: {str(e)[:50]}", "tp_pct": 0.10, "sl_pct": 0.05}
    
    def _build_prompt(self, stock_info: Dict[str, Any], news_context: str, market_regime: str = 'normal', hot_sectors: str = '', market_dashboard: str = '', target_date: str = '') -> str:
        # Prompt tuned exclusively for DAILY catalysts
        formatted_date = ""
        if target_date:
            try:
                dt = datetime.strptime(target_date, '%Y%m%d')
                formatted_date = dt.strftime('%Y年%m月%d日')
            except:
                formatted_date = target_date
                
        return f"""你是一个专业的A股高级交易策略师。现在的交易日是：{formatted_date}。
请结合【大盘环境】、【板块热度】、【个股微观技术面】和【每日新闻催化剂】，生成一份「决策仪表盘」。

## 1. 大盘环境 (Market Context)
{market_dashboard}
市场周期: {market_regime}

## 2. 热门板块热度 (Hot Sectors)
{hot_sectors}

## 3. 个股微观技术分析 (Micro Technicals)
- 代码: {stock_info.get('ts_code', 'N/A')} ({stock_info.get('name', 'N/A')})
- 行业: {stock_info.get('industry', 'N/A')}
- 价格: {stock_info.get('close', 'N/A')} | 涨跌幅: {stock_info.get('pct_chg', 'N/A')}%
- 均线状态: {stock_info.get('ma_status', 'N/A')}
- 乖离率(MA5): {stock_info.get('bias_ma5', 'N/A')}% (严禁追高：>5%需警惕)
- 量能状态: {stock_info.get('volume_status', 'N/A')} (量比: {stock_info.get('volume_ratio', 'N/A')})
- MACD/RSI: {stock_info.get('macd_rsi_status', 'N/A')}

## 4. 每日新闻/催化剂分析 (Catalyst Analysis)
{news_context}

## 评估与决策要求 (Decision Logic)
1. **核心逻辑**: 寻找“趋势向上 + 强力催化 + 风险可控”的标的。
2. **评分标准 (0-100)**:
   - 85-100: 重大消息利好 + 技术面多头排列 + 缩量回调/放量起步。建议"买入"。
   - 70-84: 消息面一般利好 + 技术面强势。建议"买入"。
   - <70: 消息面平淡或技术面乖离过大(追高风险)。建议"观望"。
3. **动态止盈止损**:
   - 根据个股波动率和催化剂强度，给出合理的 `tp_pct` (止盈比例，如 0.15) 和 `sl_pct` (止损比例，如 0.05)。

请返回严格的JSON格式（不要加 markdown block）:
{{
    "score": 88,
    "recommendation": "买入",
    "summary": "一句话核心结论",
    "tp_pct": 0.15,
    "sl_pct": 0.06,
    "confidence": 0.9
}}"""
    
    def _parse_response(self, response_text: str) -> Dict[str, Any]:
        import re
        try:
            # 1. Try to extract JSON block using regex for better robustness
            json_block_match = re.search(r'```json\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if not json_block_match:
                # Try to find anything that looks like a JSON object {}
                json_block_match = re.search(r'(\{.*?\})', response_text, re.DOTALL)
            
            if json_block_match:
                try:
                    data = json.loads(json_block_match.group(1))
                    return {
                        "score": data.get("score", 60),
                        "recommendation": data.get("recommendation", "观望"),
                        "summary": data.get("summary", data.get("ai_summary", "无摘要")),
                        "tp_pct": data.get("tp_pct", 0.10),
                        "sl_pct": data.get("sl_pct", 0.05)
                    }
                except:
                    pass

            # 2. Comprehensive Regex Fallback
            score_match = re.search(r'"score"\s*:\s*(\d+)', response_text)
            rec_match = re.search(r'"recommendation"\s*:\s*"([^"]+)"', response_text)
            summary_match = re.search(r'"summary"\s*:\s*"([^"]+)"', response_text)
            tp_match = re.search(r'"tp_pct"\s*:\s*([\d.]+)', response_text)
            sl_match = re.search(r'"sl_pct"\s*:\s*([\d.]+)', response_text)
            
            score = 60
            if score_match:
                score = int(score_match.group(1))
            
            recommendation = "观望"
            if rec_match:
                recommendation = rec_match.group(1)
            
            summary = "无摘要"
            if summary_match:
                summary = summary_match.group(1)
            elif "Buy" in response_text or "买入" in response_text:
                summary = "AI推荐买入 (正则兜底)"
            
            tp_pct = 0.10
            if tp_match:
                tp_pct = float(tp_match.group(1))
            
            sl_pct = 0.05
            if sl_match:
                sl_pct = float(sl_match.group(1))
                
            return {
                "score": score,
                "recommendation": recommendation,
                "summary": summary,
                "tp_pct": tp_pct,
                "sl_pct": sl_pct
            }
        except Exception as e:
            logger.warning(f"Failed to parse Gemini response: {e}")
            return {"score": 50, "recommendation": "观望", "summary": "解析失败", "tp_pct": 0.10, "sl_pct": 0.05}


def get_stock_candidates(target_date: str, lookahead: bool = False, market_regime: str = 'normal') -> List[Dict[str, Any]]:
    """Get stock candidates for daily picking."""
    stock_basic = data_provider.get_basic_information_api()
    if stock_basic.empty:
        return []
    
    from pick_stocks_from_sector.ts_ths_dc import no_risky_stocks
    risky_free_list = no_risky_stocks(stock_basic)
    stock_basic = stock_basic[stock_basic['ts_code'].isin(risky_free_list)].reset_index(drop=True)
    
    valid_stocks = set(stock_basic['ts_code'].tolist())
    stock_info_map = {row['ts_code']: row for _, row in stock_basic.iterrows()}
    
    if lookahead:
        data_date = target_date
    else:
        data_date = get_trading_days_before(target_date, 1)
    
    # Increase lookback for MA calculation
    start_date = get_trading_days_before(data_date, 80)
    
    from backtest.utils.trading_calendar import get_trading_days_between
    trading_dates = get_trading_days_between(start_date, data_date)
    
    # We only need bulk daily for the candidates selection (momentum), 
    # but we need individual history for the final candidates technical analysis.
    # To keep it efficient, we use bulk daily for the last 5 days to find momentum.
    momentum_dates = trading_dates[-5:]
    
    all_daily_data = {}
    for date in momentum_dates:
        df = data_provider.get_bulk_daily_by_date(date)
        if df is not None and not df.empty:
            all_daily_data[date] = df
        time.sleep(0.1)
    
    stock_history_5d = {}
    for date in momentum_dates:
        if date not in all_daily_data:
            continue
        df = all_daily_data[date]
        for _, row in df.iterrows():
            ts_code = row['ts_code']
            if ts_code in valid_stocks:
                if ts_code not in stock_history_5d:
                    stock_history_5d[ts_code] = []
                stock_history_5d[ts_code].append(row.to_dict())
    
    candidates = []
    for ts_code, history in stock_history_5d.items():
        if len(history) < 3:
            continue
        
        history.sort(key=lambda x: x['trade_date'], reverse=True)
        latest = history[0]
        
        pct_chg = float(latest.get('pct_chg', 0) or 0)
        # We want stocks with momentum for daily play
        if pct_chg < 3.0 or pct_chg > 10.0:  # Daily strategy needs stronger momentum
            continue
        
        # Calculate volume ratio using the 5-day history we have
        avg_vol = sum(float(h['vol'] or 0) for h in history[1:]) / (len(history) - 1) if len(history) > 1 else 1.0
        volume_ratio = float(latest['vol'] or 0) / avg_vol if avg_vol > 0 else 1.0
            
        if volume_ratio < 1.2:  # Demand higher volume explosion for daily
            continue
            
        stock_info = stock_info_map.get(ts_code, {})
        candidates.append({
            'ts_code': ts_code,
            'name': stock_info.get('name', ''),
            'industry': stock_info.get('industry', ''),
            'close': float(latest['close']),
            'pct_chg': pct_chg,
            'vol': float(latest['vol'] or 0),
            'volume_ratio': round(volume_ratio, 2)
        })
        
        if len(candidates) >= MAX_CANDIDATES * 3: # Keep more in preliminary test
            break
            
    # Now for the actual candidates, we fetch full 80-day history for micro-technicals
    final_candidates = []
    logger.info(f"Refining {len(candidates)} candidates with micro technicals...")
    for stock in candidates[:MAX_CANDIDATES]:
        ts_code = stock['ts_code']
        df = data_provider.get_stock_data(ts_code, start_date, data_date)
        if df is None or df.empty or len(df) < 20:
            continue
        
        close_ser = df['close']
        ma5 = TechnicalIndicators.moving_average(close_ser, 5).iloc[-1]
        ma10 = TechnicalIndicators.moving_average(close_ser, 10).iloc[-1]
        ma20 = TechnicalIndicators.moving_average(close_ser, 20).iloc[-1]
        ma60 = TechnicalIndicators.moving_average(close_ser, 60).iloc[-1] if len(df) >= 60 else ma20
        
        curr_close = close_ser.iloc[-1]
        bias_ma5 = ((curr_close - ma5) / ma5) * 100
        
        # MA Status
        if ma5 > ma10 > ma20:
             ma_status = "多头排列 (Strong Bullish)"
        elif ma5 > ma10:
             ma_status = "短期走强 (Improving)"
        else:
             ma_status = "调整阶段 (Consolidating)"
             
        # Volume Status
        vol_ser = df['vol']
        avg_vol_5d = vol_ser.tail(6).iloc[:-1].mean()
        curr_vol = vol_ser.iloc[-1]
        if curr_vol > avg_vol_5d * 1.5:
            volume_status = "放量 (Volume Spike)"
        elif curr_vol < avg_vol_5d * 0.7:
            volume_status = "缩量 (Shrinking Volume)"
        else:
            volume_status = "平稳 (Steady Volume)"
            
        # MACD / RSI
        rsi = TechnicalIndicators.rsi(close_ser, 14).iloc[-1]
        macd, signal, hist = TechnicalIndicators.macd(close_ser)
        macd_val, signal_val = macd.iloc[-1], signal.iloc[-1]
        
        macd_rsi_status = f"RSI: {rsi:.1f} | MACD: {'金叉' if macd_val > signal_val else '死叉'}"
        
        final_candidates.append({
            **stock,
            'ma_status': ma_status,
            'bias_ma5': round(bias_ma5, 2),
            'volume_status': volume_status,
            'macd_rsi_status': macd_rsi_status
        })
    
    final_candidates.sort(key=lambda x: x['pct_chg'], reverse=True)
    return final_candidates[:MAX_PICKS * 2]


def pick_stocks(target_date: str, lookahead: bool = False) -> List[StockPick]:
    """Main daily picking logic."""
    logger.info(f"=== Daily News-Driven Stock Picker (ts_daily) ===")
    
    regime_data = detect_market_regime(target_date)
    market_regime = regime_data.get('regime', 'normal')
    
    candidates = get_stock_candidates(target_date, lookahead, market_regime)
    if not candidates:
        logger.warning("No candidates found")
        return []
        
    news_service = NewsService()
    analyzer = GeminiDailyAnalyzer()
    
    hot_sectors_context = get_hot_sectors(target_date)
    market_dashboard = get_market_dashboard(target_date)
    
    if not analyzer.is_available():
        logger.error("Analyzer unavailable")
        return []
        
    analyzed_stocks = []
    
    # We analyze up to 15 candidates for daily due to the stricter requirements
    for stock in candidates[:15]:
        ts_code = stock['ts_code']
        
        cached = analyzer._cache.get(ts_code, target_date, market_regime)
        if cached:
            analyzed_stocks.append({
                **stock,
                'ai_score': cached['score'],
                'recommendation': cached['recommendation'],
                'ai_summary': cached['summary'],
                'tp_pct': cached.get('tp_pct', 0.10),
                'sl_pct': cached.get('sl_pct', 0.05)
            })
            continue
            
        logger.info(f"Analyzing daily catalyst for {stock['name']} ({ts_code})...")
        news_context = news_service.search(stock['name'], ts_code, target_date)
        analysis = analyzer.analyze(stock, news_context, market_regime, hot_sectors_context, market_dashboard, target_date)
        
        analyzed_stocks.append({
            **stock,
            'ai_score': analysis['score'],
            'recommendation': analysis['recommendation'],
            'ai_summary': analysis['summary'],
            'tp_pct': analysis['tp_pct'],
            'sl_pct': analysis['sl_pct']
        })
        time.sleep(1.0)
        
    min_score = 65  # Higher threshold because we demand a valid news catalyst
    buy_candidates = [
        s for s in analyzed_stocks 
        if s['recommendation'] == '买入' and s['ai_score'] >= min_score
    ]
    
    # Sort by AI score
    buy_candidates.sort(key=lambda x: x['ai_score'], reverse=True)
    
    picks = []
    for i, stock in enumerate(buy_candidates[:MAX_PICKS]):
        picks.append(StockPick(
            rank=i + 1,
            symbol=stock['ts_code'],
            score=stock['ai_score'],
            name=stock['name'],
            ai_summary=stock['ai_summary'],
            tp_pct=stock.get('tp_pct', 0.10),
            sl_pct=stock.get('sl_pct', 0.05)
        ))
        
    return picks


def main():
    parser = argparse.ArgumentParser(description='Daily Stock Picker (ts_daily)')
    parser.add_argument('date', help='Target trading date (YYYYMMDD)')
    parser.add_argument('--lookahead', action='store_true', help='Use lookahead data')
    parser.add_argument('--output', default=OUTPUT_FILE, help='Output file path')
    args = parser.parse_args()
    
    if not args.output:
        args.output = '/tmp/tmp'
        
    # Pick stocks
    picks = pick_stocks(args.date, args.lookahead)
    
    # Standard output format
    output = {
        "selected_stocks": [
            {
                "rank": p.rank,
                "symbol": p.symbol,
                "score": p.score,
                "name": p.name,
                "ai_summary": p.ai_summary,
                "tp_pct": getattr(p, 'tp_pct', 0.10),
                "sl_pct": getattr(p, 'sl_pct', 0.05)
            }
            for p in picks
        ]
    }
    
    with open(args.output, 'w') as f:
        json.dump(output, f)
        
    logger.info(f"ts_daily picked {len(picks)} stocks. Valid output saved to {args.output}")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
