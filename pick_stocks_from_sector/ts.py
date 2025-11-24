"""
Pick short-term strong stocks from Tushare THS hot concept sectors.
[Tushare API 打板专题数据](https://tushare.pro/document/2?doc_id=346)，并基于热门搜索和强势板块制定短期强势股选股策略。** 搜索接口链接已失效 **

## 📊 策略说明与使用要点
### 三个核心策略：
1. **板块动量策略**：识别近期表现强势的板块，并从中选择表现更好的个股
2. **资金流向策略**：跟踪主力资金流向，选择资金大幅流入的股票
3. **涨停板策略**：基于涨停股票数据，重点关注首板和二板股票

### 策略优化建议：
- **风险控制**：短期强势股波动大，建议设置止损位
- **仓位管理**：分散投资，避免过度集中
- **及时止盈**：设定明确的盈利目标并及时止盈
- **结合大盘**：在大盘向好时效果更佳

### 注意事项：
- Tushare API 有调用频率限制，代码中已加入延时
- 某些高级功能需要Tushare积分才能访问
- 实际交易前建议进行充分回测和模拟测试

TODO:
pip install -U pywencai
param = "{date}涨停，非涉嫌信息披露违规且非立案调查且非ST，非科创板，非北交所"
df = pywencai.get(query= param ,sort_key='成交金额', sort_order='desc')
print(df)
df.to_excel(spath, engine='xlsxwriter')
selected_columns = ['股票代码', '股票简称', '最新价','最新涨跌幅', '首次涨停时间['+date + ']', '连续涨停天数['+date + ']','涨停原因类别['+date + ']','a股市值(不含限售股)['+date + ']','涨停类型['+date + ']']
"""
import os
import sys
import time
from datetime import datetime
import requests
import json
from dotenv import load_dotenv
import logging
from loguru import logger
import pandas as pd
import numpy as np
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential
import warnings
from typing import Any

import tushare as ts
import adata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import data_provider
from backtest.utils.trading_calendar import get_trading_days_before, get_trading_days_between
from backtest.utils.util import convert_trade_date
from backtest.utils.market_regime import detect_market_regime
from utils.stock_code_name_valid import convert_akcode_to_tushare

# Create a standard logging logger for tenacity
tenacity_logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=UserWarning, module='py_mini_racer')

load_dotenv()
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
if not TUSHARE_TOKEN:
    raise ValueError("Please set the TUSHARE_TOKEN environment variable.")
PRO = ts.pro_api(TUSHARE_TOKEN)     # pyright: ignore
RECENT_DAYS = 5                     # recent days to calculate returns
LOOKBACK_DAYS = RECENT_DAYS * 4     # trading days lookback, almost 4 weeks, 1 month.

def filter_mainboard_stocks(stock_list: pd.DataFrame | list) -> pd.DataFrame:
    """
    过滤A股主板股票

    Args:
        stock_list: 包含股票信息的列表或DataFrame，必须有'ts_code'字段

    Returns:
        DataFrame: 只包含A股主板股票的DataFrame
    """
    if isinstance(stock_list, list) and len(stock_list) == 0 or (isinstance(stock_list, pd.DataFrame) and stock_list.empty):
        return pd.DataFrame()

    # 转换为DataFrame便于处理
    if isinstance(stock_list, list):
        df = pd.DataFrame(stock_list)
    else:
        df = stock_list.copy()

    main_board_mask = df['ts_code'].str.startswith(('60', '00'))
    main_board_stocks = df[main_board_mask].reset_index(drop=True)
    logger.info(f'{len(df)} stocks before filtering, {len(main_board_stocks)} mainboard stocks after filtering.')
    return main_board_stocks


