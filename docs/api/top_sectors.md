# 使用Python搭配Tushare、AKShare等库获取A股热门板块信息，是进行市场分析一个非常有效的方法。不同的接口从市场强度、搜索热度等不同维度定义了“热门”，你可以根据自己的需求选择。

## Docs API
- [Tushare](https://tushare.pro/document/2)
- [AkShare](https://akshare.akfamily.xyz/introduction.html)

## Features

下面这个表格整理了常见的获取渠道和它们的特点：

| 数据平台 | 核心功能/接口 📊 | 主要特点 ✨ |
| :--- | :--- | :--- |
| **Tushare** | `limit_cpt_list` (最强板块统计)<br>`get_concept_classified` (概念分类) | `limit_cpt_list` 直接定位**当日强势板块**（按涨停股数量），适合追踪资金动向。概念分类数据提供基础的板块-股票关联信息。 |
| **AKShare** | `stock_hot_search_baidu` (百度热搜)<br>`sw_index_third_info` (行业信息) | 百度热搜接口从**网络搜索热度**反映市场关注度，是情绪面的补充。申万行业信息提供**标准化的行业分类**数据。 |
| **数立方** | `a_conseption` (A股概念板块) | 提供Wind概念板块数据，包含股票的纳入和剔除日期，数据结构清晰。 |

### 🔴 如何使用Tushare获取强势板块

Tushare的 `limit_cpt_list` 接口能直接获取每日涨停股数量最多的板块，是分析市场强势板块和资金轮动的利器。

1.  **环境准备与认证**
    首先需要安装Tushare库并获取Token。

    ```bash
    pip install tushare
    ```

    ```python
    import tushare as ts

    # 将'YOUR_TOKEN'替换为你在Tushare官网注册获取的Token
    pro = ts.pro_api('YOUR_TOKEN')
    ```

2.  **获取最强板块数据**
    使用 `limit_cpt_list` 接口，可以获取指定交易日的最强板块列表。

    ```python
    # 获取2024年11月27日的最强板块统计
    df_strong = pro.limit_cpt_list(trade_date='20241127')
    print(df_strong[['ts_code', 'name', 'trade_date', 'up_nums', 'pct_chg', 'rank']])
    ```
    执行后，你会得到一个包含板块代码、名称、涨停家数、涨跌幅和排名的DataFrame，类似下表：

    | ts_code | name | trade_date | up_nums | pct_chg | rank |
    | :--- | :--- | :--- | :--- | :--- | :--- |
    | 885728.TI | 人工智能 | 20241127 | 27 | 2.8608 | 1 |
    | 885420.TI | 电子商务 | 20241127 | 25 | 1.8973 | 2 |

3.  **获取概念板块成分股**
    如果你想进一步了解某个热门板块里具体包含哪些股票，可以使用 `get_concept_classified` 接口。

    ```python
    # 获取所有股票的概念板块分类信息
    df_concept = pro.get_concept_classified()
    # 筛选出"人工智能"概念的所有股票
    ai_stocks = df_concept[df_concept['c_name'] == '人工智能']
    print(ai_stocks[['code', 'name', 'c_name']])
    ```

### 🟠 如何使用AKShare获取关注度与行业数据

AKShare提供了另一种视角的热门板块信息，例如市场的搜索热度和标准的行业分类。

1.  **环境准备**
    首先需要安装AKShare。

    ```bash
    pip install akshare
    ```

2.  **获取百度热搜股票**
    通过百度搜索热度可以间接反映板块和股票的受关注程度，可用于市场情绪分析。

    ```python
    import akshare as ak

    # 获取当前百度热搜股票数据
    df_hot_search = ak.stock_hot_search_baidu(symbol="A股", date="20250629")
    print(df_hot_search)
    ```
    **请注意**：此接口早期版本可能存在日期参数问题，建议使用最新版AKShare。

3.  **获取申万行业分类**
    申万行业分类是国内公认的标准之一，适用于严谨的行业分析。

    ```python
    # 获取申万三级行业信息，包括成份股数量、市盈率等
    df_sw_industry = ak.sw_index_third_info()
    print(df_sw_industry.head())
    ```

### ⚠️ 重要注意事项

- **积分与权限**：部分数据接口，特别是Tushare的高级接口，需要一定的积分才能调用。请务必在[Tushare官网](https://tushare.pro/)查看具体积分要求。
- **数据延时**：多数接口的数据更新频率为**日级**。实时或分钟级的高频数据通常有更高权限或费用要求。
- **合理使用**：热搜、强势板块等数据是市场情绪的体现，往往具有**滞后性**。它们可以作为决策的重要参考，但不应作为唯一的投资依据，务必结合基本面、技术面等多维度进行综合分析。

希望这份指南能帮助你顺利获取所需的板块数据！如果你能明确更具体的目标（例如，是想做短线强势股跟踪，还是中长期的行业配置），我可以给出更有针对性的建议。


## TODO

- ths_daily API running ok, but limit_cpt_list API not return None. so how to extend ths_daily to return like limit_cpt_list ?
```python
# [Tushare API for 同花顺板块指数行情](https://tushare.pro/document/2?doc_id=260)
# 接口：ths_daily
# 描述：获取同花顺板块指数行情。注：数据版权归属同花顺，如做商业用途，请主动联系同花顺，如需帮助请联系微信：waditu_a
# 限量：单次最大3000行数据（需6000积分），可根据指数代码、日期参数循环提取。
pro = ts.pro_api()
df = pro.ths_daily(ts_code='865001.TI', start_date='20200101', end_date='20210101', fields='ts_code,trade_date,open,close,high,low,pct_change')

数据样例:
       ts_code trade_date      close       open       high        low pct_change           vol
0    865001.TI   20201231  1664.7530  1660.7060  1671.2290  1649.4200     0.5646  13224.260000
1    865001.TI   20201230  1655.4070  1644.5950  1664.2290  1638.1100     0.3073  10815.800000
...

# [Tushare API for 最强板块统计](https://tushare.pro/document/2?doc_id=357)
# 接口：limit_cpt_list
# 描述：获取每天涨停股票最多最强的概念板块，可以分析强势板块的轮动，判断资金动向
# 限量：单次最大2000行数据，可根据股票代码或者日期循环提取全部
# 积分：8000积分以上每分钟500次，每天总量不限制，具体请参阅积分获取办法

pro = ts.pro_api()
df = pro.limit_cpt_list(trade_date='20241127')

数据样例:
      ts_code    name      trade_date  days up_stat  cons_nums  up_nums pct_chg  rank
0   885728.TI    人工智能   20241127    18    9天7板         9       27  2.8608     1
1   885420.TI    电子商务   20241127     6    9天7板        11       25  1.8973     2
...
```
