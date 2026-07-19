import os
import sys
import json
from typing import Optional, Dict, List
from datetime import datetime, timedelta
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

# ── Provider capability cache ────────────────────────────────────────────────
# The cache records which search providers actually return results for historical
# date queries.  SearXNG's json_engine cannot extract display_time, so it always
# fails the date-window filter for historical dates.  By testing once and
# caching the results, the backtest avoids wasting time on broken providers.

PROVIDER_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "search_providers_cache.json")


def _load_provider_cache() -> Dict:
    """Load the provider capability cache from disk."""
    try:
        if os.path.exists(PROVIDER_CACHE_PATH):
            with open(PROVIDER_CACHE_PATH) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load provider cache: {e}")
    return {}


def _build_all_providers(config) -> list:
    """
    Build instances of ALL known provider types from config.
    Returns a list of (name, provider_instance) tuples.
    Providers without API keys are still included (their is_available will be False).
    """
    from src.search_service import (
        SearXNGSearchProvider,
        TavilySearchProvider,
        SerpAPISearchProvider,
        BraveSearchProvider,
        AnspireSearchProvider,
        TinyFishSearchProvider,
        MiniMaxSearchProvider,
        BochaSearchProvider,
        DuckDuckGoSearchProvider,
    )

    providers = []

    # 1. SearXNG
    name = "SearXNG"
    try:
        p = SearXNGSearchProvider(
            config.searxng_base_urls,
            use_public_instances=getattr(config, "searxng_public_instances_enabled", True),
        )
    except Exception:
        p = None
    providers.append((name, p))

    # 2. Tavily
    name = "Tavily"
    try:
        p = TavilySearchProvider(config.tavily_api_keys) if config.tavily_api_keys else None
    except Exception:
        p = None
    providers.append((name, p))

    # 3. SerpAPI
    name = "SerpAPI"
    try:
        p = SerpAPISearchProvider(config.serpapi_keys) if config.serpapi_keys else None
    except Exception:
        p = None
    providers.append((name, p))

    # 4. Brave
    name = "Brave"
    try:
        p = BraveSearchProvider(config.brave_api_keys) if config.brave_api_keys else None
    except Exception:
        p = None
    providers.append((name, p))

    # 5. Anspire
    name = "Anspire"
    try:
        p = AnspireSearchProvider(config.anspire_api_keys) if config.anspire_api_keys else None
    except Exception:
        p = None
    providers.append((name, p))

    # 6. TinyFish
    name = "TinyFish"
    try:
        p = TinyFishSearchProvider(config.tinyfish_api_keys) if config.tinyfish_api_keys else None
    except Exception:
        p = None
    providers.append((name, p))

    # 7. MiniMax
    name = "MiniMax"
    try:
        p = MiniMaxSearchProvider(config.minimax_api_keys) if config.minimax_api_keys else None
    except Exception:
        p = None
    providers.append((name, p))

    # 8. Bocha
    name = "Bocha"
    try:
        p = BochaSearchProvider(config.bocha_api_keys) if config.bocha_api_keys else None
    except Exception:
        p = None
    providers.append((name, p))

    # 9. DuckDuckGo (no key needed)
    name = "DuckDuckGo"
    try:
        p = DuckDuckGoSearchProvider()
    except Exception:
        p = None
    providers.append((name, p))

    return providers


