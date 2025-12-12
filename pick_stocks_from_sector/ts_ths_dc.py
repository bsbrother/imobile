"""
Pick short-term strong stocks from hot concept sectors by Tushare THS/DC API.
[Tushare API 打板专题数据](https://tushare.pro/document/2?doc_id=346)

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
import warnings
from typing import Any

import tushare as ts

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import data_provider
from backtest.utils.trading_calendar import get_trading_days_before, get_trading_days_between
from backtest.utils.util import convert_trade_date
from backtest.utils.market_regime import detect_market_regime

warnings.filterwarnings("ignore", category=UserWarning, module='py_mini_racer')

load_dotenv()
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
if not TUSHARE_TOKEN:
    raise ValueError("Please set the TUSHARE_TOKEN environment variable.")
PRO = ts.pro_api(TUSHARE_TOKEN)     # pyright: ignore
RECENT_DAYS = 5                     # recent days to calculate returns
LOOKBACK_DAYS = RECENT_DAYS * 4     # trading days lookback, almost 4 weeks, 1 month.

def get_concept_sectors(start_date: str, end_date: str, src: str='ts_ths') -> pd.DataFrame:
    """
    获取所有概念板块

    Args:
        start_date: YYYYMMDD
        end_date: YYYYMMDD
        src: 数据源，'ts_ths' or 'ts_dc', default is ts_ths.

    Returns:
        DataFrame: 概念板块列表. https://tushare.pro/document/2?doc_id=362, ts_ths code: 886105.TI, ts_dc code: BK0052.DC
        ts_ths: ts_code, name, count, exchange, list_date, type
        ts_dc: ts_code, ts_date, name, leading, leading_code, pct_change, leading_pct, total_mv, turnover_rate, up_num, down_num
    """
    if src == 'ts_ths':
        concept_list = PRO.ths_index(exchange='A', type='N')
    else:
        concept_list = PRO.dc_index(start_date=start_date, end_date=end_date)
    if 'trade_date' in concept_list:
        concept_list = concept_list.sort_values(by='trade_date', ascending=False)
    logger.info(f"Got {len(concept_list)} concept sectors index records.")
    return concept_list


def batch_get_concept_daily(concept_list: pd.DataFrame, start_date: str, end_date: str, src: str='ts_ths') -> pd.DataFrame:
    """
    批量获取概念板块日线数据，直到获取所有概念板块的完整数据

    Args:
        concept_list: 概念板块列表
        start_date: YYYYMMDD
        end_date: YYYYMMDD
        src: 数据源，'ts_ths' or 'ts_dc', default is ts_ths.

    Returns:
        DataFrame: 所有概念板块在指定日期范围内的日线数据. https://tushare.pro/document/2?doc_id=260
        ts_ths: ts_code, trade_date, open, high, low, close, pre_close, avg_price, change, pct_change, vol, turnover_rate
        ts_dc: ts_code, trade_date, close, open, high, low, change, pct_change, vol, amount, swing, turnover_rate, category
    """
    logger.info(f'Start fetch concept daily data from {start_date} to {end_date} ...')
    concept_codes = set(concept_list['ts_code'].tolist())
    logger.info(f"Got {len(concept_codes)} concept codes.")

    # Got all concepts/sectors from [ths_index](https://tushare.pro/document/2?doc_id=260)
    # Obtain daily data for all sectors(3000 records/once) in order to avoid frequent API call limits(5 times/minute).
    # Each day records < 3000, max 3000/1 time. end_date - start_date = RECENT_DAYS days to get all concepts.
    all_concept_daily = pd.DataFrame()
    for date in get_trading_days_between(start_date, end_date):
        if src == 'ts_ths':
            concept_daily = PRO.ths_daily(start_date=date, end_date=date)
        else:
            concept_daily = PRO.dc_daily(start_date=date, end_date=date)
        if 'trade_date' in concept_daily:
            concept_daily = concept_daily.sort_values(by='trade_date', ascending=False)
        # 过滤出概念板块
        concept_daily = concept_daily[concept_daily['ts_code'].isin(concept_codes)]
        logger.info(f"{date} concept daily records: {len(concept_daily)}")
        all_concept_daily = pd.concat([all_concept_daily, concept_daily], ignore_index=True)
        time.sleep(1)
    logger.info(f"Got {len(all_concept_daily)} concept sectors daily records from {start_date} to {end_date}.")
    return all_concept_daily


# 策略1: 基于板块动量筛选强势股
def sector_momentum_strategy(stock_basic: pd.DataFrame, concept_list: pd.DataFrame, start_date: str, end_date: str, src: str='ts_ths') -> pd.DataFrame:
    logger.info("策略1: 板块动量选股")
    strong_stocks = []
    try:
        all_concept_daily = batch_get_concept_daily(concept_list=concept_list, start_date=start_date, end_date=end_date, src=src)
        if src == 'ts_ths':
            sector_data = pd.merge(all_concept_daily, concept_list[['ts_code', 'name']], on=['ts_code'], how='left')
        else:
            sector_data = pd.merge(all_concept_daily, concept_list[['ts_code', 'trade_date', 'name']], on=['ts_code', 'trade_date'], how='left')
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
            if src == 'ts_ths':
                # ts_code, con_code, con_name
                members = PRO.ths_member(ts_code=sector['sector_code'])
            else:
                # trade_date, ts_code, con_code, name 
                members = PRO.dc_member(ts_code=sector['sector_code'])
            # filter members from start_date to end_date and no-risk mainboard stocks
            members = members[members['con_code'].isin(stock_basic['ts_code'])].reset_index(drop=True)
            if src == 'ts_dc':
                members = members[(members['trade_date'] >= start_date) & (members['trade_date'] <= end_date)]

            sector_stocks = []
            logger.info(f'Get sector ({sector["sector_code"]}){sector["sector_name"]} {len(members)} members daily data from {start_date} to {end_date} ...')
            for _, member in members.iterrows():
                stock_data = data_provider.get_ohlcv_data(symbol=member['con_code'], start_date=start_date, end_date=end_date)
                if 'trade_date' in stock_data:
                    stock_data = stock_data.sort_values(by='trade_date', ascending=False)
                if len(stock_data) >= 4:
                    # Calculate 3-day return for stock
                    stock_3d_return = (stock_data.iloc[0]['close'] /
                                        stock_data.iloc[3]['close'] - 1) * 100
                    
                    # [MODIFIED] Remove < 15% cap to allow Dragon stocks
                    # Only filter out weak stocks (< 5%)
                    if stock_3d_return > 3:
                        sector_stocks.append({
                            'ts_code': member['con_code'],
                            'name': member['con_name'] if 'con_name' in member else member['name'],
                            'sector': sector['sector_name'],
                            'sector_return': sector['3d_return'],
                            'stock_3d_return': stock_3d_return,
                            'strategy': '板块动量'
                        })
            
            # Identify Sector Leader (Dragon)
            if sector_stocks:
                sector_stocks.sort(key=lambda x: x['stock_3d_return'], reverse=True)
                # Mark the top stock as Leader
                sector_stocks[0]['is_leader'] = True
                # Add top 3 to strong_stocks
                strong_stocks.extend(sector_stocks[:3])

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
                raise

        if accumulated_mf.empty:
            logger.warning("No money flow data fetched.")
            return []

        money_flow = accumulated_mf
        # 筛选主力净流入大的股票
        money_flow = money_flow.sort_values('net_mf_amount', ascending=False)
        top_money_flow = money_flow.head(100) # Increase candidate pool
        top_money_flow = top_money_flow[top_money_flow['ts_code'].isin(stock_basic['ts_code'])].reset_index(drop=True)
        logger.info("分析主力资金净流入前100的股票")
        # [MODIFIED] Switch to Relative Money Flow (Net Inflow / Circulating Market Cap)
        # Need to fetch daily_basic for circ_mv
        for _, stock in top_money_flow.iterrows():
            basic_info = stock_basic[stock_basic['ts_code'] == stock['ts_code']]
            if not basic_info.empty:
                # Fetch fundamental data for circ_mv
                daily_basic = data_provider.get_fundamental_data(stock['ts_code'], end_date, end_date)
                if daily_basic.empty:
                    continue
                    
                circ_mv = daily_basic.iloc[0]['circ_mv'] # 万元
                if circ_mv <= 0:
                    continue
                    
                net_mf = stock['net_mf_amount'] # 万元
                
                # Relative Money Flow Ratio
                mf_ratio = net_mf / circ_mv
                
                # 结合价格走势分析
                price_data = data_provider.get_ohlcv_data(symbol=stock['ts_code'], start_date=start_date, end_date=end_date)
                if len(price_data) < 4:
                    logger.warning(f"Not enough price data for {stock['ts_code']}, skip it.")
                    continue
                if 'trade_date' in price_data:
                    price_data = price_data.sort_values(by='trade_date', ascending=False)
                if len(price_data) > 1:
                    price_change = (price_data.iloc[0]['close'] / price_data.iloc[3]['close'] - 1) * 100
                    
                    # [MODIFIED] Thresholds: 
                    # 1. Relative Inflow > 0.5% of Circ Cap (Significant buying)
                    # 2. Price Trend > 0 (Upward)
                    if mf_ratio > 0.005 and price_change > 0:
                        strong_stocks.append({
                            'ts_code': stock['ts_code'],
                            'name': basic_info.iloc[0]['name'],
                            'net_mf_amount': net_mf,
                            'mf_ratio': mf_ratio,
                            'price_change': price_change,
                            'strategy': '资金流向'
                        })
    except Exception as e:
        logger.error(f"资金流向策略执行出错: {e}")
        import traceback
        traceback.print_exc()
        raise
    logger.info(f"Got {len(strong_stocks)} stocks from money flow strategy.")
    return strong_stocks


# 策略3: 基于涨停板数据筛选
def limit_up_strategy(stock_basic: pd.DataFrame, start_date: str, end_date: str):
    logger.info("策略3: 涨停板选股")
    strong_stocks = []
    try:
        # 获取当日涨停股票
        daily_data = data_provider.get_stock_data(symbols=stock_basic['ts_code'].tolist(), start_date=start_date, end_date=end_date)
        if 'trade_date' in daily_data:
            daily_data = daily_data.sort_values(by='trade_date', ascending=False)
        # 筛选涨停股 (假设涨跌幅超过9.5%为涨停)
        limit_up_stocks = daily_data[daily_data['pct_chg'] > 9.5]
        limit_up_stocks = limit_up_stocks[limit_up_stocks['ts_code'].isin(stock_basic['ts_code'])].reset_index(drop=True)
        logger.info(f"发现 {len(limit_up_stocks)} 只涨停股票")
        for _, stock in limit_up_stocks.iterrows():
            basic_info = stock_basic[stock_basic['ts_code'] == stock['ts_code']]
            if not basic_info.empty:
                # 分析连续涨停情况
                hist_data = data_provider.get_ohlcv_data(symbol=stock['ts_code'], start_date=start_date, end_date=end_date)
                if 'trade_date' in hist_data:
                    hist_data = hist_data.sort_values(by='trade_date', ascending=False)
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
        raise
    logger.info(f"Got {len(strong_stocks)} stocks from limit up strategy.")
    return strong_stocks


def calculate_stock_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算股票综合评分
    """
    df = df.copy()

    # 初始化各维度得分
    df['strategy_score'] = 0
    df['momentum_score'] = 0
    df['money_flow_score'] = 0
    df['limit_up_score'] = 0
    df['leader_score'] = 0 # [NEW]

    # 1. 策略数量得分 (权重: 30%)
    if 'strategy_count' in df.columns:
        max_strategy_count = df['strategy_count'].max()
        if max_strategy_count > 0:
            df['strategy_score'] = (df['strategy_count'] / max_strategy_count) * 20

    # 2. 动量得分 (权重: 25%)
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
                    (max_momentum - min_momentum) * 20
                )
    # 3. 资金流向得分 (权重: 15%)
    money_flow_mask = df['strategy'].str.contains('资金流向')
    if money_flow_mask.any():
        money_flow_stocks = df[money_flow_mask]
        if 'mf_ratio' in df.columns: # Use mf_ratio
            # 归一化处理
            max_mf = money_flow_stocks['mf_ratio'].max()
            min_mf = money_flow_stocks['mf_ratio'].min()
            if max_mf > min_mf:
                df['money_flow_score'] = df.get('money_flow_score', np.nan).astype(float) # pyright: ignore
                df.loc[money_flow_mask, 'money_flow_score'] = (
                    (money_flow_stocks['mf_ratio'] - min_mf) /
                    (max_mf - min_mf) * 30
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
                
    # 5. 龙头得分 (权重: 20%) [NEW]
    if 'is_leader' in df.columns:
        df.loc[df['is_leader'] == True, 'leader_score'] = 20

    # 计算综合评分
    df['composite_score'] = (
        df['strategy_score'] +
        df['momentum_score'] +
        df['money_flow_score'] +
        df['limit_up_score'] +
        df['leader_score']
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
    logger.info(f"龙头得分范围: {df['leader_score'].min():.2f} - {df['leader_score'].max():.2f}")
    logger.info(f"综合评分范围: {df['composite_score'].min():.2f} - {df['composite_score'].max():.2f}")
    return df

def is_late_trend(ts_code: str, ref_end_date: str, regime_data: Any = None) -> bool:
    """判断是否为趋势末期/透支行情的个股.
    
    [MODIFIED] Uses dynamic thresholds based on market regime.
    """
    # Default thresholds (Normal Market)
    ma20_ext_limit = 0.25
    ret_5d_limit = 0.30
    ret_10d_limit = 0.50
    vol_ratio_limit = 2.5
    
    if regime_data:
        # Get late_trend_filter config from regime data
        filter_config = regime_data.get('late_trend_filter', {})
        if filter_config:
            ma20_ext_limit = filter_config.get('ma_threshold', ma20_ext_limit)
            # Convert threshold (e.g. 1.4) to extension limit (0.4)
            if ma20_ext_limit > 1.0:
                ma20_ext_limit -= 1.0
                
            ret_5d_limit = filter_config.get('short_gain_threshold', ret_5d_limit)
            ret_10d_limit = filter_config.get('mid_gain_threshold', ret_10d_limit)
            vol_ratio_limit = filter_config.get('volume_multiplier', vol_ratio_limit)
        else:
            # Fallback to simple mapping if config not present
            regime = regime_data.get('regime', 'normal')
            if regime == 'bull':
                ma20_ext_limit = 0.60
                ret_5d_limit = 0.60
                ret_10d_limit = 1.00
                vol_ratio_limit = 4.0
            elif regime == 'volatile':
                ma20_ext_limit = 0.20
                ret_5d_limit = 0.20
                ret_10d_limit = 0.35
                vol_ratio_limit = 2.5
            elif regime == 'bear':
                ma20_ext_limit = 0.15
                ret_5d_limit = 0.15
                ret_10d_limit = 0.25
                vol_ratio_limit = 2.0
            
    # 获取最近 30 个交易日的K线数据
    lookback_days = 30
    start_k_date = get_trading_days_before(ref_end_date, lookback_days - 1)
    # no need get_kline(has adj(qfq/hfq)), repalce by OHLCV data.
    kline = data_provider.get_ohlcv_data(symbol=ts_code, start_date=start_k_date, end_date=ref_end_date)
    # TODO: use stock_basic list_date to no-limit need >= 20 days data.
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
        raise

    # 3. 成交量放大到均量多倍，可能是尾声放量
    if latest_vol_ma20 > 0 and latest_vol > latest_vol_ma20 * vol_ratio_limit:
        logger.debug(
            f"{ts_code} filtered by volume climax: vol={latest_vol:.0f}, "
            f"ma20={latest_vol_ma20:.0f}, limit={vol_ratio_limit}"
        )
        return True
    return False

def pick_strong_stocks(start_date: str, end_date: str, src: str='ts_ths') -> pd.DataFrame:
    """
    Pick strong stocks based on different sources.

    Args:
        start_date (str): Start date.
        end_date (str): End date.
        src (str): Source of data, ts_ths|ts_dc default is ts_ths.
    """
    # 获取股票基本信息
    stock_basic = data_provider.get_basic_information_api()
    if stock_basic.empty:
        raise ValueError("No basic information found")
    total_stocks = len(stock_basic)
    # Filter out stocks that are not mainboard
    risky_free_list = no_risky_stocks(stock_basic=stock_basic)
    stock_basic = stock_basic[stock_basic['ts_code'].isin(risky_free_list)].reset_index(drop=True)
    logger.info(f"Total stocks: {total_stocks}, after filtering risky, no-mainboard stocks, {len(stock_basic)} stocks")

    # Detect market regime
    regime_data: Any = detect_market_regime(end_date)
    logger.info(f"Market Regime: {regime_data.get('regime')}")

    logger.info(f"From {start_date} to {end_date}, fetch short term strong stocks from THS hot concept sectors ...")
    concept_list = get_concept_sectors(start_date=start_date, end_date=end_date, src=src)

    all_strong_stocks = []
    # 执行三个策略
    all_strong_stocks.extend(sector_momentum_strategy(stock_basic=stock_basic, concept_list=concept_list, start_date=start_date, end_date=end_date, src=src))
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

    return filtered_df


def no_risky_stocks(stock_basic: pd.DataFrame, mainboard: bool = True) -> list[str]:
    """
    return default is only no risk mainboard stocks.
    Usage:
    risky_free_list = no_risky_stocks(stock_basic=stock_basic)
    filtered_df = filtered_df[filtered_df['ts_code'].isin(risky_free_list)].reset_index(drop=True)

    Args:
        stock_basic (pd.DataFrame): Stock basic information.
        mainboard (bool, optional): Whether to include mainboard stocks. Defaults to True.
    
    Returns:
        list[str]: List of stocks that are suitable for short-term trading.
    """
    # Filter out risky stocks (ST, *ST, New, etc.)
    name_pattern = r'^(?:C|N|\*?ST|S)|退'
    # 沪市主板股票代码以600/601/603/605开头，科创板股票代码以688开头。
    # 深市主板股票代码以000/001/002/003/004开头，创业板股票代码以300/301开头。
    # 北交所股票代码以8|92开头。 新三板: 400/430/830开头。
    # Default only mainboard stocks
    ts_code_pattern = r'^(?:C|N|\*|4|9|8|30|688)|ST' if mainboard else r'^(?:C|N|\*|9|8|)|ST'
    exclude_conditions = (
        stock_basic['name'].str.contains(name_pattern, regex=True, na=False) |
        stock_basic['ts_code'].str.contains(ts_code_pattern, regex=True, na=False)
    )
    risky_stocks = stock_basic[exclude_conditions]['ts_code'].tolist()
    logger.info(f"Filtered out {len(risky_stocks)} risky stocks.")
    all_stocks = stock_basic['ts_code'].tolist()
    risky_free_stocks = list(set(all_stocks) - set(risky_stocks))
    return risky_free_stocks


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) >=2:
        src = argv[1]
        date = convert_trade_date(argv[0])
    elif len(argv) >= 1:
        src = 'ts_ths'
        date = convert_trade_date(argv[0])
    else:
        src = 'ts_ths'
        date = datetime.now().strftime('%Y%m%d')
    if src not in ['ts_ths', 'ts_dc']:
        logger.error("Usage: python -m pick_stocks_from_sector.ts_ths_dc <date YYYYMMDD> <ts_ths | ts_dc>")
        exit(1)

    # Save to /tmp/tmp: {"selected_stocks": [{"rank": 1, "symbol": "603085.SH", "score": 0.94},...]}
    output_file = '/tmp/tmp'
    date = get_trading_days_before(date, 1)
    start_date = get_trading_days_before(date, RECENT_DAYS-1)
    end_date = date
    days = get_trading_days_between(start_date, end_date)
    df = pick_strong_stocks(start_date=start_date, end_date=end_date, src=src)
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