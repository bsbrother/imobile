"""
Short-Term Strong Stocks Selection Strategy 
Base on hot search and strong sector data, combined with technical indicators such as volume and turnover rate to select short-term strong stocks is a practical strategy.
This script demonstrates how to implement this strategy using Tushare and AKShare libraries.

## 重要提示
1. **风险控制**: 短线交易风险较高，务必设置止损位
2. **及时性**: 这些信号具有时效性，需要盘中实时监控
3. **综合判断**: 不要仅依赖单一指标，要结合大盘环境、板块轮动等综合分析
4. **仓位管理**: 短线交易建议轻仓操作，控制单笔交易风险

## 📊 建议的监控指标权重
| 指标 | 权重 | 说明 |
|------|------|------|
| 量比 | 40% | 反映资金关注度 |
| 涨幅 | 30% | 反映价格强度 |
| 换手率 | 20% | 反映筹码交换活跃度 |
| 热搜度 | 10% | 反映市场情绪 |

# 根据需要调整筛选参数：
 - `price_change_pct`：涨幅阈值
 - `volume_ratio`：量比阈值
 - `turnover_rate`：换手率阈值
# 可以根据实际需求调整各指标的权重系数
"""
import os
from datetime import datetime
from dotenv import load_dotenv
from loguru import logger
import pandas as pd

import time
import retry
from tenacity import before_sleep_log, retry_if_exception_type, stop_after_attempt, wait_random_exponential

import tushare as ts
import akshare as ak

from backtest.utils.trading_calendar import get_trading_days_before
from backtest.utils.util import convert_trade_date

load_dotenv()
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
if not TUSHARE_TOKEN:
    raise ValueError("Please set the TUSHARE_TOKEN environment variable.")
PRO = ts.pro_api(TUSHARE_TOKEN) # # pyright: ignore
LOOKBACK_DAYS = 10  # trading days lookback, almost 2 weeks.
RECENT_DAYS = 5     # recent days to calculate returns


@retry(
    stop=stop_after_attempt(2),
    wait=wait_random_exponential(multiplier=0.2, min=1, max=2),
    retry=retry_if_exception_type(Exception),
    before_sleep=before_sleep_log(tenacity_logger, logging.INFO)
)
def _ak_call(self, func, **kwargs) -> pd.DataFrame:
    for date_param in ['start_date', 'end_date', 'trade_date']:
        if date_param in kwargs and kwargs[date_param]:
            kwargs[date_param] = convert_trade_date(kwargs[date_param])

    time.sleep(self.rate_limit_delay)
    df = func(**kwargs)
    if df is None or not isinstance(df, pd.DataFrame):
        raise ValueError("Invalid response from Akshare API")
    if df.empty:
        time.sleep(self.rate_limit_delay)
        df = func(**kwargs)
    return df


def get_sectors_stocks_ts_cpt(date: str | None = None) -> list:
    """
    Get hot and strong sectors top 10 with stocks by Tushare API limit_cpt_list at date.
    """
    date = convert_trade_date(date)
    if not date:
        date = datetime.now().strftime('%Y%m%d')
    previous_date = get_trading_days_before(date, 1)
    df_strong_sectors = PRO.limit_cpt_list(trade_date=previous_date)
    if df_strong_sectors is None or df_strong_sectors.empty:
        return []
    df_concept_stocks = PRO.get_concept_classified()
    hot_stocks = []
    for _, sector in df_strong_sectors.head(10).iterrows():
        sector_name = sector['name']
        sector_stocks = df_concept_stocks[df_concept_stocks['c_name'] == sector_name]
        hot_stocks.append(sector_stocks)
    if hot_stocks:
        all_hot_stocks = pd.concat(hot_stocks, ignore_index=True)
        return all_hot_stocks['code'].unique().tolist()[:10]
    return []

