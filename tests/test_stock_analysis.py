#!/usr/bin/env python
"""
Test script for stock_analysis.py

This script verifies the functionality of stock_analysis.py without
actually running the time-consuming external analysis.
"""

import unittest
import sqlite3
import os
import sys
from unittest.mock import Mock, patch
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stock_analysis import StockAnalyzer


class TestStockAnalyzer(unittest.TestCase):
    """Test cases for StockAnalyzer class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.db_path = "imobile.db"
        self.script_path = "../illm/utils/EvoAgentX/Wonderful_workflow_corpus/invest/stock_analysis.py"
    
    def test_database_connection(self):
        """Test that we can connect to the database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='stocks_table'")
        result = cursor.fetchone()
        conn.close()
        
        self.assertIsNotNone(result, "stocks_table should exist")
    
    def test_get_stocks_from_db(self):
        """Test retrieving stocks from database."""
        analyzer = StockAnalyzer(db_path=self.db_path, external_script_path=self.script_path)
        stocks = analyzer.get_stocks_from_db()
        
        self.assertIsInstance(stocks, list, "Should return a list")
        if stocks:
            self.assertEqual(len(stocks[0]), 2, "Each stock should have code and name")
            code, name = stocks[0]
            self.assertIsInstance(code, str, "Code should be a string")
            self.assertIsInstance(name, str, "Name should be a string")
    
    def test_get_stocks_with_user_filter(self):
        """Test retrieving stocks with user_id filter."""
        analyzer = StockAnalyzer(db_path=self.db_path, external_script_path=self.script_path)
        
        # Get all stocks first to find a valid user_id
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT user_id FROM stocks_table LIMIT 1")
        result = cursor.fetchone()
        conn.close()
        
        if result:
            user_id = result[0]
            stocks = analyzer.get_stocks_from_db(user_id=user_id)
            self.assertIsInstance(stocks, list, "Should return a list")
    
    def test_database_query_distinct(self):
        """Test that we get distinct stocks (no duplicates)."""
        analyzer = StockAnalyzer(db_path=self.db_path, external_script_path=self.script_path)
        stocks = analyzer.get_stocks_from_db()
        
        # Check for duplicates
        codes = [code for code, _ in stocks]
        self.assertEqual(len(codes), len(set(codes)), "Stock codes should be unique")
    
    def test_stock_data_format(self):
        """Test that stock data is in expected format."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT code, name FROM stocks_table LIMIT 1")
        result = cursor.fetchone()
        conn.close()
        
        if result:
            code, name = result
            # Stock codes should be 6 digits
            self.assertTrue(len(code) == 6, f"Stock code should be 6 digits, got: {code}")
            self.assertTrue(code.isdigit(), f"Stock code should be numeric, got: {code}")
            # Name should not be empty
            self.assertTrue(len(name) > 0, "Stock name should not be empty")
    
    @patch('subprocess.run')
    def test_analyze_stock_success(self, mock_run):
        """Test successful stock analysis."""
        # Mock successful subprocess call
        mock_run.return_value = Mock(returncode=0, stderr="")
        
        analyzer = StockAnalyzer(db_path=self.db_path, external_script_path=self.script_path)
        result = analyzer.analyze_stock("000970", "中科三环")
        
        self.assertTrue(result, "Analysis should succeed")
        mock_run.assert_called_once()
    
    @patch('subprocess.run')
    def test_analyze_stock_failure(self, mock_run):
        """Test failed stock analysis."""
        # Mock failed subprocess call
        mock_run.return_value = Mock(returncode=1, stderr="Error message")
        
        analyzer = StockAnalyzer(db_path=self.db_path, external_script_path=self.script_path)
        result = analyzer.analyze_stock("000970", "中科三环")
        
        self.assertFalse(result, "Analysis should fail")
    
    def test_invalid_database_path(self):
        """Test handling of invalid database path."""
        with self.assertRaises(FileNotFoundError):
            StockAnalyzer(db_path="nonexistent.db", external_script_path=self.script_path)
    
    def test_invalid_script_path(self):
        """Test handling of invalid script path."""
        with self.assertRaises(FileNotFoundError):
            StockAnalyzer(db_path=self.db_path, external_script_path="nonexistent.py")


class TestDatabaseIntegrity(unittest.TestCase):
    """Test cases for database integrity."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.db_path = "imobile.db"
    
    def test_stocks_table_columns(self):
        """Test that stocks_table has expected columns."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(stocks_table)")
        columns = [row[1] for row in cursor.fetchall()]
        conn.close()
        
        required_columns = ['code', 'name', 'user_id']
        for col in required_columns:
            self.assertIn(col, columns, f"Column '{col}' should exist in stocks_table")
    
    def test_stock_code_format(self):
        """Test that all stock codes are in correct format."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT code FROM stocks_table")
        codes = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        for code in codes:
            self.assertEqual(len(code), 6, f"Stock code should be 6 digits: {code}")
            self.assertTrue(code.isdigit(), f"Stock code should be numeric: {code}")


def run_quick_test():
    """Run a quick functionality test without mocking."""
    print("=" * 60)
    print("QUICK FUNCTIONALITY TEST")
    print("=" * 60)
    
    try:
        from stock_analysis import StockAnalyzer
        
        analyzer = StockAnalyzer()
        
        print("\n1. Testing database connection...")
        stocks = analyzer.get_stocks_from_db()
        print(f"   ✓ Successfully retrieved {len(stocks)} stocks")
        
        if stocks:
            print(f"\n2. Sample stocks (first 3):")
            for code, name in stocks[:3]:
                print(f"   - {code}: {name}")
        
        print("\n3. Testing user filter...")
        conn = sqlite3.connect("imobile.db")
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT user_id FROM stocks_table LIMIT 1")
        result = cursor.fetchone()
        conn.close()
        
        if result:
            user_id = result[0]
            user_stocks = analyzer.get_stocks_from_db(user_id=user_id)
            print(f"   ✓ Found {len(user_stocks)} stocks for user_id={user_id}")
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)
        print("\nTo run actual stock analysis (with limit):")
        print("  python stock_analysis.py --limit 1")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        return False
    
    return True


if __name__ == "__main__":
    print("\nRunning quick functionality test first...\n")
    if run_quick_test():
        print("\n\nRunning unit tests...\n")
        unittest.main(argv=[''], verbosity=2)
    else:
        print("\nQuick test failed. Fix errors before running unit tests.")
        sys.exit(1)
