# TODO
- 2025.11.20
  Create single html file:
    - pick stock for next trading date: current hot sectors top 10, and pick stocks from these sectors by score.
    - sector history: show the sector daily line。

- 2025.11.19 [buy/sell by 5 & 21 lines](https://www.laoyulaoyu.com/index.php/2025/11/11/%e7%94%a8%e4%b8%a4%e6%a0%b9%e7%ba%bf%ef%bc%8c%e4%bb%8e10%e4%b8%87%e5%81%9a%e5%88%b0100%e4%b8%87%ef%bc%9a%e4%b8%80%e4%b8%aa%e7%ae%80%e5%8d%95%e7%b2%97%e6%9a%b4%e7%9a%84%e8%b5%9a%e9%92%b1%e7%a7%98/)

- 2025.11.18 return all zero, no trading.
  In the Chinese A-Shares market, Using Python and the Tushare API, develop a strategy based on 'Trend Reversal Signals(Cover Buy Sell)' and 'Wave Momentum Indicators(EWO)' that can outperform the market index(SSE, CSI300 etc.)
  [EWO](https://saber2pr.top/zh/posts/3516500479/52137657/)
  Ways to Generate Trades:
  - Expand the date window so the strict entry condition has more chances to appear (the divergence detector needs a 20‑day lookback, and the MA regime filter needs 200 trading days of history).
  - Relax the entry logic in build_signal_panel if you want more activity, e.g. replace the & with | or introduce a parameter that lets you choose between “strict (divergence + zero-cross)” and “looser (either signal)”.
  - Tune the indicator spans (fast_span, slow_span, lookback, ma_fas, ma_slow) to better match the volatility of you universe.


- 2025.11.15
  - 不要用前复权qfq，也不要用后复权hfq，就用不复权数据，遇到除权除息，则动态调整股票数量计算了红利，然后再使用新的不复权数据计算总资产。我们真实的购买股票的收益就是这样算的。谁自己买卖的股票还用什么前复权和后复权数据来算收益的吗？都是使用的实际成本算的
    * use get_ohlcv_data, not use get_kline.

- Avoid major news periods (technical indicators may fail)
  - Central bank rate cut/hike days
  - Release days of important economic data (GDP, CPI, etc.)
  - Major international events (war, financial crisis, etc.)

- Advance adjust.
  - Use the 60-minute chart to determine the trend direction. If it's upward, sell.
  - Trading volume should increase by more than 1.5 times.
  - Avoid stocks with losses or significant negative news.
  - When the Shanghai Composite Index is below 2800 points, be more aggressive in buying; above 4000 points, be cautious with buying.

- Add a small log helper to dump, for each skipped name, the actual MA20 distance / 5d return / volume ratio so you can tune the thresholds quickly.


- 2025.11.4
  # pick stocks from strong sectors
  - strategy_n(40%): multi strategy selected, high count got high score.
  - vol_return(30%): stock_5d_return > 6 and < 12
  - limit_up(20%):   consecutive_limit_up < 2
  - money_flow(10%): net_mf_amount > 1000, price_change > 0

  - avoid low-activity loss stocks:
    * turnover_rate > 10%, volume_ratio > 1.5
    * Open price > prev close then buy

  -  implement alternative force-sell strategies (trailing stops, volume-based, or market condition-based).

  - Additional Recommendations
    * Monitor sector rotation - Switch from hot sectors when they cool
    - Track market volatility - Adjust stops based on ATR (Average True Range)
    - Implement trailing stops - Instead of fixed %, adjust as price rises
    - Add buy-and-hold baseline - Compare vs simply holding index
    - Add profit-taking levels - Sell 50% at 10%, 30% at 15%, 20% at 20%

  # smart orders sell
  - next_open_price < buy_price, adjust order trigger: >next_open_price sell
  - 5 days not > take_profit_price then sell.


- 2025.10.30
  - [免费获取股票数据接口API（实时数据、历史数据、CDMA、KDJ等指标数据)](https://blog.csdn.net/Eumenides_max/article/details/144694349)
  - [数立方平台](https://datacube.foundersc.com/document/2)
  - pandas vs polars

- 2025.10.6
  Modify utils/downloader_by_drissionpage.py to use LLM, not use drissionpage.
  The apk filename format always as yyz_n1.n2.n3.*_gtja.apk. n1, n2, n3 are 1-2 digits, ignore any other digits or characters after n3, end with _gtja.apk.

- 2025.10.7
  Use class Tablex(...) and Tablex.f1, so can be reflex migrate.
  Now is directory changed by db/imobile.sql, db/migrations/add_realtime_fields.sql, and reload data: python app_guotai.py
