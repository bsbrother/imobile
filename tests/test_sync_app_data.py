#!/usr/bin/env python3
"""
This demonstrates how to sync real-time data from mobile app to database.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_guotai import sync_app_data_to_db


def test_only_quote_data():
    """Test with only quote page data."""

    quote_data = """
Index Name,Index Number,Index Ratio
Shanghai (沪),3882.78,+0.52%
Shenzhen (深),13526.51,+0.35%
Chi (创),3238.16,0.00%

stock_name,code,latest_price,increase_percentage,increase_amount
中科三环,000970,14.17,+1.21%,+0.17
中南股份,000717,2.72,+0.37%,+0.01
盈方微,000670,8.93,+2.53%,+0.22
深圳华强,000062,28.73,-0.21%,-0.06
中国宝安,000009,12.46,+3.66%,+0.44
深振业Ａ,000006,10.29,+10.05%,+0.94
安井食品,603345,70.51,+0.37%,+0.26
美凯龙,601828,2.80,+0.36%,+0.01
华安证券,600909,6.32,-0.32%,-0.02
亚泰集团,600881,1.92,-1.03%,-0.02
重庆港,600279,5.16,-0.77%,-0.04
皖维高新,600063,6.21,+1.47%,+0.09
    """

    print("\n" + "=" * 70)
    print("Testing with only quote data")
    print("=" * 70)

    result = sync_app_data_to_db(
        quote_data=quote_data,
        user_id=1,
        db_path='imobile.db'
    )

    print(f"\n✅ Success: {result['success']}")
    print(f"📝 Message: {result['message']}")

    return result['success']


def test_only_position_data():
    """Test with only position page data."""

    position_data = """浮动盈亏,账户资产,总市值,仓位,可用,可取"""
    position_data = """
Floating Profit/Loss,Account Assets,Market Cap,Positions,Available,Desirable
-361757.86,855169.66,814839.00,95.28%,40330.66,40330.66

stock_name,market_cap,open,available,current_price,cost,floating_profit,floating_loss(%)
深振业Ａ,385875.000,37500,37500,10.290,13.361,-115165.77,-22.99%
盈方微,89300.000,10000,10000,8.930,21.885,-129545.21,-59.19%
中国宝安,62300.000,5000,5000,12.460,16.573,-20565.24,-24.82%
深圳华强,60333.000,2100,2100,28.730,30.949,-4660.19,-7.17%
中南股份,50592.000,18600,18600,2.720,3.205,-9027.26,-15.14%
重庆港,46440.000,9000,9000,5.160,13.159,-71987.60,-60.79%
皖维高新,32913.000,5300,5300,6.210,6.924,-3782.05,-10.31%
中科三环,29757.000,2100,2100,14.170,11.400,+5816.26,+24.29%
安井食品,21153.000,300,300,70.510,97.521,-8103.25,-27.70%
亚泰集团,14592.000,7600,7600,1.920,2.084,-1244.38,-7.86%
美凯龙,14000.000,5000,5000,2.800,3.426,-3130.71,-18.27%
华安证券,7584.000,1200,1200,6.320,6.622,-362.46,-4.56%
    """

    print("\n" + "=" * 70)
    print("Testing with only position data")
    print("=" * 70)

    result = sync_app_data_to_db(
        position_data=position_data,
        user_id=1,
        db_path='imobile.db'
    )

    print(f"\n✅ Success: {result['success']}")
    print(f"📝 Message: {result['message']}")

    return result['success']


if __name__ == "__main__":
    print("\n" + "🚀" * 35)
    print("     Real-time Data Save Function Test Suite")
    print("🚀" * 35 + "\n")

    # Check if database exists
    db_path = os.path.join(os.path.dirname(__file__), '..', 'imobile.db')
    if not os.path.exists(db_path):
        print(f"⚠️  Warning: Database file not found at {db_path}")
        print("   The function will create it automatically.\n")

    # Run tests
    test_results = []

    try:
        test_results.append(("Quote only test", test_only_quote_data()))
        test_results.append(("Position only test", test_only_position_data()))
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Print summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)

    for test_name, result in test_results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status} - {test_name}")

    all_passed = all(result for _, result in test_results)

    if all_passed:
        print("\n" + "🎉" * 35)
        print("     All tests passed!")
        print("🎉" * 35 + "\n")
        sys.exit(0)
    else:
        print("\n⚠️  Some tests failed. Please check the output above.\n")
        sys.exit(1)