def get_sectors_stocks_ts_ths(date: str | None = None) -> list:
    """
    Get hot and strong sectors top 10 with stocks by Tushare API ths_index and ths_daily.
    """
    date = convert_trade_date(date)
    if not date:
        date = datetime.now().strftime('%Y%m%d')
    previous_date = get_trading_days_before(date, 1)
    end_date = previous_date
    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS)
    df_ths_index = PRO.ths_index() # Get all Ths sectors
    hot_sectors_data = []
    for i in range(0, len(df_ths_index), 50):  # every time fetch 50 sectors, avoid request too large.
        batch_codes = df_ths_index['ts_code'].iloc[i:i+50].tolist()
        for ts_code in batch_codes:
            try:
                df_sector = PRO.ths_daily(ts_code=ts_code,
                                        start_date=start_date,
                                        end_date=end_date,
                                        fields='ts_code,trade_date,close,pct_change,vol,amount')
                if not df_sector.empty:
                    latest = df_sector.iloc[-1]
                    if len(df_sector) > 1:
                        prev_close = df_sector.iloc[-2]['close']
                        price_change_pct = (latest['close'] - prev_close) / prev_close * 100
                    else:
                        price_change_pct = latest.get('pct_change', 0)
                    sector_info = {
                        'ts_code': latest['ts_code'],
                        'trade_date': latest['trade_date'],
                        'close': latest['close'],
                        'pct_change': price_change_pct,
                        'volume': latest.get('vol', 0),
                        'amount': latest.get('amount', 0)
                    }
                    hot_sectors_data.append(sector_info)
            except Exception as e:
                logger.error(f"获取板块 {ts_code} 数据失败: {e}")
                continue
    if not hot_sectors_data:
        logger.warning("未获取到板块数据")
        return []
    df_hot_sectors = pd.DataFrame(hot_sectors_data)
    # 筛选强势板块：涨幅前20且成交额不为0
    df_strong_sectors = df_hot_sectors[
        (df_hot_sectors['pct_change'] > 0) &
        (df_hot_sectors['amount'] > 0)
    ].nlargest(20, 'pct_change')
    logger.info(f"筛选出 {len(df_strong_sectors)} 个强势板块")
    # 获取强势板块的成分股
    hot_stocks = []
    for _, sector in df_strong_sectors.iterrows():
        try:
            # 获取板块成分股
            df_members = PRO.ths_member(ts_code=sector['ts_code'])
            if not df_members.empty:
                # 添加板块强度信息
                df_members['sector_pct_change'] = sector['pct_change']
                df_members['sector_ts_code'] = sector['ts_code']
                hot_stocks.append(df_members)
        except Exception as e:
            logger.error(f"获取板块 {sector['ts_code']} 成分股失败: {e}")
            continue
    if hot_stocks:
        all_hot_stocks = pd.concat(hot_stocks, ignore_index=True)
        return all_hot_stocks['code'].unique().tolist()[:10]
    else:
        return []


def get_sectors_stocks_ts(date: str | None = None) -> list:
    """
    Get constituent stocks from hot and strong sectors top 10 by Tushare API.
    """
    date = convert_trade_date(date)
    if not date:
        date = datetime.now().strftime('%Y%m%d')
    try:
        return get_sectors_stocks_ts_cpt(date=date)
    except Exception as e:
        logger.warning(f"Error fetching sector data by Tushare limit_cpt_list: {e}")
        logger.warning("Falling back to alternative method using ths_index and ths_daily.")
        return get_sectors_stocks_ts_ths(date=date)


def get_sectors_stocks_ak(date: str | None = None) -> list:
    """
    Get constituent stocks from hot and strong sectors top 10 by Tushare API.
    """
    date = convert_trade_date(date)
    if not date:
        date = datetime.now().strftime('%Y%m%d')
    previous_date = get_trading_days_before(date, 1)
    end_date = previous_date
    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS)
    # 根据涨幅筛选强势板块
    board_list_df = ak.stock_board_concept_name_em()
    strong_sectors = []
    for idx, row in board_list_df.iterrows():
        sector_name = row['板块名称']
        sector_code = row['板块代码']
        try:
            hist_data = ak.stock_board_concept_hist_em(symbol=sector_name, period='daily', start_date=start_date, end_date=end_date, adjust="")
            if not hist_data.empty:
                recent_return = (hist_data.iloc[-1]['收盘'] / hist_data.iloc[0-RECENT_DAYS]['收盘'] - 1) * 100
                strong_sectors.append({'板块名称': sector_name, '板块代码': sector_code, '近期涨幅%': round(recent_return, 2)})
        except Exception as e:
            logger.warning(f"获取板块 {sector_name} 数据时出错: {e}")
            continue
    # Top 20 strong sectors sort by recent_return.
    strong_sectors_df = pd.DataFrame(strong_sectors).sort_values('近期涨幅%', ascending=False).head(20)
    logger.info("强势板块列表:")
    logger.info(strong_sectors_df)

    all_hot_stocks_from_sectors = []
    for _, sector in strong_sectors_df.iterrows():
        try:
            cons_df = ak.stock_board_concept_cons_em(symbol=sector['板块代码'])
            cons_df['所属强势板块'] = sector['板块名称']
            all_hot_stocks_from_sectors.append(cons_df)
        except Exception as e:
            logger.warning(f"获取板块 {sector['板块名称']} 的成分股失败: {e}")
            continue
    # Combine all constituent stocks
    if all_hot_stocks_from_sectors:
        hot_stocks_df = pd.concat(all_hot_stocks_from_sectors, ignore_index=True)
        hot_stock_codes = hot_stocks_df['代码'].unique().tolist()[:10]
        logger.info(f"\n从强势板块中获取到 {len(hot_stock_codes)} 只候选股票")
        return hot_stock_codes
    return []


