# TODO
- 2025.12.12 result_ts_vs_index.py
Period               | Index Returns (SSE, CSI300)    | Method Returns
----------------------------------------------------------------------------------------------------
From 20250701 to 20250731,
- index(SSE: 3.74%, CSI300: 0.42%) return.
- ts_combine: -3.63%, ts_dc: 0.11%, ts_ths: -0.42%
--------------------------------------------------
From 20250801 to 20250831,
- index(SSE: 7.97%, CSI300: 10.33%) return.
- ts_combine: 6.52%, ts_dc: 12.59%, ts_ths: 9.29%
--------------------------------------------------
From 20250901 to 20250930,
- index(SSE: 0.12%, CSI300: 0.00%) return.
- ts_combine: 6.35%, ts_dc: 8.34%, ts_ths: 3.25%
--------------------------------------------------
From 20251001 to 20251031,
- index(SSE: 1.85%, CSI300: 1.49%) return.
- ts_combine: 0.33%, ts_dc: 1.54%, ts_ths: 0.08%
--------------------------------------------------
From 20251101 to 20251130,
- index(MISMATCH! SSE:-1.67%/-2.01%, CSI:-2.46%) return.
- ts_combine: 4.63%, ts_dc: 9.27%, ts_ths: 3.27%
--------------------------------------------------

- 2025.12.11
  https://github.com/rejith77/SMA-Trading-Agent
  An autonomous financial trading agent powered by LangGraph, LangChain, and OpenAI, capable of executing financial analysis tasks such as fetching stock data, calculating indicators (like SMA/RSI), and running strategy backtests automatically through iterative reasoning.

  [AI auto pick stock, trading and backtest] https://github.com/sngyai/Sequoia
    strategies = {
        '放量上涨': enter.check_volume,
        '均线多头': keep_increasing.check,
        '停机坪': parking_apron.check,
        '回踩年线': backtrace_ma250.check,
        # '突破平台': breakthrough_platform.check,
        '无大幅回撤': low_backtrace_increase.check,
        '海龟交易法则': turtle_trade.check_enter,
        '高而窄的旗形': high_tight_flag.check,
        '放量跌停': climax_limitdown.check,
    }

- 2025.12.10
- 策略核心逻辑与进阶优化建议
板块动量轮动策略: 买入当期热点板块中的龙头股，并持有至月底。

为了让策略更有效，你可以从以下几个方面进行优化，这也是专业量化研究的常见方向：

动态选股：当前策略使用了固定的股票池。你可以将其改进为在每个月末，根据过去20-60天的涨幅、成交额、市值等因子，从所有A股中动态筛选出强势板块的龙头股。

引入多因子模型：像搜索中提到的那样，结合估值（PE/PB）、动量、财务质量等多个因子进行综合评分选股。

改进买卖点：不止在期初期末交易，可以引入均线突破、RSI等技术指标作为买卖信号。

风险控制：在策略中加入止损、止盈和仓位管理的规则。

降低交易成本：在cerebro.broker.set_slippage_perc()中设置滑点，以更真实地模拟交易。

- 2025.12.06
Not re-runing again, all result at backtest/backtest_results/20251001_20251031_ts_xxx/.
xxx is ths, dc, combine.
ths: return 2.49%
dc: return 0.22%
combine: -1.23%

Analysis each trading date(pick stocks, create smart orders, trading orders), why ths return best, dc return better, and combine both(ths and dc) is low. fix it.

- 2005.12.05
python backtest_orders.py 20251101 20251130 ts_xxx, xxx is: ths, dc, combine
results at backtest/backtest_results/20251101_20251130_ts_xxx/.

You not need re-running again, just anaysis each trading date(pick stocks, create smart orders, trading orders) at these directory:
- ths:
  | Metric | Strategy | SSE Composite | CSI 300 |
  |--------|----------|---------------|---------|
  | **Total Return** | 15.60% | -2.55% | -2.72% |

- dc:
  | Metric | Strategy | SSE Composite | CSI 300 |
  |--------|----------|---------------|---------|
  | **Total Return** | 2.49% | -2.55% | -2.72% |

- combine:
  | Metric | Strategy | SSE Composite | CSI 300 |
  |--------|----------|---------------|---------|
  | **Total Return** | -0.11% | -2.21% | -2.72% |

- Why combine (ths and dc) get litter profit(-0.11%) then ths(15.60% and dc(2.49%)?
- Why ths get more profit then dc ?

- 2025.12.03
Strategy: Instead of switching monthly, consider diversifying. Allocate 50% capital to THS strategy and 50% to DC.
In Oct, you would have averaged ~ -1.0% (better than -2.6%).
In Nov, you would have averaged ~ +6.8% (capturing the gains).
create a "Combined Strategy" script that runs both and merges the picks.

- 2025.12.02
Analysis backtest result at backtest/backtest_results/, it has 4 folders, according to provider tushare api ts_dc or ts_ths, period from 20251001 to 20251031 or from 20251101 to 20251130. Each trading date pick stocks as pick_stocks_yyyymmdd.json, create smart orders as smart_orders_yyyymmdd, excute orders(buy/sell) as report_orders_yyyymmdd.md.
- 20251001_20251031_ts_dc/report_period_20251001_20251031.md: Total Return -2.63%

- 20251101_20251130_ts_dc/report_period_20251101_20251130.md: Total Return 6.95%
- 20251001_20251031_ts_ths/report_period_20251001_20251031.md: Total Return 0.56%
- 20251101_20251130_ts_ths/report_period_20251101_20251130.md: Total Return 6.68%

1. what are difference with ths and dc ?
2. why dc month 10 get nagative (-2.63%) profit, but month 11 get more (6.95%) profit?
3. ths month 10 get litter (0.56%) profit, but month 11 get 6.68% profilt, less then dc 6.95% profit.
4. can use ths at month 10 at use dc at month 11, for more profit.

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
