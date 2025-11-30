"""
要通过 AKShare 获取每日热门趋势板块及相关股票，并快速筛选出短线强势股，可以综合运用其**板块行情、概念成分、机构评级、实时行情及市场热度**等多类接口。下面将为您梳理核心接口、构建分析流程，并提供可直接运行的代码示例。

## 🔍 核心接口与数据维度

首先，下表汇总了实现该目标所需的核心 AKShare 接口及其用途，您可以根据需要组合使用：

| 数据维度 | 接口名称 | 核心功能与产出 | 关键参数说明 |
| :--- | :--- | :--- | :--- |
| **板块强弱** | `stock_board_concept_hist_em` | 获取特定**概念板块的历史行情**，用于计算板块强度。 | `symbol`：板块名称 |
| **板块成分** | `stock_board_concept_cons_em` | 获取指定板块内的**所有成分股列表**。 | `symbol`：板块代码 |
| **机构关注** | `stock_institute_recommend` | 获取机构**最新投资评级**，识别受关注的股票。 | `symbol`：数据类别，如"最新投资评级" |
| **实时行情** | `stock_zh_a_spot_em` | 获取沪深京A股全市场的**实时行情**，包含量价数据。 | 无参数 |
| **市场热度** | `stock_hot_search_baidu` | 获取股票的**百度搜索热度**，反映市场情绪。 | `symbol`: 市场类型，如"A股" |

## 💡 使用建议与注意事项

1.  **数据时效性与更新**：机构评级数据通常每日更新，建议在**交易日下午16:00后**调用以获取最新评级。实时行情接口在交易时间内持续更新。
2.  **接口调用频率**：避免高频请求AKShare接口，建议间隔时间**大于60秒**，以防被暂时封禁IP。
3.  **策略组合与优化**：
    *   上述代码是一个基础框架，你可以调整筛选条件（如涨幅、量比、换手率的阈值）来适应不同的市场风格。
    *   可以引入更多技术指标（如RPS相对强度）或结合多个时间周期的数据进行综合判断。
    *   机构推荐和百度热搜数据带有一定的**滞后性**和**市场情绪**，更适合作为辅助参考，不宜作为唯一的决策依据。
4.  **依赖环境**：确保你的AKShare库是最新版本 (`pip install akshare --upgrade`)。

希望这个综合的方案能帮助你有效地利用AKShare挖掘市场热点。如果你在实践过程中遇到具体的技术问题，或者想针对某个筛选维度进行更深入的探讨，随时可以再来问我。
"""

# 综合分析流程与代码实现
# 接下来的代码将串联上述接口，构建一个从板块到个股的分析流程。

## 步骤 1：获取强势板块
### 思路是获取板块历史数据，通过计算其相对强度（例如涨幅、RPS等）来筛选出近期强势板块。

import pandas as pd

import akshare as ak
import adata

from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from utils.stock_code_name_valid import convert_akcode_to_tushare

# Create a standard logging logger for tenacity
tenacity_logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=UserWarning, module='py_mini_racer')

def get_concept_sectors(start_date: str, end_date: str, src: str='ts_ths') -> pd.DataFrame:
    """
    获取所有概念板块
    """
    if src == 'ak_ths':
        concept_list = PRO.ths_index(exchange='A', type='N')
    elif src == 'adata_dc':
        concept_list = adata.stock.info.all_concept_code_east()
        concept_list = pd.DataFrame(concept_list)
        concept_list['ts_code'] = concept_list['index_code']
        concept_list['name'] = concept_list['name']
        concept_list['trade_date'] = concept_list['date']
    if 'trade_date' in concept_list:
        concept_list = concept_list.sort_values(by='trade_date', ascending=True)
    logger.info(f"Got {len(concept_list)} concept sectors index records.")
    return concept_list


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


def dc_member():
    #members = ths_member(ts_code=sector['sector_code'].split('.')[0])
    members = adata.stock.info.concept_constituent_ths(index_code=sector['sector_code'].split('.')[0])
    # Create an explicit copy to avoid the warning
    members = members.copy()
    members.rename(columns={'stock_code': 'ts_code', 'short_name': 'name'}, inplace=True)
    members['ts_code'] = members['ts_code'].apply(convert_akcode_to_tushare)


