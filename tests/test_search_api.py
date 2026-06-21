# -*- coding: utf-8 -*-
"""
Real-world Search API Providers Integration Test Suite.
Fetches real stock news, sentiment, and opinion.
Measures and prints performance metrics (speed vs. content richness) to rank the 8 providers:
- SearXNG, TinyFish, SerpAPI, MiniMax, Brave, Anspire, Bocha, Tavily
"""

import os
import sys
import unittest
import time
from unittest.mock import MagicMock
import dotenv

# Load environment variables
dotenv.load_dotenv()

# Ensure sys.path includes workspace root and utils/daily_stock_analysis
workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

project_root = os.path.join(workspace_root, 'utils', 'daily_stock_analysis')
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Mock newspaper to avoid import dependency issues
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.config import Config
from src.search_service import (
    AnspireSearchProvider,
    BochaSearchProvider,
    TinyFishSearchProvider,
    TavilySearchProvider,
    BraveSearchProvider,
    SerpAPISearchProvider,
    MiniMaxSearchProvider,
    SearXNGSearchProvider,
    DuckDuckGoSearchProvider,
)
from utils.anysearch import AnySearchSearchProvider


import subprocess

def ensure_searxng_container_running():
    """
    Check if the local SearXNG instance is running at http://localhost:8080.
    SearXNG runs directly on the host (no Docker) -- VPN handles upstream routing.
    """
    import os
    import time
    import urllib.request

    def _is_searxng_reachable():
        """Check if searxng HTTP server is responding."""
        try:
            base_url = os.getenv("SEARXNG_BASE_URLS", "http://localhost:8080").split(",")[0].strip()
            req = urllib.request.Request(base_url, method="GET")
            req.add_header("User-Agent", "imobile-healthcheck/1.0")
            resp = urllib.request.urlopen(req, timeout=5)
            resp.read(64)
            return True, "OK"
        except Exception as e:
            return False, str(e)

    # Step 1: Check if already running
    reachable, reason = _is_searxng_reachable()
    if reachable:
        print("[SearXNG] Local instance RUNNING and REACHABLE at localhost:8080")
        return True

    print("[SearXNG] Local instance not reachable: {}".format(reason))
    print("[SearXNG] Starting local SearXNG...")

    # Step 2: Start SearXNG locally using the installed package
    searxng_settings = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "searxng-data", "settings.yml"
    )
    # Fallback: try common locations
    if not os.path.exists(searxng_settings):
        searxng_settings = os.path.expanduser("~/searxng-data/settings.yml")

    env = os.environ.copy()
    env["SEARXNG_SETTINGS_PATH"] = searxng_settings

    proc = subprocess.Popen(
        ["python", "-m", "searxng_run"] if False else
        [os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".venv", "bin", "searxng-run")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )

    print("[SearXNG] Started local instance (PID {}), waiting 8s...".format(proc.pid))
    time.sleep(8)

    # Step 3: Verify
    reachable, reason = _is_searxng_reachable()
    if reachable:
        print("[SearXNG] Local instance RUNNING and REACHABLE")
        return True
    else:
        print("[SearXNG] Local instance not reachable after startup: {}".format(reason))
        print("[SearXNG] Trying to auto-start: run 'SEARXNG_SETTINGS_PATH=~/searxng-data/settings.yml .venv/bin/searxng-run &' manually")
        return reachable


