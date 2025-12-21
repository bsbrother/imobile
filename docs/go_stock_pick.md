Here is the step-by-step analysis of how pick_stocks works:

1. How it picks out the stock (Step-by-Step)
The process starts in main.go and uses tushare_data_api.go.

- Determine Date: It takes the input date. If it's not a trading day or if data is missing (e.g., future date), it automatically falls back to the previous valid trading day.

- Fetch All Data: It calls the Tushare daily API to get market-wide data (all stocks) for that specific date.

- Sort Universally: Inside GetTopList (backend/data/tushare_data_api.go), it sorts ALL stocks by pct_chg (Percentage Change) in descending order.

- Filter Candidates: It iterates through this pre-sorted list (starting from the highest gainers) and applies the following strict filters. A stock is REJECTED if:
  - Board Filter: Code starts with 30 (ChiNext), 688 (STAR Market), 8, 4, or 9. (Basically restricts to Mainboard 00 and 60 only).
  - Risk Filter: Name contains ST or matches the regex ^(?:C|N|\*?ST|S)|退.
    - ST/*ST: Specially Treat / Risk Warning.
    - N: New listing (1st day).
    - C: New listing (first 5 days).
    - S: Not fully share reform? (Undetermined, but filtered).
    - 退: Generates "Delisting" warning.

2. How it orders Top 10?
The ordering is determined before the filtering takes place:

Primary Sort: By Daily Percentage Change (pct_chg) from Highest to Lowest.
Selection: The code picks the first 10 stocks from this sorted list that survive the filters above.

In summary: The result is the Top 10 Mainboard Non-Risk Stocks with the Highest Daily Gain, ordered by their gain (pct_chg).

