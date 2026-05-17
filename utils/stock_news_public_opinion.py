import os
import sys
from typing import Optional
from loguru import logger

# Add daily_stock_analysis to path so we can import its robust SearchService
_daily_analysis_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'utils', 'daily_stock_analysis')
if _daily_analysis_path not in sys.path:
    sys.path.insert(0, _daily_analysis_path)

try:
    from src.search_service import SearchService
    from src.config import get_config
    SERVICE_AVAILABLE = True
except ImportError as e:
    logger.error(f"Failed to import SearchService from daily_stock_analysis: {e}")
    SERVICE_AVAILABLE = False

_search_service_instance = None

def get_search_service() -> Optional['SearchService']:
    global _search_service_instance
    if not SERVICE_AVAILABLE:
        return None
        
    if _search_service_instance is None:
        try:
            config = get_config()
            # Initialize the robust SearchService which automatically handles Bocha, Anspire, Brave, SearXNG, etc.
            # This is highly optimized for A-Share Chinese news and public opinion.
            _search_service_instance = SearchService(
                bocha_keys=config.bocha_api_keys,
                tavily_keys=config.tavily_api_keys,
                anspire_keys=config.anspire_api_keys,
                brave_keys=config.brave_api_keys,
                serpapi_keys=config.serpapi_keys,
                minimax_keys=config.minimax_api_keys,
                searxng_base_urls=config.searxng_base_urls,
            )
        except Exception as e:
            logger.error(f"Failed to initialize SearchService: {e}")
            return None
            
    return _search_service_instance

def fetch_stock_news_and_opinion(stock_name: str, stock_code: str, target_date: str, max_results: int = 5) -> str:
    """
    Fetch historical news and public opinion for a specific stock on a specific date.
    Uses daily_stock_analysis's SearchService which is heavily optimized for A-Share context.
    
    Args:
        stock_name: Name of the stock
        stock_code: Code of the stock
        target_date: Date in YYYYMMDD format
        max_results: Max results to fetch
        
    Returns:
        Formatted string containing news and public opinions
    """
    service = get_search_service()
    if not service or not service.is_available:
        return ""
        
    try:
        # Format date for searching
        if len(target_date) == 8:
            date_str = f"{target_date[:4]}年{target_date[4:6]}月{target_date[6:]}日"
        else:
            date_str = target_date
            
        # Comprehensive query for A-Share
        query = f"{stock_name} {stock_code.split('.')[0]} 股票 {date_str} 最新消息 利好 舆情"
        
        response = service.search_stock_news(
            stock_code=stock_code, 
            stock_name=stock_name, 
            max_results=max_results, 
            focus_keywords=[query]
        )
        
        if not response or not response.results:
            return ""
            
        results = []
        for i, item in enumerate(response.results):
            # Format the output clearly
            results.append(f"{i+1}. [{item.source}] {item.title}: {item.snippet}")
            
        return "\n".join(results)
        
    except Exception as e:
        logger.error(f"Error fetching news via stock_news_public_opinion: {e}")
        return ""