def batch_get_concept_daily(start_date: str, end_date: str) -> tuple[(pd.DataFrame, pd.DataFrame)]:
    """
    批量获取概念板块日线数据，直到获取所有概念板块的完整数据

    Args:
        start_date: YYYYMMDD
        end_date: YYYYMMDD

    Returns:
        DataFrame: 所有概念板块在指定日期范围内的日线数据
    """
    logger.info(f'Start fetch concept daily data from {start_date} to {end_date} ...')
    concept_list = PRO.ths_index(exchange='A', type='N')
    if 'trade_date' in concept_list:
        concept_list = concept_list.sort_values(by='trade_date', ascending=True)
    logger.info(f"Got {len(concept_list)} concept sectors index records.")
    concept_codes = set(concept_list['ts_code'].tolist())

    # Got all concepts/sectors from [ths_index](https://tushare.pro/document/2?doc_id=260)
    # Obtain daily data for all sectors(3000 records/once) in order to avoid frequent API call limits(5 times/minute).
    # Each day records < 3000, max 3000/1 time. end_date - start_date = RECENT_DAYS days to get all concepts.
    all_concept_daily = pd.DataFrame()
    for date in get_trading_days_between(start_date, end_date):
        all_sectors_daily = PRO.ths_daily(start_date=date, end_date=date)
        if 'trade_date' in all_sectors_daily:
            all_sectors_daily = all_sectors_daily.sort_values(by='trade_date', ascending=True)
        # 过滤出概念板块
        concept_daily = all_sectors_daily[all_sectors_daily['ts_code'].isin(concept_codes)]
        logger.info(f"{date} all sectors: {len(all_sectors_daily)}, filter to concept sector: {len(concept_daily)}")
        all_concept_daily = pd.concat([all_concept_daily, concept_daily], ignore_index=True)
        time.sleep(1)
    logger.info(f"Got {len(all_concept_daily)} concept sectors daily records from {start_date} to {end_date}.")
    return concept_list, all_concept_daily


@retry(
    stop=stop_after_attempt(10),
    wait=wait_random_exponential(multiplier=0.4, min=2, max=6),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(tenacity_logger, logging.INFO)
)
def ths_member(ts_code:str) ->pd.DataFrame:
    """
    Custom function to avoid TuShare API 6000 points limit.
    members = PRO.ths_member(ts_code=sector['sector_code']) # 6000+ points can call.
    """
    url = f"https://d.10jqka.com.cn/v2/blockrank/{ts_code}/199112/d1000.js"
    headers = {
        'Referer': 'http://q.10jqka.com.cn/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
    }

    stocks_df = pd.DataFrame()
    response = requests.get(url, headers=headers, timeout=10)
    if response.status_code == 200:
        json_str = response.text.split('(', 1)[1].rsplit(')', 1)[0]
        data = json.loads(json_str)

        stock_list = data.get('items', [])
        if stock_list:
            stocks_df = pd.DataFrame(
                [(s.get('5', '').zfill(6),
                  s.get('55', '')) #,
                  #f"{float(s.get('8', 0)):.2f}",
                  #f"{float(s.get('199112', 0)):.2f}%")
                 for s in stock_list],
                #columns=['股票代码', '股票名称', '最新价', '涨跌幅']
                columns=['ts_code', 'name']
            )
        else:
            logger.warning("未找到相关个股数据")
    else:
        logger.error(f"Request status：{response.status_code}")
    return stocks_df


