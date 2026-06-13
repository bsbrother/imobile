# -*- coding: utf-8 -*-
"""
Test FreeRide integration to replace Google Gemini model for stock analysis.
Tests that FreeRide can analyze stock news/sentiments/opinion obtained from search APIs.

Usage:
    python tests/test_freeride.py
    python tests/test_freeride.py --model openrouter/nvidia/nemotron-3-super-120b-a12b:free
    python tests/test_freeride.py --model openrouter/owl-alpha
    python tests/test_freeride.py --model invalid/model
"""

import os
import sys
import argparse
import unittest

# Ensure sys.path includes workspace root
workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

# ---------------------------------------------------------------------------
# Model validation: accept --model arg, default to the current hard-coded model,
# validate against FreeRide's model list, exit with "invalid model" if not found.
# ---------------------------------------------------------------------------

def _validate_model_or_exit(cli_model: str | None) -> str:
    """Validate the model string against FreeRide's available free models.

    If *cli_model* is None, the default model is returned.
    If *cli_model* is not found in the FreeRide list, prints "invalid model"
    and exits with code 1.
    """
    default_model = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
    model = cli_model if cli_model else default_model

    try:
        from utils.FreeRide.main import get_free_models
        free_models = get_free_models()
        model_ids = [m["id"] for m in free_models]
    except Exception as e:
        # If we can't reach OpenRouter, fall back leniently but warn
        print(f"Warning: could not fetch FreeRide model list: {e}")
        print(f"Proceeding with model: {model}")
        return model

    if model in model_ids:
        return model

    # Try matching with openrouter/ prefix stripped
    # (FreeRide list uses bare IDs like "nvidia/nemotron-3-super-120b-a12b:free",
    #  but callers may pass "openrouter/nvidia/nemotron-3-super-120b-a12b:free")
    stripped = model
    if stripped.startswith("openrouter/"):
        stripped = stripped[len("openrouter/"):]
    if stripped in model_ids:
        return stripped

    # Partial/lower-case match on both forms
    for m_id in model_ids:
        if model.lower() == m_id.lower() or model.lower() in m_id.lower():
            print(f"Model '{model}' matched to '{m_id}'")
            return m_id
        if stripped.lower() == m_id.lower() or stripped.lower() in m_id.lower():
            print(f"Model '{model}' matched to '{m_id}'")
            return m_id

    # Not found
    print(f"invalid model: '{model}' is not in the FreeRide model list.")
    print(f"Run 'python utils/FreeRide/main.py list' to see available models.")
    sys.exit(1)