class TestSearchAPIProvidersReal(unittest.TestCase):
    """Real integration tests for search providers without mocking."""

    # Class-level summary list to store performance metrics for comparison
    results_summary = []

    @classmethod
    def setUpClass(cls):
        # Programmatically guarantee local SearXNG container is active before running tests
        ensure_searxng_container_running()

        # Read keys directly from Config singleton
        Config._Config__instance = None
        cls.config = Config._load_from_env()
        print("\n" + "="*80)
        print(" INITIALIZING REAL SEARCH PROVIDERS TESTING ")
        print("="*80)

        # ── Provider capability test (historical date support) ──────────────────
        # Delete old cache so we always start fresh, then run the provider
        # capability test to create a new search_providers_cache.json.
        cache_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "utils", "search_providers_cache.json",
        )
        if os.path.exists(cache_path):
            os.remove(cache_path)
            print(f"[PROVIDER CACHE] Deleted old cache: {cache_path}")

        from utils.stock_news_public_opinion import (
            test_search_providers, get_backtest_providers, get_search_capable_providers,
        )
        print("\n" + "="*80)
        print(" RUNNING PROVIDER CAPABILITY TEST (historical date support) ")
        print("="*80)
        test_search_providers(
            test_stock_name="平安银行",
            test_stock_code="000001.SZ",
            test_date="",
            max_results=3,
        )
        backtest_ok = get_backtest_providers()
        search_ok = get_search_capable_providers()
        print(f"\n[PROVIDER CACHE] Backtest-usable (both true): {backtest_ok}")
        print(f"[PROVIDER CACHE] Search-capable (can_search=true): {search_ok}")
        searxng_backtest = any("searxng" in w.lower() for w in backtest_ok)
        print(f"[PROVIDER CACHE] SearXNG backtest-capable: {searxng_backtest}")
        if not searxng_backtest:
            print("[PROVIDER CACHE] SearXNG excluded: json_engine cannot extract historical date info")
        print("="*80 + "\n")

    @classmethod
    def tearDownClass(cls):
        """Sort and print comparison ranking after all tests complete."""
        if not cls.results_summary:
            print("\n⚠️ No search providers were successfully run/tested with real keys.")
            return

        # Calculate composite score: higher is better
        # Score = Content Richness (Count * Avg Snippet Length) / Time Taken
        for entry in cls.results_summary:
            count = entry["result_count"]
            avg_len = entry["avg_snippet_length"]
            time_taken = entry["time_taken"]
            
            # Richness score
            richness = count * (avg_len or 10)
            entry["richness_score"] = richness
            entry["composite_score"] = richness / (time_taken + 0.1) if entry["success"] else 0.0

        # Sort by composite score descending (best performance first)
        ranked_summary = sorted(cls.results_summary, key=lambda x: x["composite_score"], reverse=True)

        # Load the provider capability cache to determine which providers
        # are backtest-capable (both can_search AND history_date_for_backtest).
        import json as _json
        cache_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "utils", "search_providers_cache.json",
        )
        provider_cache = {}
        try:
            if os.path.exists(cache_path):
                with open(cache_path) as f:
                    provider_cache = _json.load(f)
        except Exception:
            pass
        # A provider is excluded from backtest if either field is false
        excluded_providers = {
            k.lower() for k, v in provider_cache.items()
            if not (v.get("can_search") and v.get("history_date_for_backtest"))
        }
        # Build a set of excluded provider name prefixes for substring matching.
        # e.g. "searxng" should also match "SearXNG_Historical"
        def _is_excluded(provider_name):
            name_lower = provider_name.lower()
            return any(excl in name_lower for excl in excluded_providers)

        print("\n" + "="*100)
        print("                        SEARCH API PROVIDERS RANKING REPORT")
        print("                      (Sorted by content quality and speed)")
        print("="*100)
        print(f"{'Rank':<6}{'Provider':<15}{'Status':<10}{'Time (s)':<12}{'Count':<8}{'Avg Snippet':<15}{'Score':<10}")
        print("-"*100)
        for rank, entry in enumerate(ranked_summary, 1):
            status = "Success" if entry["success"] else "Failed"
            # Mark excluded providers clearly
            is_excl = _is_excluded(entry["provider"])
            if is_excl:
                status = "EXCLUDED"
            print(f"#{rank:<5}{entry['provider']:<15}{status:<10}{entry['time_taken']:<12.3f}{entry['result_count']:<8}{int(entry['avg_snippet_length']):<15}{entry['composite_score']:<10.2f}")
        print("="*100)
        print("Recommendation Strategy Priority Order (based on contents and speed):")
        # Only include providers that succeeded AND are not excluded by capability test
        priority_list = [
            entry["provider"] for entry in ranked_summary
            if entry["success"] and not _is_excluded(entry["provider"])
        ]
        failed_list = [
            entry["provider"] for entry in ranked_summary
            if not entry["success"] and not _is_excluded(entry["provider"])
        ]
        excluded_list = [
            entry["provider"] for entry in ranked_summary
            if _is_excluded(entry["provider"])
        ]
        print(" -> ".join(priority_list) if priority_list else "None succeeded")
        if failed_list:
            print(f"Failed providers (no quota or rate-limited): {', '.join(failed_list)}")
        if excluded_list:
            print(f"Excluded providers (cannot provide historical date info): {', '.join(excluded_list)}")
        print("="*100 + "\n")

    def _run_real_search(self, provider_instance, provider_name: str, query: str = "腾讯控股 股票 舆情 利好 分析"):
        """Helper to run search and measure time and contents."""
        # Support searching with specific historical trading date if configured in self (speci_trading_date)
        trading_date = getattr(self, "speci_trading_date", None)
        if trading_date:
            query = f"{query} {trading_date}"
            
        print(f"\n🔍 Testing {provider_name} with real query: '{query}'...")
        start_time = time.time()
        try:
            response = provider_instance.search(query, max_results=3, days=3)
            elapsed = time.time() - start_time
            
            if not response.success:
                print(f"⚠️ {provider_name} returned failure: {response.error_message}")
                self.results_summary.append({
                    "provider": provider_name,
                    "time_taken": elapsed,
                    "result_count": 0,
                    "avg_snippet_length": 0,
                    "success": False
                })
                # Gracefully return instead of failing the test case
                return
                
            # Compute stats
            results = response.results
            count = len(results)
            avg_snippet_len = sum(len(r.snippet) for r in results) / count if count > 0 else 0
            
            print(f"✅ {provider_name} succeeded! Time: {elapsed:.3f}s, Results: {count}, Avg Snippet Length: {int(avg_snippet_len)}")
            for i, res in enumerate(results, 1):
                print(f"   [{i}] Source: {res.source} | Title: {res.title[:50]}...")
                
            self.results_summary.append({
                "provider": provider_name,
                "time_taken": elapsed,
                "result_count": count,
                "avg_snippet_length": avg_snippet_len,
                "success": True
            })
            
        except Exception as e:
            elapsed = time.time() - start_time
            print(f"⚠️ {provider_name} raised exception: {e}")
            self.results_summary.append({
                "provider": provider_name,
                "time_taken": elapsed,
                "result_count": 0,
                "avg_snippet_length": 0,
                "success": False
            })

    def test_anspire_provider_real(self):
        """Real integration test for Anspire Search Provider."""
        keys = self.config.anspire_api_keys
        if not keys:
            self.skipTest("Anspire keys not configured in environment (ANSPIRE_API_KEYS)")
            
        provider = AnspireSearchProvider(keys)
        self._run_real_search(provider, "Anspire")

    def test_bocha_provider_real(self):
        """Real integration test for Bocha Search Provider."""
        keys = self.config.bocha_api_keys
        if not keys:
            self.skipTest("Bocha keys not configured in environment (BOCHA_API_KEYS)")
            
        provider = BochaSearchProvider(keys)
        self._run_real_search(provider, "Bocha")

    def test_tinyfish_provider_real(self):
        """Real integration test for TinyFish Search Provider."""
        keys = self.config.tinyfish_api_keys
        if not keys:
            self.skipTest("TinyFish keys not configured in environment (TINYFISH_API_KEYS or TINYFISH_API_KEY)")
            
        provider = TinyFishSearchProvider(keys)
        self._run_real_search(provider, "TinyFish")

    def test_tavily_provider_real(self):
        """Real integration test for Tavily Search Provider."""
        keys = self.config.tavily_api_keys
        if not keys:
            self.skipTest("Tavily keys not configured in environment (TAVILY_API_KEYS)")
            
        provider = TavilySearchProvider(keys)
        self._run_real_search(provider, "Tavily")

    def test_brave_provider_real(self):
        """Real integration test for Brave Search Provider."""
        keys = self.config.brave_api_keys
        if not keys:
            self.skipTest("Brave keys not configured in environment (BRAVE_API_KEYS)")
            
        provider = BraveSearchProvider(keys)
        self._run_real_search(provider, "Brave")

    def test_serpapi_provider_real(self):
        """Real integration test for SerpAPI Search Provider."""
        keys = self.config.serpapi_keys
        if not keys:
            self.skipTest("SerpAPI keys not configured in environment (SERPAPI_API_KEYS)")
            
        provider = SerpAPISearchProvider(keys)
        self._run_real_search(provider, "SerpAPI")

    def test_minimax_provider_real(self):
        """Real integration test for MiniMax Search Provider."""
        keys = self.config.minimax_api_keys
        if not keys:
            self.skipTest("MiniMax keys not configured in environment (MINIMAX_API_KEYS)")
            
        provider = MiniMaxSearchProvider(keys)
        self._run_real_search(provider, "MiniMax")

    def test_searxng_provider_real(self):
        """Real integration test for SearXNG Search Provider (with and without historical trading date filter)."""
        provider = SearXNGSearchProvider(
            self.config.searxng_base_urls,
            use_public_instances=self.config.searxng_public_instances_enabled
        )
        if not provider.is_available:
            self.skipTest("SearXNG provider not available (no base URLs or public discovery disabled)")

        # 1. Test standard real-time search
        print("\n--- Testing SearXNG with Realtime Query ---")
        self._run_real_search(provider, "SearXNG")

        # 2. Test historical search with a specific trading date
        print("\n--- Testing SearXNG with Historical Trading Date (20251215) ---")
        self.speci_trading_date = "20251215"
        try:
            self._run_real_search(provider, "SearXNG_Historical")
        finally:
            self.speci_trading_date = None
    def test_duckduckgo_provider_real(self):
        """Real integration test for DuckDuckGo Search Provider."""
        provider = DuckDuckGoSearchProvider()
        self._run_real_search(provider, "DuckDuckGo")

    def test_anysearch_provider_real(self):
        """Real integration test for AnySearch Search Provider (with and without historical trading date filter)."""
        key = os.getenv("ANYSEARCH_API_KEY")
        if not key:
            self.skipTest("ANYSEARCH_API_KEY not configured in environment.")
            
        provider = AnySearchSearchProvider([key])
        
        # 1. Test standard real-time search
        print("\n--- Testing AnySearch with Realtime Query ---")
        self._run_real_search(provider, "AnySearch")
        
        # 2. Test historical search with a specific trading date
        print("\n--- Testing AnySearch with Historical Trading Date (20251215) ---")
        # Temporarily configure a specific trading date for this test case
        self.speci_trading_date = "20251215"
        try:
            self._run_real_search(provider, "AnySearch_Historical")
        finally:
            self.speci_trading_date = None


if __name__ == "__main__":
    unittest.main()