# 策略1: 基于板块动量筛选强势股
def sector_momentum_strategy(start_date: str, end_date: str):
    logger.info("策略1: 板块动量选股")
    strong_stocks = []
    try:
        concept_list, all_concept_daily = batch_get_concept_daily(start_date, end_date)
        sector_data = pd.merge(all_concept_daily, concept_list[['ts_code', 'name']], on='ts_code', how='left')
        sector_data = sector_data.sort_values(['ts_code', 'trade_date'], ascending=[True, False])
        sector_performance = []
        for sector_code in sector_data['ts_code'].unique():
            sector_daily = sector_data[sector_data['ts_code'] == sector_code]
            if len(sector_daily) >= 4:  # Need at least 4 days for 3-day return
                sector_name = sector_daily['name'].iloc[0]
                # 计算3日收益率 (Identify hot sectors faster, T vs T-3)
                recent_3d_return = (sector_daily.iloc[0]['close'] / sector_daily.iloc[3]['close'] - 1) * 100
                if recent_3d_return > 3:  # 3日内涨幅超过3%
                    sector_performance.append({
                        'sector_name': sector_name,
                        'sector_code': sector_code,
                        '3d_return': recent_3d_return,
                        'data_points': len(sector_daily)
                    })

        # 按收益率排序
        sector_performance.sort(key=lambda x: x['3d_return'], reverse=True)

        logger.info("强势板块排名top 10:")
        for i, sector in enumerate(sector_performance[:10], 1):
            logger.info(f"{i}. {sector['sector_name']}: {sector['3d_return']:.2f}%")

        # 获取强势板块的成分股
        for sector in sector_performance[:10]:
            #members = ths_member(ts_code=sector['sector_code'].split('.')[0])
            members = adata.stock.info.concept_constituent_ths(index_code=sector['sector_code'].split('.')[0])
            members.rename(columns={'stock_code': 'ts_code', 'short_name': 'name'}, inplace=True)
            members['ts_code'] = members['ts_code'].apply(convert_akcode_to_tushare)
            members = filter_mainboard_stocks(members)
            for _, member in members.iterrows():
                stock_data = PRO.daily(ts_code=member['ts_code'], start_date=start_date, end_date=end_date)
                if 'trade_date' in stock_data:
                    stock_data = stock_data.sort_values(by='trade_date', ascending=True)
                if len(stock_data) >= 4:
                    # Calculate 3-day return for stock
                    stock_3d_return = (stock_data.iloc[0]['close'] /
                                        stock_data.iloc[3]['close'] - 1) * 100
                    # Catch stocks that just started (e.g. 1 limit up or strong move, but not too extended)
                    if 5 < stock_3d_return < 15:
                        strong_stocks.append({
                            'ts_code': member['ts_code'],
                            'name': member['name'],
                            'sector': sector['sector_name'],
                            'sector_return': sector['3d_return'],
                            'stock_3d_return': stock_3d_return,
                            'strategy': '板块动量'
                        })
    except Exception as e:
        logger.error(f"板块动量策略执行出错: {e}")
        raise
    logger.info(f"Got {len(strong_stocks)} stocks from sector momentum strategy.")
    return strong_stocks


# 策略2: 基于资金流向筛选
def money_flow_strategy(stock_basic: pd.DataFrame, start_date: str, end_date: str):
    logger.info("策略2: 资金流向选股")
    strong_stocks = []
    try:
        # 获取资金流向数据 start_date, end_date
        # 由于API限制单次6000行，无法一次获取多日所有股票数据，需按日获取并累加
        accumulated_mf = pd.DataFrame()
        trading_days = get_trading_days_between(start_date, end_date)
        logger.info(f"Fetching money flow data for {len(trading_days)} days from {start_date} to {end_date}...")
        
        for trade_date in trading_days:
            try:
                daily_mf = PRO.moneyflow(trade_date=trade_date)
                if not daily_mf.empty:
                    if accumulated_mf.empty:
                        accumulated_mf = daily_mf[['ts_code', 'net_mf_amount']]
                    else:
                        # Merge and sum net_mf_amount
                        daily_mf_subset = daily_mf[['ts_code', 'net_mf_amount']]
                        accumulated_mf = pd.merge(accumulated_mf, daily_mf_subset, on='ts_code', how='outer', suffixes=('', '_new'))
                        accumulated_mf['net_mf_amount'] = accumulated_mf['net_mf_amount'].fillna(0) + accumulated_mf['net_mf_amount_new'].fillna(0)
                        accumulated_mf = accumulated_mf[['ts_code', 'net_mf_amount']]
                time.sleep(0.1) # Avoid hitting API rate limits
            except Exception as e:
                logger.warning(f"Failed to fetch money flow for {trade_date}: {e}")

        if accumulated_mf.empty:
            logger.warning("No money flow data fetched.")
            return []

        money_flow = accumulated_mf
        # 筛选主力净流入大的股票
        money_flow = money_flow.sort_values('net_mf_amount', ascending=False)
        top_money_flow = money_flow.head(50)
        logger.info("分析主力资金净流入前50的股票")
        top_money_flow = filter_mainboard_stocks(top_money_flow)
        for _, stock in top_money_flow.iterrows():
            basic_info = stock_basic[stock_basic['ts_code'] == stock['ts_code']]
            if not basic_info.empty:
                # 结合价格走势分析
                price_data = PRO.daily(ts_code=stock['ts_code'], start_date=start_date, end_date=end_date)
                if 'trade_date' in price_data:
                    price_data = price_data.sort_values(by='trade_date', ascending=True)
                if len(price_data) > 1:
                    # Calculate return over the period (latest / earliest - 1)
                    price_change = (price_data.iloc[-1]['close'] /
                                  price_data.iloc[0]['close'] - 1) * 100
                    # 主力大幅流入且股价上涨
                    if stock['net_mf_amount'] > 1000 and price_change > 0:  # 净流入超过1000万, unit in 万元
                        strong_stocks.append({
                            'ts_code': stock['ts_code'],
                            'name': basic_info.iloc[0]['name'],
                            'net_mf_amount': stock['net_mf_amount'],
                            'price_change': price_change,
                            'strategy': '资金流向'
                        })
    except Exception as e:
        logger.error(f"资金流向策略执行出错: {e}")
    logger.info(f"Got {len(strong_stocks)} stocks from money flow strategy.")
    return strong_stocks