def dc_daily(concept_list: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    """
    use AKShare to get eastmoney concept daily data as TuShare dc_daily() function.
    # Tushare dc_daily need >6000 point
    # concept_daily = PRO.dc_daily(start_date=date, end_date=date)
    """
    all_dc_daily = pd.DataFrame()
    for concept_name in concept_list['name']:
        concept_daily = ak.stock_board_concept_hist_em(symbol=concept_name, period='daily', start_date=date, end_date=date)
        import pdb;pdb.set_trace()
        all_dc_daily = pd.concat([all_dc_daily, concept_daily], ignore_index=True)

    return all_dc_daily



# 1. 获取所有概念板块列表
board_list_df = ak.stock_board_concept_name_em()
print(f"共有 {len(board_list_df)} 个概念板块")

# 2. 计算并筛选近期强势板块
strong_sectors = []
for idx, row in board_list_df.head(10).iterrows():  # 示例：仅分析前10个板块以提高效率
    sector_name = row['板块名称']
    sector_code = row['板块代码']
    try:
        # 获取板块历史行情，此处以日线数据为例
        hist_data = ak.stock_board_concept_hist_em(symbol=sector_name, period='daily', start_date="20250101", end_date="20251030", adjust="")
        if not hist_data.empty:
            # 计算近期涨幅 (例如：最近5日)
            recent_return = (hist_data.iloc[-1]['收盘'] / hist_data.iloc[-5]['收盘'] - 1) * 100
            strong_sectors.append({'板块名称': sector_name, '板块代码': sector_code, '近期涨幅%': round(recent_return, 2)})
    except Exception as e:
        print(f"获取板块 {sector_name} 数据时出错: {e}")
        continue

## 按近期涨幅排序
strong_sectors_df = pd.DataFrame(strong_sectors).sort_values('近期涨幅%', ascending=False)
print("强势板块列表:")
print(strong_sectors_df)

## 步骤 2：获取成分股与机构评级
### 在确定强势板块后，我们可以获取其成分股，并叠加机构评级数据，以增强选股逻辑。

# 3. 获取强势板块的成分股
all_hot_stocks_from_sectors = []
for _, sector in strong_sectors_df.head(5).iterrows():  # 取涨幅前5的板块
    try:
        # 获取板块成分股
        cons_df = ak.stock_board_concept_cons_em(symbol=sector['板块代码'])
        cons_df['所属强势板块'] = sector['板块名称']  # 标记股票所属的强势板块
        all_hot_stocks_from_sectors.append(cons_df)
    except Exception as e:
        print(f"获取板块 {sector['板块名称']} 的成分股失败: {e}")
        continue

# 合并所有成分股
if all_hot_stocks_from_sectors:
    hot_stocks_df = pd.concat(all_hot_stocks_from_sectors, ignore_index=True)
    hot_stock_codes = hot_stocks_df['代码'].unique().tolist()
    print(f"\n从强势板块中获取到 {len(hot_stock_codes)} 只候选股票")
else:
    hot_stock_codes = []
    hot_stocks_df = pd.DataFrame()

# 4. 获取机构推荐池数据，作为另一个维度的补充
try:
    df_institute_recommend = ak.stock_institute_recommend(symbol="最新投资评级")
    institute_hot_codes = df_institute_recommend['股票代码'].unique().tolist()
    print(f"机构推荐池中有 {len(institute_hot_codes)} 只股票")
    # 可以在这里将机构推荐股与板块成分股合并，扩大候选池
    # all_candidate_codes = list(set(hot_stock_codes + institute_hot_codes))
except Exception as e:
    print(f"获取机构推荐数据失败: {e}")
    institute_hot_codes = []

## 步骤 3：整合实时行情与技术指标
### 现在，我们获取候选股票的实时行情，并计算量比、换手率等技术指标，进行初步筛选。

# 5. 获取全市场实时行情，并筛选出候选股
try:
    spot_df = ak.stock_zh_a_spot_em()
    # 筛选出我们关注的候选股票
    candidate_spot_data = spot_df[spot_df['代码'].isin(hot_stock_codes)].copy()

    # 进行数据清洗：确保数值型数据正确，并过滤掉停牌等无效数据
    numeric_columns = ['最新价', '涨跌幅', '成交量', '成交额', '量比', '换手率']
    for col in numeric_columns:
        candidate_spot_data[col] = pd.to_numeric(candidate_spot_data[col], errors='coerce')
    candidate_spot_data = candidate_spot_data[candidate_spot_data['最新价'] > 0]  # 过滤停牌

    # 定义筛选条件，寻找短线强势股
    # 条件示例：涨幅大于3%，量比大于1.5，换手率大于5%
    short_term_strong = candidate_spot_data[
        (candidate_spot_data['涨跌幅'] > 3) &
        (candidate_spot_data['量比'] > 1.5) &
        (candidate_spot_data['换手率'] > 5)
    ]

    print(f"\n筛选出 {len(short_term_strong)} 只短线强势股")
    if not short_term_strong.empty:
        result_df = short_term_strong[['代码', '名称', '涨跌幅', '量比', '换手率', '最新价']].sort_values('涨跌幅', ascending=False)
        print(result_df)
    else:
        print("今日未筛选出同时满足所有条件的短线强势股，可以考虑放宽条件。")

except Exception as e:
    print(f"获取或处理实时行情时出错: {e}")

## 步骤 4：融入市场热度数据
### 最后，可以引入百度热搜等市场情绪数据，作为辅助参考。

# 6. (可选) 获取百度热搜股票，观察市场情绪
try:
    df_baidu_hot = ak.stock_hot_search_baidu(symbol="A股", date="20251030")
    print("\n百度热搜股票（部分）:")
    print(df_baidu_hot[['股票名称', '涨跌幅', '所属板块名称']].head())
    # 可以尝试将热搜股票与我们的强势股列表进行匹配，观察是否有重叠
except Exception as e:
    print(f"获取百度热搜数据失败: {e}")


if __name__ == "__main__":
    sector_code = '885333.TI'
    index_code = sector_code.split('.')[0]
    df = ths_member(index_code)
    print(df)
    # akshare limited API by IP, use adata to get concept members.
    import adata
    df21 = adata.stock.info.all_concept_code_ths()
    print(df21)
    df22 = adata.stock.info.concept_constituent_ths(index_code=index_code)
    print(df22)
    import pdb;pdb.set_trace()