def get_stock_technical_data_ak(stock_codes: list, date: str | None = None) -> pd.DataFrame:
    """
    Get technical indicator data for top 10 stocks, including volume ratio, turnover rate, etc.
    """
    date = convert_trade_date(date)
    if not date:
        date = datetime.now().strftime('%Y%m%d')
    previous_date = get_trading_days_before(date, 1)
    end_date = previous_date
    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS)
    technical_data = []
    for code in stock_codes[:10]:
        try:
            stock_data = ak.stock_zh_a_hist(symbol=code, period="daily",
                                          start_date=start_date, end_date=end_date,
                                          adjust="qfq")
            latest = stock_data.iloc[-1]
            prev = stock_data.iloc[-2]

            volume_ratio = latest['成交量'] / stock_data['成交量'].tail(RECENT_DAYS).mean()  # 量比
            volume_trend = '上升' if latest['成交量'] > prev['成交量'] else '下降'

            price_change = (latest['收盘'] - prev['收盘']) / prev['收盘'] * 100
            amplitude = (latest['最高'] - latest['最低']) / prev['收盘'] * 100  # 振幅

            turnover_rate = latest.get('换手率', 0)

            stock_info = {
                'code': code,
                'name': f"股票{code}",
                'close': latest['收盘'],
                'price_change_pct': price_change,
                'volume_ratio': volume_ratio,
                'volume_trend': volume_trend,
                'amplitude': amplitude,
                'turnover_rate': turnover_rate,
                'sector_strength': '热门板块'
            }
            technical_data.append(stock_info)
        except Exception as e:
            logger.warning(f"获取{code}数据失败: {e}")
            continue

    return pd.DataFrame(technical_data)


def screen_short_term_strong_stocks_ak(date: str | None = None):
    """
    Filter short-term strong stocks based on technical indicators
    """
    date = convert_trade_date(date)
    if not date:
        date = datetime.now().strftime('%Y%m%d')
    hot_stock_codes = get_sectors_stocks_ak(date=date)
    if not hot_stock_codes:
        logger.warning("未获取到热门板块股票")
        return pd.DataFrame()

    df_stocks = get_stock_technical_data_ak(hot_stock_codes, date=date)
    if df_stocks.empty:
        return pd.DataFrame()

    df_filtered = df_stocks[
        (df_stocks['price_change_pct'] > 3) &  # 涨幅超过3%
        (df_stocks['volume_ratio'] > 1.5) &    # 量比大于1.5
        (df_stocks['volume_trend'] == '上升') & # 成交量上升
        (df_stocks['turnover_rate'] > 5)       # 换手率大于5%
    ]

    df_filtered['score'] = (
        df_filtered['volume_ratio'] * 0.4 +
        df_filtered['price_change_pct'] * 0.3 +
        df_filtered['turnover_rate'] * 0.3
    )

    df_sorted = df_filtered.sort_values('score', ascending=False)

    return df_sorted


def combine_with_baidu_hot_search_ak(date: str | None = None):
    """
    Extend the short-term strong stock selection by incorporating Baidu hot search data
    """
    date = convert_trade_date(date)
    if not date:
        date = datetime.now().strftime('%Y%m%d')
    df_technical = screen_short_term_strong_stocks_ak(date=date)
    try:
        df_hot_search = ak.stock_hot_search_baidu(symbol="A股", date=date)
        if df_technical.empty or df_hot_search.empty:
            return df_technical
        # 合并热搜热度
        # 假设热搜数据中有股票代码和搜索量
        # 这里需要根据实际数据结构调整
        merged_df = pd.merge(df_technical, df_hot_search, on='code', how='left')
        # 如果有搜索量数据，可以加权计算最终得分
        if 'search_volume' in merged_df.columns:
            merged_df['final_score'] = (
                merged_df['score'] * 0.7 +
                (merged_df['search_volume'] / merged_df['search_volume'].max()) * 0.3
            )
            merged_df = merged_df.sort_values('final_score', ascending=False)
        return merged_df
    except Exception as e:
        logger.warning(f"结合热搜数据失败: {e}")
        return df_technical


def comprehensive_short_term_screener_ak(date: str | None = None):
    """
    comprehensive short-term strong stock screener
    """
    date = convert_trade_date(date)
    if not date:
        date = datetime.now().strftime('%Y%m%d')
    print("开始筛选短线强势股...")
    df_technical = screen_short_term_strong_stocks_ak(date=date)
    df_with_hot_search = combine_with_baidu_hot_search_ak(date=date)

    print("\n=== 技术指标筛选结果 ===")
    if not df_technical.empty:
        for _, stock in df_technical.iterrows():
            print(f"代码: {stock['code']}, 涨幅: {stock['price_change_pct']:.2f}%, "
                  f"量比: {stock['volume_ratio']:.2f}, 换手率: {stock['turnover_rate']:.2f}%")

    print("\n=== 结合热搜筛选结果 ===")
    if not df_with_hot_search.empty:
        for _, stock in df_with_hot_search.iterrows():
            print(f"代码: {stock['code']}, 综合得分: {stock.get('final_score', stock['score']):.2f}")

    return df_with_hot_search if not df_with_hot_search.empty else df_technical


if __name__ == "__main__":
    strong_stocks = screen_short_term_strong_stocks_ak(date='20251001')
    print("筛选出的短线强势股:")
    print(strong_stocks[['code', 'name', 'price_change_pct', 'volume_ratio', 'turnover_rate', 'score']])
    exit(0)