# 策略3: 基于涨停板数据筛选
def limit_up_strategy(stock_basic: pd.DataFrame, start_date: str, end_date: str):
    logger.info("策略3: 涨停板选股")
    strong_stocks = []
    try:
        # 获取当日涨停股票
        daily_data = PRO.daily(trade_date=end_date)
        if 'trade_date' in daily_data:
            daily_data = daily_data.sort_values(by='trade_date', ascending=True)
        # 筛选涨停股 (假设涨跌幅超过9.5%为涨停)
        limit_up_stocks = daily_data[daily_data['pct_chg'] > 9.5]
        logger.info(f"发现 {len(limit_up_stocks)} 只涨停股票")
        limit_up_stocks = filter_mainboard_stocks(limit_up_stocks)
        for _, stock in limit_up_stocks.iterrows():
            basic_info = stock_basic[stock_basic['ts_code'] == stock['ts_code']]
            if not basic_info.empty:
                # 分析连续涨停情况
                hist_data = PRO.daily(ts_code=stock['ts_code'], start_date=start_date, end_date=end_date)
                if 'trade_date' in hist_data:
                    hist_data = hist_data.sort_values(by='trade_date', ascending=True)
                # 计算连续涨停天数
                consecutive_limit_up = 0
                for i in range(min(RECENT_DAYS, len(hist_data))):
                    if hist_data.iloc[i]['pct_chg'] > 9.5:
                        consecutive_limit_up += 1
                    else:
                        break
                # 首板或二板重点关注
                if consecutive_limit_up < 2:
                    strong_stocks.append({
                        'ts_code': stock['ts_code'],
                        'name': basic_info.iloc[0]['name'],
                        'consecutive_days': consecutive_limit_up,
                        'pct_chg': stock['pct_chg'],
                        'amount': stock['amount'],
                        'strategy': '涨停板'
                    })
    except Exception as e:
        logger.error(f"涨停板策略执行出错: {e}")
    logger.info(f"Got {len(strong_stocks)} stocks from limit up strategy.")
    return strong_stocks