def _save_provider_cache(cache: Dict) -> None:
    """Persist the provider capability cache to disk."""
    try:
        with open(PROVIDER_CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Failed to save provider cache: {e}")
        return
    logger.info(f"Provider capability cache saved to {PROVIDER_CACHE_PATH}")


def test_search_providers(
    test_stock_name: str = "平安银行",
    test_stock_code: str = "000001.SZ",
    test_date: str = "",
    max_results: int = 3,
) -> Dict[str, Dict[str, bool]]:
    """
    Test each configured search provider and record Capabilities.

    Each provider gets two boolean fields:

    - ``can_search``: the provider is reachable and returns >= 1 result.
    - ``history_date_for_backtest``: the provider can return results **with
      published_date** for a historical-date query.  Backtest only uses
      providers where **both** fields are true.

    Example return value (and cache file contents)::

        {
            "SearXNG":     {"can_search": true,  "history_date_for_backtest": false},
            "Tavily":      {"can_search": true,  "history_date_for_backtest": true},
            "SerpAPI":     {"can_search": true,  "history_date_for_backtest": true},
            "TinyFish":    {"can_search": true,  "history_date_for_backtest": false},
            "DuckDuckGo":  {"can_search": true,  "history_date_for_backtest": false},
            "Anspire":     {"can_search": false, "history_date_for_backtest": false},
        }

    The result is saved to ``search_providers_cache.json``.
    """
    if not SERVICE_AVAILABLE:
        logger.error("SearchService not available; cannot test providers")
        return {}

    if not test_date:
        # Use a recent date window where search engines have indexed content
        test_date = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

    # Build ALL known provider instances from config (not just the ones
    # that SearchService decided to load).  This ensures every provider
    # type appears in the cache, even if it has no API key.
    try:
        config = get_config()
        all_providers = _build_all_providers(config)
    except Exception as e:
        logger.error(f"Failed to build provider list: {e}")
        return {}

    date_str = f"{test_date[:4]}年{test_date[4:6]}月{test_date[6:]}"
    query = f"{test_stock_name} {test_stock_code.split('.')[0]} 股票 {date_str} 最新消息 利好 舆情"

    # Default entry for every provider
    _default = {"can_search": False, "history_date_for_backtest": False}
    results: Dict[str, Dict[str, bool]] = {}

    logger.info(f"=== Testing search providers (historical date {test_date}) ===")

    for name, provider in all_providers:
        results[name] = dict(_default)

        if provider is None or not provider.is_available:
            logger.info(f"  {name:20s}: SKIPPED (no API key configured)")
            continue

        # ── Test 1: basic can_search ──────────────────────────────────────
        try:
            resp = provider.search(query, max_results=max_results, days=30)
            if resp.success and resp.results:
                results[name]["can_search"] = True
                # ── Test 2: history_date_for_backtest ────────────────────
                dated = [r for r in resp.results if r.published_date]
                if dated:
                    results[name]["history_date_for_backtest"] = True
                    logger.info(
                        f"  {name:20s}: can_search=True  history_date=True  "
                        f"({len(resp.results)} results, {len(dated)} with date)"
                    )
                else:
                    logger.info(
                        f"  {name:20s}: can_search=True  history_date=False  "
                        f"({len(resp.results)} results, 0 with date)"
                    )
            else:
                logger.info(
                    f"  {name:20s}: can_search=False  history_date=False  "
                    f"({resp.error_message or 'no results'})"
                )
        except Exception as e:
            logger.info(f"  {name:20s}: can_search=False  history_date=False  ERROR: {e}")

    # SearXNG-specific hard override:
    # Even if SearXNG returns results (can_search=True), its json_engine
    # cannot extract display_time, so history_date_for_backtest is always false.
    searxng_names = [k for k in results if "searxng" in k.lower()]
    for k in searxng_names:
        results[k]["history_date_for_backtest"] = False
        if results[k]["can_search"]:
            logger.info(
                f"  {k:20s}: OVERRIDE history_date_for_backtest=False  "
                f"(json_engine cannot extract display_time)"
            )

    _save_provider_cache(results)

    # Print summary
    ok_both = [k for k, v in results.items() if v["can_search"] and v["history_date_for_backtest"]]
    ok_search_only = [k for k, v in results.items() if v["can_search"] and not v["history_date_for_backtest"]]
    broken = [k for k, v in results.items() if not v["can_search"]]
    logger.info(f"=== Backtest-usable providers (both true): {ok_both} ===")
    logger.info(f"=== Search-only providers: {ok_search_only} ===")
    logger.info(f"=== Broken/unavailable providers: {broken} ===")

    return results


def get_backtest_providers() -> List[str]:
    """
    Return provider names that are usable for backtest.

    A provider is usable when **both** ``can_search`` AND
    ``history_date_for_backtest`` are true in the cache.
    """
    cache = _load_provider_cache()
    return [
        name for name, caps in cache.items()
        if caps.get("can_search") and caps.get("history_date_for_backtest")
    ]


def get_search_capable_providers() -> List[str]:
    """
    Return provider names that can perform basic search (can_search=true).
    This includes providers that may not have historical date support.
    """
    cache = _load_provider_cache()
    return [name for name, caps in cache.items() if caps.get("can_search")]


def get_search_service() -> Optional['SearchService']:
    """
    Return a SearchService filtered to only use providers that passed the
    capability test.

    If no cache exists yet, the first call will run the provider test automatically.
    """
    global _search_service_instance

    if not SERVICE_AVAILABLE:
        return None

    cache = _load_provider_cache()

    # If cache is empty, run the test once
    if not cache:
        logger.info("No provider cache found; running provider test...")
        cache = test_search_providers()
        if not cache:
            logger.warning("Provider test returned empty results; using all providers")

    # Build env filter from cache: only include backtest-capable providers
    # (both can_search AND history_date_for_backtest are true).
    backtest_providers = get_backtest_providers()
    if backtest_providers:
        provider_filter = ",".join(p.lower() for p in backtest_providers)
        os.environ["WORKING_SEARCH_PROVIDERS"] = provider_filter
        logger.info(f"Search provider whitelist (backtest-capable): {backtest_providers}")
    else:
        # No backtest-capable providers found; fall back to search-capable
        search_providers = get_search_capable_providers()
        if search_providers:
            provider_filter = ",".join(p.lower() for p in search_providers)
            os.environ["WORKING_SEARCH_PROVIDERS"] = provider_filter
            logger.warning(
                f"No backtest-capable providers; falling back to search-capable: {search_providers}"
            )
        else:
            logger.warning("No working providers in cache; SearchService will try all")

    if _search_service_instance is None:
        try:
            config = get_config()
            _search_service_instance = SearchService(
                bocha_keys=config.bocha_api_keys,
                tavily_keys=config.tavily_api_keys,
                anspire_keys=config.anspire_api_keys,
                brave_keys=config.brave_api_keys,
                serpapi_keys=config.serpapi_keys,
                minimax_keys=config.minimax_api_keys,
                searxng_base_urls=config.searxng_base_urls,
                tinyfish_keys=config.tinyfish_api_keys,
                anysearch_keys=config.anysearch_api_keys,
            )
        except Exception as e:
            logger.error(f"Failed to initialize SearchService: {e}")
            return None

    # NOTE: We do NOT patch SearXNG to skip time_range.  Instead, we mark
    # SearXNG as "cannot provide historical date info" in the provider cache
    # and exclude it via WORKING_SEARCH_PROVIDERS.  This is the correct
    # approach because SearXNG's json_engine fundamentally cannot extract
    # display_time from API responses, so its results always have
    # publishedDate=None and get dropped by _filter_news_response.

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