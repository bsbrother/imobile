#
# Trading time (before, open, after) utils
#
import os
import sys
# Add the parent directory to Python path so we can import from utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.db import DB

# Get market open time relate fields from app_config table in db/imobile.db .
def get_market_open_times_refresh_interval():
    """
    Get market open times and data refresh interval from app_config table.

    Returns a tuple:
    (open_time_morning_start, open_time_morning_end,
     open_time_afternoon_start, open_time_afternoon_end,
     data_refresh_interval)
    All times are in "HH:MM" format strings, data_refresh_interval is in minutes (int).
    """
    query = """
    SELECT
        open_time_morning_start,
        open_time_morning_end,
        open_time_afternoon_start,
        open_time_afternoon_end,
        data_refresh_interval
    FROM
        app_config
    WHERE
        user_id = 1 and
        market = 'A-shares'
    """
    result = DB.fetch_one(query)
    return ','.join(str(x) for x in result)

if __name__ == "__main__":
    times = get_market_open_times_refresh_interval()
    print(f"Market open times and data refresh interval: {times}")

