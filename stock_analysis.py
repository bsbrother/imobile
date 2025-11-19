#!/usr/bin/env python
"""
Stock Analysis Script for iMobile Project

This script reads stocks from the imobile.db stocks_table and runs analysis 
for each stock using the external stock_analysis.py script.
"""

import sqlite3
import subprocess
import sys
import os
from pathlib import Path
from typing import List, Tuple, Optional
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class StockAnalyzer:
    """Handles stock analysis by reading from database and calling external script."""
    
    def __init__(self, db_path: str = "imobile.db", 
                 external_script_path: str = "../illm/utils/EvoAgentX/Wonderful_workflow_corpus/invest/stock_analysis.py"):
        """
        Initialize the StockAnalyzer.
        
        Args:
            db_path: Path to the imobile database
            external_script_path: Path to the external stock_analysis.py script
        """
        self.db_path = db_path
        self.external_script_path = external_script_path
        self.output_dir = Path("/tmp")
        
        # Validate paths
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        
        if not os.path.exists(self.external_script_path):
            raise FileNotFoundError(f"External script not found: {self.external_script_path}")
    
    def get_stocks_from_db(self, user_id: Optional[int] = None) -> List[Tuple[str, str]]:
        """
        Read stocks from the database.
        
        Args:
            user_id: Optional user_id to filter stocks. If None, fetch all stocks.
            
        Returns:
            List of tuples containing (stock_code, stock_name)
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            if user_id:
                query = "SELECT DISTINCT code, name FROM stocks_table WHERE user_id = ? ORDER BY code"
                cursor.execute(query, (user_id,))
            else:
                query = "SELECT DISTINCT code, name FROM stocks_table ORDER BY code"
                cursor.execute(query)
            
            stocks = cursor.fetchall()
            conn.close()
            
            logger.info(f"Retrieved {len(stocks)} stocks from database")
            return stocks
            
        except sqlite3.Error as e:
            logger.error(f"Database error: {e}")
            return []
    
    def analyze_stock(self, stock_code: str, stock_name: str) -> bool:
        """
        Run analysis for a single stock by calling the external script.
        
        Args:
            stock_code: Stock code (e.g., "000970")
            stock_name: Stock name (e.g., "中科三环")
            
        Returns:
            True if analysis succeeded, False otherwise
        """
        try:
            logger.info(f"Analyzing stock: {stock_code} - {stock_name}")
            
            # Call the external script with stock code
            # The script will create output in /tmp/{stock_name}/
            result = subprocess.run(
                [sys.executable, self.external_script_path, stock_code],
                capture_output=True,
                text=True,
                timeout=300  # 5 minutes timeout
            )
            
            # Check if output directory was created
            expected_output_dir = self.output_dir / stock_name
            
            if result.returncode == 0:
                logger.info(f"✓ Successfully analyzed {stock_code} - {stock_name}")
                if expected_output_dir.exists():
                    logger.info(f"  Output directory: {expected_output_dir}")
                return True
            else:
                logger.error(f"✗ Failed to analyze {stock_code} - {stock_name}")
                logger.error(f"  Error: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"✗ Timeout analyzing {stock_code} - {stock_name}")
            return False
        except Exception as e:
            logger.error(f"✗ Exception analyzing {stock_code} - {stock_name}: {e}")
            return False
    
    def analyze_all_stocks(self, user_id: Optional[int] = None, 
                          limit: Optional[int] = None) -> dict:
        """
        Analyze all stocks from the database.
        
        Args:
            user_id: Optional user_id to filter stocks
            limit: Optional limit on number of stocks to analyze
            
        Returns:
            Dictionary with analysis statistics
        """
        stocks = self.get_stocks_from_db(user_id)
        
        if not stocks:
            logger.warning("No stocks found in database")
            return {"total": 0, "success": 0, "failed": 0}
        
        if limit:
            stocks = stocks[:limit]
            logger.info(f"Limiting analysis to first {limit} stocks")
        
        stats = {
            "total": len(stocks),
            "success": 0,
            "failed": 0,
            "stocks_analyzed": []
        }
        
        for i, (code, name) in enumerate(stocks, 1):
            logger.info(f"\n[{i}/{len(stocks)}] Processing stock...")
            
            if self.analyze_stock(code, name):
                stats["success"] += 1
                stats["stocks_analyzed"].append((code, name, "success"))
            else:
                stats["failed"] += 1
                stats["stocks_analyzed"].append((code, name, "failed"))
        
        return stats
    
    def print_summary(self, stats: dict):
        """Print analysis summary."""
        logger.info("\n" + "="*60)
        logger.info("ANALYSIS SUMMARY")
        logger.info("="*60)
        logger.info(f"Total stocks: {stats['total']}")
        logger.info(f"Successfully analyzed: {stats['success']}")
        logger.info(f"Failed: {stats['failed']}")
        
        if stats['failed'] > 0:
            logger.info("\nFailed stocks:")
            for code, name, status in stats['stocks_analyzed']:
                if status == "failed":
                    logger.info(f"  ✗ {code} - {name}")
        
        logger.info("="*60)


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Analyze stocks from imobile database"
    )
    parser.add_argument(
        "--user-id",
        type=int,
        help="Filter stocks by user ID"
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of stocks to analyze"
    )
    parser.add_argument(
        "--db",
        default="imobile.db",
        help="Path to database file (default: imobile.db)"
    )
    parser.add_argument(
        "--script",
        default="../illm/utils/EvoAgentX/Wonderful_workflow_corpus/invest/stock_analysis.py",
        help="Path to external stock_analysis.py script"
    )
    
    args = parser.parse_args()
    
    try:
        analyzer = StockAnalyzer(
            db_path=args.db,
            external_script_path=args.script
        )
        
        stats = analyzer.analyze_all_stocks(
            user_id=args.user_id,
            limit=args.limit
        )
        
        analyzer.print_summary(stats)
        
        # Exit with error code if any analysis failed
        sys.exit(0 if stats['failed'] == 0 else 1)
        
    except FileNotFoundError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.warning("\nAnalysis interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