def calculate_stock_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算股票综合评分

    Args:
        df: 包含股票数据的DataFrame

    Returns:
        添加了综合评分的DataFrame
    """
    df = df.copy()

    # 初始化各维度得分
    df['strategy_score'] = 0
    df['momentum_score'] = 0
    df['money_flow_score'] = 0
    df['limit_up_score'] = 0

    # 1. 策略数量得分 (权重: 40%)
    if 'strategy_count' in df.columns:
        max_strategy_count = df['strategy_count'].max()
        if max_strategy_count > 0:
            df['strategy_score'] = (df['strategy_count'] / max_strategy_count) * 40

    # 2. 动量得分 (权重: 30%)
    # 板块动量策略
    momentum_mask = df['strategy'].str.contains('板块动量')
    if momentum_mask.any():
        momentum_stocks = df[momentum_mask]
        if 'stock_3d_return' in df.columns:
            # 归一化处理
            max_momentum = momentum_stocks['stock_3d_return'].max()
            min_momentum = momentum_stocks['stock_3d_return'].min()
            if max_momentum > min_momentum:
                df['momentum_score'] = df.get('momentum_score', np.nan).astype(float) # pyright: ignore
                df.loc[momentum_mask, 'momentum_score'] = (
                    (momentum_stocks['stock_3d_return'] - min_momentum) /
                    (max_momentum - min_momentum) * 30
                )
    # 3. 资金流向得分 (权重: 20%)
    money_flow_mask = df['strategy'].str.contains('资金流向')
    if money_flow_mask.any():
        money_flow_stocks = df[money_flow_mask]
        if 'net_mf_amount' in df.columns:
            # 归一化处理
            max_mf = money_flow_stocks['net_mf_amount'].max()
            min_mf = money_flow_stocks['net_mf_amount'].min()
            if max_mf > min_mf:
                df['money_flow_score'] = df.get('money_flow_score', np.nan).astype(float) # pyright: ignore
                df.loc[money_flow_mask, 'money_flow_score'] = (
                    (money_flow_stocks['net_mf_amount'] - min_mf) /
                    (max_mf - min_mf) * 20
                )

    # 4. 涨停板得分 (权重: 10%)
    limit_up_mask = df['strategy'].str.contains('涨停板')
    if limit_up_mask.any():
        limit_up_stocks = df[limit_up_mask]
        if 'consecutive_days' in df.columns:
            # 连续涨停天数越多得分越高
            max_days = limit_up_stocks['consecutive_days'].max()
            if max_days > 0:
                df['limit_up_score'] = df.get('limit_up_score', np.nan).astype(float) # pyright: ignore
                df.loc[limit_up_mask, 'limit_up_score'] = (
                    limit_up_stocks['consecutive_days'] / max_days * 10
                )

    # 计算综合评分
    df['composite_score'] = (
        df['strategy_score'] +
        df['momentum_score'] +
        df['money_flow_score'] +
        df['limit_up_score']
    )

    # Debug: Check for NaN values
    nan_count = df['composite_score'].isna().sum()
    if nan_count > 0:
        logger.info(f"Found {nan_count} stocks with NaN composite_score")
        logger.info(df[df['composite_score'].isna()][['ts_code', 'composite_score']])
    # 添加排名
    # df['rank'] = df['composite_score'].rank(ascending=False, method='min').astype(int)
    df['rank'] = df['composite_score'].rank(ascending=False, method='min', na_option='bottom').astype(int)

    logger.info("评分系统统计:")
    logger.info(f"策略数量得分范围: {df['strategy_score'].min():.2f} - {df['strategy_score'].max():.2f}")
    logger.info(f"动量得分范围: {df['momentum_score'].min():.2f} - {df['momentum_score'].max():.2f}")
    logger.info(f"资金流向得分范围: {df['money_flow_score'].min():.2f} - {df['money_flow_score'].max():.2f}")
    logger.info(f"涨停板得分范围: {df['limit_up_score'].min():.2f} - {df['limit_up_score'].max():.2f}")
    logger.info(f"综合评分范围: {df['composite_score'].min():.2f} - {df['composite_score'].max():.2f}")
    return df

def is_late_trend(ts_code: str, ref_end_date: str, regime_data: Any = None) -> bool:
    """判断是否为趋势末期/透支行情的个股.

    规则（任一满足即视为晚期趋势）：
    - 收盘价距离20日均线 > 15% (可配置)
    - 最近5日涨幅 > 20% 或 最近10日涨幅 > 30% (可配置)
    - 当日成交量 > 20日均量的 2.0 倍 (可配置)
    """
    # Default thresholds
    ma20_ext_limit = 0.15
    ret_5d_limit = 0.20
    ret_10d_limit = 0.30
    vol_ratio_limit = 2.0
    
    if regime_data:
        thresholds = regime_data.get('filter_thresholds', {})
        ma20_ext_limit = thresholds.get('ma20_extension', ma20_ext_limit)
        ret_5d_limit = thresholds.get('return_5d', ret_5d_limit)
        ret_10d_limit = thresholds.get('return_10d', ret_10d_limit)
        vol_ratio_limit = thresholds.get('volume_ratio', vol_ratio_limit)

    # 获取最近 30 个交易日的K线数据
    lookback_days = 30
    start_k_date = get_trading_days_before(ref_end_date, lookback_days - 1)
    # no need get_kline(has adj(qfq/hfq)), repalce by OHLCV data.
    kline = data_provider.get_ohlcv_data(symbol=ts_code, start_date=start_k_date, end_date=ref_end_date)
    if kline is None or kline.empty or len(kline) < 20:
        logger.warning(f"Get kline failed or insufficient data, ts_code={ts_code}")
        return False

    close = kline["close"].astype(float)
    volume = kline["vol"].astype(float)

    ma20 = close.rolling(20).mean()
    vol_ma20 = volume.rolling(20).mean()

    latest_close = close.iloc[-1]
    latest_ma20 = ma20.iloc[-1]
    latest_vol = volume.iloc[-1]
    latest_vol_ma20 = vol_ma20.iloc[-1] if not np.isnan(vol_ma20.iloc[-1]) else 0.0

    # 1. 价格明显脱离均线，属于透支上涨
    if latest_ma20 > 0 and latest_close > latest_ma20 * (1 + ma20_ext_limit):
        logger.debug(
            f"{ts_code} filtered by MA20 extension: close={latest_close:.2f}, "
            f"ma20={latest_ma20:.2f}, limit={ma20_ext_limit:.2%}"
        )
        return True

    # 2. 最近5/10日涨幅过大
    try:
        if len(close) >= 6:
            ret_5d = latest_close / close.iloc[-6] - 1
            if ret_5d > ret_5d_limit:
                logger.debug(f"{ts_code} filtered by 5d return: {ret_5d:.2%}, limit={ret_5d_limit:.2%}")
                return True
        if len(close) >= 11:
            ret_10d = latest_close / close.iloc[-11] - 1
            if ret_10d > ret_10d_limit:
                logger.debug(f"{ts_code} filtered by 10d return: {ret_10d:.2%}, limit={ret_10d_limit:.2%}")
                return True
    except Exception as e:
        logger.warning(f"计算短期涨幅失败, ts_code={ts_code}, error={e}")
        return True

    # 3. 成交量放大到均量多倍，可能是尾声放量
    if latest_vol_ma20 > 0 and latest_vol > latest_vol_ma20 * vol_ratio_limit:
        logger.debug(
            f"{ts_code} filtered by volume climax: vol={latest_vol:.0f}, "
            f"ma20={latest_vol_ma20:.0f}, limit={vol_ratio_limit}"
        )
        return True
    return False

def pick_strong_stocks(start_date: str, end_date: str) -> pd.DataFrame:
    # 获取股票基本信息
    stock_basic = PRO.stock_basic(exchange='', list_status='L')
    logger.info(f"From {start_date} to {end_date}, fetch short term strong stocks from THS hot concept sectors ...")
    
    # Detect market regime
    regime_data: Any = detect_market_regime(end_date)
    logger.info(f"Market Regime: {regime_data.get('regime')}")

    all_strong_stocks = []
    # 执行三个策略
    all_strong_stocks.extend(sector_momentum_strategy(start_date=start_date, end_date=end_date))
    all_strong_stocks.extend(money_flow_strategy(stock_basic=stock_basic, start_date=start_date, end_date=end_date))
    all_strong_stocks.extend(limit_up_strategy(stock_basic=stock_basic, start_date=start_date, end_date=end_date))
    all_strong_stocks = [dct for dct in all_strong_stocks if isinstance(dct, dict) and 'ts_code' in dct and 'strategy' in dct]
    # 去重并汇总结果
    unique_stocks = {}
    for stock in all_strong_stocks:
        if stock['ts_code'] not in unique_stocks:
            unique_stocks[stock['ts_code']] = stock
        else:
            # 如果同一只股票被多个策略选中，合并策略信息
            stock['strategy'] = unique_stocks[stock['ts_code']]['strategy'] + f", {stock['strategy']}"
            unique_stocks[stock['ts_code']].update(stock)
    if not unique_stocks:
        logger.warning('Not found any strong stocks based on the strategies.')
        return pd.DataFrame()

    result_df = pd.DataFrame(list(unique_stocks.values()))
    # 按策略数量排序 (被多个策略选中的股票更可靠)
    result_df['strategy_count'] = result_df['strategy'].apply(lambda x: len(x.split(',')))
    result_df = result_df.sort_values('strategy_count', ascending=False)
    logger.info(f"Got {len(result_df)} strong stocks")

    # 计算综合评分
    result_df = calculate_stock_scores(result_df)
    # 按综合评分排序
    result_df = result_df.sort_values('composite_score', ascending=False)

    # 可选：输出预筛选 TOP 10，便于调试
    for i, (_, stock) in enumerate(result_df.head(10).iterrows(), 1):
        logger.info(
            f"预筛选TOP{i}: {stock['name']}({stock['ts_code']}) "
            f"rank={stock['rank']} score={stock['composite_score']:.2f}"
        )

    # === 新增：先按晚期趋势规则过滤一遍，避免追高 ===
    filtered_rows = []
    for _, row in result_df.iterrows():
        ts_code = row['ts_code']
        if is_late_trend(ts_code, ref_end_date=end_date, regime_data=regime_data):
            logger.info(f"跳过晚期趋势个股: {row['name']}({ts_code})")
            continue
        filtered_rows.append(row)

    if not filtered_rows:
        logger.info("所有强势股均被晚期趋势规则过滤，回退使用原始结果集。")
        filtered_df = result_df
    else:
        filtered_df = pd.DataFrame(filtered_rows).reset_index(drop=True)

    # Only return on risky-free stocks
    risky_free_list = no_risky_stocks()
    filtered_df = filtered_df[filtered_df['ts_code'].isin(risky_free_list)].reset_index(drop=True)
    logger.info(f"After filtering late-trend and risky stocks, {len(filtered_df)} stocks")
    return filtered_df


def no_risky_stocks() -> list[str]:
    """
    返回不适合短线操作的股票列表
    """
    # Get all stocks (not cached, direct API call)
    basic_info = data_provider.get_basic_information()
    if basic_info.empty:
        raise ValueError("No basic information found")

    # Filter out risky stocks (ST, *ST, etc.)
    name_pattern = r'^(?:C|N|\*?ST|S)|退'
    ts_code_pattern = r'^(?:C|N|\*|4|9|8|30|688)|ST'
    exclude_conditions = (
        basic_info['name'].str.contains(name_pattern, regex=True, na=False) |
        basic_info['ts_code'].str.contains(ts_code_pattern, regex=True, na=False)
    )
    risky_stocks = basic_info[exclude_conditions]['ts_code'].tolist()
    logger.info(f"Filtered out {len(risky_stocks)} risky stocks.")
    all_stocks = basic_info['ts_code'].tolist()
    risky_free_stocks = list(set(all_stocks) - set(risky_stocks))
    return risky_free_stocks


if __name__ == "__main__":
    """
    sector_code = '885333.TI'
    index_code = sector_code.split('.')[0]
    df = ths_member(index_code)
    print(df)
    # akshare limited APi by IP, use adata to get concept members.
    import adata
    df21 = adata.stock.info.all_concept_code_ths()
    print(df21)
    df22 = adata.stock.info.concept_constituent_ths(index_code=index_code)
    print(df22)
    import pdb;pdb.set_trace()
    """

    argv = sys.argv[1:]
    if len(argv) >= 1:
        date = convert_trade_date(argv[0])
    else:
        logger.info("Usage: python -m pick_stocks_from_sector.ts <date YYYYMMDD>")
        date = convert_trade_date('20251120')
    if not date:
        date = datetime.now().strftime('%Y%m%d')
    date = get_trading_days_before(date, 1)
    start_date = get_trading_days_before(date, RECENT_DAYS-1)
    end_date = date
    days = get_trading_days_between(start_date, end_date)
    df = pick_strong_stocks(start_date=start_date, end_date=end_date)
    # Save to /tmp/tmp: {"selected_stocks": [{"rank": 1, "symbol": "603085.SH", "score": 0.94},...]}
    output_file = '/tmp/tmp'
    if len(argv) >=1:
        selected_stocks = []
        for _, stock in df.iterrows():
            selected_stocks.append({
                'rank': int(stock['rank']),
                'symbol': stock['ts_code'],
                'score': float(f"{stock['composite_score']:.2f}")
            })
        with open(output_file, 'w') as f:
            json.dump({'selected_stocks': selected_stocks}, f)
        logger.info(f"Saved picked stocks to {output_file}")
        exit(0)

    print("TOP 10强势股排名--------------------------")
    for i, (_, stock) in enumerate(df.head(10).iterrows(), 1):
        print(f"{i}. {stock['name']}({stock['ts_code']}) - 排名: {stock['rank']} - 综合评分: {stock['composite_score']:.2f}")
        print(f"   策略: {stock['strategy']}")
        print(f"   策略得分: {stock['strategy_score']:.2f}, 动量得分: {stock['momentum_score']:.2f}")
        print(f"   资金得分: {stock['money_flow_score']:.2f}, 涨停得分: {stock['limit_up_score']:.2f}")