def _parse_args():
    """Parse only --model, leave the rest for unittest."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--model", type=str, default=None,
                        help="FreeRide model ID to test (default: openrouter/nvidia/nemotron-3-super-120b-a12b:free)")
    # Use parse_known_args so unittest still gets its own flags
    args, _ = parser.parse_known_args()
    return args


# Validate model *before* tests run
_args = _parse_args()
VALIDATED_MODEL: str = _validate_model_or_exit(_args.model)
print(f"Using FreeRide model: {VALIDATED_MODEL}")

# ---------------------------------------------------------------------------
# Imports that depend on validated model
# ---------------------------------------------------------------------------
from backtest.strategies.ts_daily import GeminiDailyAnalyzer, DailyAnalysisCache


class TestFreeRideIntegration(unittest.TestCase):
    """Test FreeRide integration for replacing Google Gemini in stock analysis."""

    def setUp(self):
        """Set up each test."""
        self.analyzer = GeminiDailyAnalyzer(model_name=VALIDATED_MODEL)

    def test_freeride_model_initialization(self):
        """Test that GeminiDailyAnalyzer initializes with FreeRide gateway."""
        analyzer = self.analyzer

        # Check that the client is initialized
        self.assertIsNotNone(analyzer._client, "FreeRide/OpenAI client should be initialized")

        # Check that the model name matches the validated model
        self.assertEqual(
            analyzer._model_name,
            VALIDATED_MODEL,
            f"Model should be set to validated model: {VALIDATED_MODEL}"
        )

        print(f"✅ FreeRide model initialization test passed (model={VALIDATED_MODEL})")

    def test_freeride_is_available(self):
        """Test that the FreeRide model is available."""
        analyzer = self.analyzer
        self.assertTrue(
            analyzer.is_available(),
            "FreeRide model should be available when OpenAI client is initialized"
        )
        print("✅ FreeRide availability test passed")

    def test_analyze_with_mock_news(self):
        """Test stock analysis with mock news context."""
        analyzer = self.analyzer

        # Use a unique target_date to avoid cache pollution from previous runs
        import uuid
        unique_date = f"20250115{uuid.uuid4().hex[:4]}"

        # Mock stock info
        stock_info = {
            'ts_code': '000001.SZ',
            'name': '平安银行',
            'industry': '银行',
            'close': 12.34,
            'pct_chg': 1.23,
            'ma_status': '多头排列',
            'bias_ma5': 2.5,
            'volume_status': '放量',
            'volume_ratio': 1.8,
            'macd_rsi_status': 'MACD金叉, RSI偏强'
        }

        # Mock news context (positive news)
        news_context = "公司今日发布利好公告，计划增加分红比例，预计全年净利润增长30%以上。"
        market_dashboard = "- 上涨: 1200 | 下跌: 800 | 平盘: 150 | 涨停: 45\n- 成交额: 8500亿"
        market_regime = 'bull'

        # Perform analysis
        try:
            result = analyzer.analyze(
                stock_info=stock_info,
                news_context=news_context,
                market_regime=market_regime,
                hot_sectors='',
                market_dashboard=market_dashboard,
                target_date=unique_date
            )

            # Validate result structure
            self.assertIn('score', result)
            self.assertIn('recommendation', result)
            self.assertIn('summary', result)

            # Validate value types and ranges
            self.assertIsInstance(result['score'], (int, float))
            self.assertGreaterEqual(result['score'], 0)
            self.assertLessEqual(result['score'], 100)

            self.assertIn(result['recommendation'], ['买入', '观望', '卖出'])

            self.assertIsInstance(result['summary'], str)
            self.assertGreater(len(result['summary']), 0)

            # tp_pct/sl_pct may not be present if the model omitted them
            # (the analyze method adds defaults, but cache lookups may not)
            if 'tp_pct' in result:
                self.assertIsInstance(result['tp_pct'], (int, float))
                self.assertGreater(result['tp_pct'], 0)
            if 'sl_pct' in result:
                self.assertIsInstance(result['sl_pct'], (int, float))
                self.assertGreater(result['sl_pct'], 0)

            print(f"✅ Analysis completed - Score: {result['score']}, Recommendation: {result['recommendation']}")
            print(f"   Summary: {result['summary']}")

        except Exception as e:
            # If FreeRide service is not available, we expect a graceful fallback
            if "FreeRide analysis failed" in str(e) or "Failed to initialize FreeRide gateway" in str(e):
                self.skipTest(f"FreeRide service not available: {e}")
            else:
                raise  # Re-raise unexpected errors

    def test_caching_mechanism(self):
        """Test that the caching mechanism works with FreeRide analyzer."""
        analyzer = self.analyzer

        # Mock stock info
        stock_info = {
            'ts_code': '600000.SH',
            'name': '浦发银行',
            'industry': '银行',
            'close': 10.50,
            'pct_chg': 0.80,
            'ma_status': '中性',
            'bias_ma5': 0.5,
            'volume_status': '量能平稳',
            'volume_ratio': 1.0,
            'macd_rsi_status': 'MACD死叉, RSI中性'
        }

        news_context = "银行板块今日表现平稳，无重大消息。"
        market_dashboard = "- 上涨: 900 | 下跌: 1100 | 平盘: 200 | 涨停: 20\n- 成交额: 7800亿"
        market_regime = 'neutral'
        target_date = '20250116'

        # First analysis - should call the model
        result1 = analyzer.analyze(
            stock_info=stock_info,
            news_context=news_context,
            market_regime=market_regime,
            hot_sectors='',
            market_dashboard=market_dashboard,
            target_date=target_date
        )

        # Second analysis with same parameters - should use cache
        result2 = analyzer.analyze(
            stock_info=stock_info,
            news_context=news_context,
            market_regime=market_regime,
            hot_sectors='',
            market_dashboard=market_dashboard,
            target_date=target_date
        )

        # Results should be identical (from cache)
        self.assertEqual(result1['score'], result2['score'])
        self.assertEqual(result1['recommendation'], result2['recommendation'])
        self.assertEqual(result1['summary'], result2['summary'])

        print("✅ Caching mechanism test passed - second call used cached result")

    def test_error_handling(self):
        """Test error handling when FreeRide service is unavailable."""
        # We'll simulate an unavailable service by patching the client to raise an exception
        analyzer = self.analyzer

        # Temporarily make the client unavailable
        original_client = analyzer._client
        analyzer._client = None

        try:
            # Mock stock info
            stock_info = {
                'ts_code': '000001.SZ',
                'name': '平安银行',
                'industry': '银行',
                'close': 12.34,
                'pct_chg': 1.23,
                'ma_status': '多头排列',
                'bias_ma5': 2.5,
                'volume_status': '放量',
                'volume_ratio': 1.8,
                'macd_rsi_status': 'MACD金叉, RSI偏强'
            }

            news_context = "测试新闻"
            market_dashboard = "市场数据"
            market_regime = 'normal'
            target_date = '20250115'

            # Should return graceful fallback
            result = analyzer.analyze(
                stock_info=stock_info,
                news_context=news_context,
                market_regime=market_regime,
                hot_sectors='',
                market_dashboard=market_dashboard,
                target_date=target_date
            )

            # Should return the fallback values
            self.assertEqual(result['score'], 50)
            self.assertEqual(result['recommendation'], "观望")
            self.assertEqual(result['summary'], "AI分析不可用")
            self.assertEqual(result['tp_pct'], 0.10)
            self.assertEqual(result['sl_pct'], 0.05)

            print("✅ Error handling test passed - graceful fallback when service unavailable")

        finally:
            # Restore original client
            analyzer._client = original_client

    def test_model_configuration(self):
        """Test that the model is correctly configured for FreeRide."""
        analyzer = self.analyzer

        # Check that we're using the OpenAI-compatible API (chat.completions)
        import inspect
        source = inspect.getsource(analyzer.analyze)
        self.assertIn('chat.completions.create', source, "Should use OpenAI-compatible chat.completions API")

        # Check that Gemini API is not used
        self.assertNotIn('models.generate_content', source, "Should not use Gemini API")

        print(f"✅ Model configuration test passed - using model {VALIDATED_MODEL}")


def run_freeride_tests():
    """Run the FreeRide integration tests."""
    # Create a test suite
    suite = unittest.TestSuite()

    # Add test cases
    test_class = TestFreeRideIntegration
    tests = [
        'test_freeride_model_initialization',
        'test_freeride_is_available',
        'test_analyze_with_mock_news',
        'test_caching_mechanism',
        'test_error_handling',
        'test_model_configuration'
    ]

    for test_name in tests:
        suite.addTest(test_class(test_name))

    # Run the tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print("\n" + "="*80)
    print(" FREERIDE INTEGRATION TEST SUMMARY ")
    print("="*80)
    print(f"Model: {VALIDATED_MODEL}")
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(getattr(result, 'skipped', []))}")

    if result.failures:
        print("\n❌ FAILURES:")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback}")

    if result.errors:
        print("\n❌ ERRORS:")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback}")

    if result.testsRun > 0 and len(result.failures) == 0 and len(result.errors) == 0:
        print("\n✅ ALL FREERIDE INTEGRATION TESTS PASSED!")
        print("🎉 The ts_daily strategy successfully uses FreeRide to replace Google Gemini")
        print("   for analyzing stock news/sentiments/opinion.")
    else:
        print("\n⚠️  Some tests failed or were skipped - check FreeRide service availability")

    print("="*80 + "\n")

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_freeride_tests()
    sys.exit(0 if success else 1)