"""Test portfolio database integration."""
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

def test_database_connection():
    """Test basic database connection and data retrieval."""
    # Create engine
    engine = create_engine("sqlite:///imobile.db")
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Test total_table query
        print("Testing total_table query...")
        total_query = text("""
            SELECT total_market_value, today_pnl, today_pnl_percent,
                   cumulative_pnl, cumulative_pnl_percent, cash,
                   floating_pnl_summary, floating_pnl_summary_percent,
                   total_assets, principal
            FROM total_table
            WHERE user_id = :user_id
            LIMIT 1
        """)
        total_result = session.execute(total_query, {"user_id": 1}).fetchone()
        
        if total_result:
            print("\n✓ Total table data found:")
            print(f"  Total Market Value: {total_result[0]}")
            print(f"  Today P&L: {total_result[1]}")
            print(f"  Today P&L %: {total_result[2]}")
            print(f"  Cumulative P&L: {total_result[3]}")
            print(f"  Cumulative P&L %: {total_result[4]}")
            print(f"  Cash: {total_result[5]}")
            print(f"  Float P&L: {total_result[6]}")
            print(f"  Float P&L %: {total_result[7]}")
            print(f"  Total Assets: {total_result[8]}")
            print(f"  Principal: {total_result[9]}")
        else:
            print("✗ No data found in total_table for user_id=1")
        
        # Test stocks_table query
        print("\n\nTesting stocks_table query...")
        stocks_query = text("""
            SELECT code, name, current_price, change, change_percent,
                   market_value, holdings, pnl_float, pnl_float_percent,
                   pnl_cumulative, pnl_cumulative_percent
            FROM stocks_table
            WHERE user_id = :user_id
            ORDER BY market_value DESC
        """)
        stocks_results = session.execute(stocks_query, {"user_id": 1}).fetchall()
        
        if stocks_results:
            print(f"\n✓ Found {len(stocks_results)} stocks:")
            for i, row in enumerate(stocks_results[:5], 1):  # Show first 5
                print(f"\n  Stock {i}:")
                print(f"    Code: {row[0]}")
                print(f"    Name: {row[1]}")
                print(f"    Current Price: {row[2]}")
                print(f"    Change: {row[3]} ({row[4]}%)")
                print(f"    Market Value: {row[5]}")
                print(f"    Holdings: {row[6]}")
                print(f"    Float P&L: {row[7]} ({row[8]}%)")
                print(f"    Cumulative P&L: {row[9]} ({row[10]}%)")
        else:
            print("✗ No stocks found in stocks_table for user_id=1")
        
        print("\n\n✓ Database integration test completed successfully!")
        
    except Exception as e:
        print(f"\n✗ Error during test: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    test_database_connection()
