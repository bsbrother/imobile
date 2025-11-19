我了解您想使用 Python 3.12 和 Tushare API 来获取打板专题数据，并基于热门搜索和强势板块制定短期强势股选股策略。由于搜索结果中的打板专题数据接口链接已失效，我将主要参考其他可靠的 Tushare 接口来构建策略。

## 🎯 短期强势股选股策略

以下策略通过识别强势板块和打板相关数据来筛选短期强势股票：

```python
import tushare as ts
import pandas as pd
from datetime import datetime, timedelta
import time

# 初始化Tushare，需要您自己的token
def initialize_tushare():
    # 您需要在Tushare官网(https://tushare.pro)注册获取token
    ts.set_token('YOUR_TUSHARE_TOKEN')
    pro = ts.pro_api()
    return pro

# 获取最近交易日
def get_recent_trade_date(pro, days_back=5):
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=days_back*2)).strftime('%Y%m%d')

    cal = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
    trade_dates = cal[cal['is_open'] == 1]['cal_date'].tolist()
    return trade_dates[-1] if trade_dates else end_date

# 策略1: 基于板块动量筛选强势股
def sector_momentum_strategy(pro, trade_date):
    print("=" * 50)
    print("策略1: 板块动量选股")
    print("=" * 50)

    strong_stocks = []

    try:
        # 获取同花顺概念板块列表
        concept_list = pro.ths_index(exchange='A', type='N')
        print(f"获取到 {len(concept_list)} 个概念板块")

        # 分析板块近期表现
        sector_performance = []

        for _, concept in concept_list.iterrows():
            time.sleep(0.2)  # 限速
            try:
                # 获取板块行情数据
                concept_daily = pro.ths_daily(
                    ts_code=concept['ts_code'],
                    start_date=(datetime.strptime(trade_date, '%Y%m%d') -
                               timedelta(days=30)).strftime('%Y%m%d'),
                    end_date=trade_date
                )

                if len(concept_daily) > 5:
                    # 计算5日收益率
                    recent_5d_return = (concept_daily.iloc[0]['close'] /
                                      concept_daily.iloc[5]['close'] - 1) * 100

                    # 筛选表现好的板块
                    if recent_5d_return > 5:  # 5日内涨幅超过5%
                        sector_performance.append({
                            'sector_name': concept['name'],
                            'sector_code': concept['ts_code'],
                            '5d_return': recent_5d_return
                        })
            except Exception as e:
                continue

        # 按收益率排序
        sector_performance.sort(key=lambda x: x['5d_return'], reverse=True)

        print("\n强势板块排名:")
        for i, sector in enumerate(sector_performance[:10], 1):
            print(f"{i}. {sector['sector_name']}: {sector['5d_return']:.2f}%")

        # 获取强势板块的成分股
        for sector in sector_performance[:5]:  # 前5个强势板块
            try:
                members = pro.ths_member(ts_code=sector['sector_code'])

                for _, member in members.iterrows():
                    # 获取个股近期表现
                    stock_data = pro.daily(
                        ts_code=member['ts_code'],
                        start_date=(datetime.strptime(trade_date, '%Y%m%d') -
                                   timedelta(days=10)).strftime('%Y%m%d'),
                        end_date=trade_date
                    )

                    if len(stock_data) > 5:
                        stock_5d_return = (stock_data.iloc[0]['close'] /
                                         stock_data.iloc[5]['close'] - 1) * 100

                        if stock_5d_return > 8:  # 个股5日涨幅超过8%
                            strong_stocks.append({
                                'ts_code': member['ts_code'],
                                'name': member['name'],
                                'sector': sector['sector_name'],
                                'sector_return': sector['5d_return'],
                                'stock_5d_return': stock_5d_return,
                                'strategy': '板块动量'
                            })
            except Exception as e:
                continue

    except Exception as e:
        print(f"板块动量策略执行出错: {e}")

    return strong_stocks

# 策略2: 基于涨停板数据筛选
def limit_up_strategy(pro, trade_date):
    print("\n" + "=" * 50)
    print("策略2: 涨停板选股")
    print("=" * 50)

    strong_stocks = []

    try:
        # 获取当日涨停股票
        daily_data = pro.daily(trade_date=trade_date)
        # 获取股票基本信息
        stock_basic = pro.stock_basic(exchange='', list_status='L')

        # 筛选涨停股 (假设涨跌幅超过9.5%为涨停)
        limit_up_stocks = daily_data[daily_data['pct_chg'] > 9.5]

        print(f"发现 {len(limit_up_stocks)} 只涨停股票")

        for _, stock in limit_up_stocks.iterrows():
            basic_info = stock_basic[stock_basic['ts_code'] == stock['ts_code']]
            if not basic_info.empty:
                # 分析连续涨停情况
                hist_data = pro.daily(
                    ts_code=stock['ts_code'],
                    start_date=(datetime.strptime(trade_date, '%Y%m%d') -
                               timedelta(days=10)).strftime('%Y%m%d'),
                    end_date=trade_date
                )

                # 计算连续涨停天数
                consecutive_limit_up = 0
                for i in range(min(5, len(hist_data))):
                    if hist_data.iloc[i]['pct_chg'] > 9.5:
                        consecutive_limit_up += 1
                    else:
                        break

                # 首板或二板重点关注
                if consecutive_limit_up <= 3:
                    strong_stocks.append({
                        'ts_code': stock['ts_code'],
                        'name': basic_info.iloc[0]['name'],
                        'consecutive_days': consecutive_limit_up,
                        'pct_chg': stock['pct_chg'],
                        'amount': stock['amount'],
                        'strategy': '涨停板'
                    })

    except Exception as e:
        print(f"涨停板策略执行出错: {e}")

    return strong_stocks

# 策略3: 基于资金流向筛选
def money_flow_strategy(pro, trade_date):
    print("\n" + "=" * 50)
    print("策略3: 资金流向选股")
    print("=" * 50)

    strong_stocks = []

    try:
        # 获取资金流向数据
        money_flow = pro.moneyflow(trade_date=trade_date)

        # 筛选主力净流入大的股票
        money_flow = money_flow.sort_values('net_mf_amount', ascending=False)
        top_money_flow = money_flow.head(50)

        print(f"分析主力资金净流入前50的股票")

        for _, stock in top_money_flow.iterrows():
            # 获取股票基本信息
            basic_info = pro.stock_basic(
                ts_code=stock['ts_code'],
                fields='ts_code,name,industry'
            )

            if not basic_info.empty:
                # 结合价格走势分析
                price_data = pro.daily(
                    ts_code=stock['ts_code'],
                    start_date=(datetime.strptime(trade_date, '%Y%m%d') -
                               timedelta(days=5)).strftime('%Y%m%d'),
                    end_date=trade_date
                )

                if len(price_data) > 1:
                    price_change = (price_data.iloc[0]['close'] /
                                  price_data.iloc[1]['close'] - 1) * 100

                    # 主力大幅流入且股价上涨
                    if stock['net_mf_amount'] > 10000000 and price_change > 0:  # 净流入超过1000万
                        strong_stocks.append({
                            'ts_code': stock['ts_code'],
                            'name': basic_info.iloc[0]['name'],
                            'net_mf_amount': stock['net_mf_amount'],
                            'price_change': price_change,
                            'strategy': '资金流向'
                        })

    except Exception as e:
        print(f"资金流向策略执行出错: {e}")

    return strong_stocks

# 主执行函数
def main():
    # 初始化
    pro = initialize_tushare()
    trade_date = get_recent_trade_date(pro)

    print(f"执行日期: {trade_date}")
    print("开始执行短期强势股选股策略...")

    all_strong_stocks = []

    # 执行三个策略
    all_strong_stocks.extend(sector_momentum_strategy(pro, trade_date))
    all_strong_stocks.extend(limit_up_strategy(pro, trade_date))
    all_strong_stocks.extend(money_flow_strategy(pro, trade_date))

    # 去重并汇总结果
    unique_stocks = {}
    for stock in all_strong_stocks:
        if stock['ts_code'] not in unique_stocks:
            unique_stocks[stock['ts_code']] = stock
        else:
            # 如果同一只股票被多个策略选中，合并策略信息
            unique_stocks[stock['ts_code']]['strategy'] += f", {stock['strategy']}"

    # 输出最终结果
    print("\n" + "=" * 60)
    print("最终选股结果")
    print("=" * 60)

    if not unique_stocks:
        print("未找到符合条件的强势股票")
        return

    result_df = pd.DataFrame(list(unique_stocks.values()))

    # 按策略数量排序 (被多个策略选中的股票更可靠)
    result_df['strategy_count'] = result_df['strategy'].apply(lambda x: len(x.split(',')))
    result_df = result_df.sort_values('strategy_count', ascending=False)

    print(f"\n共筛选出 {len(result_df)} 只强势股票:")
    print("\n推荐重点关注股票:")

    for i, (_, stock) in enumerate(result_df.iterrows(), 1):
        print(f"{i}. {stock['name']}({stock['ts_code']})")
        print(f"   策略: {stock['strategy']}")

        # 根据不同策略显示不同指标
        if '板块动量' in stock['strategy']:
            print(f"   板块收益: {stock.get('sector_return', 'N/A'):.2f}%")
            print(f"   个股5日收益: {stock.get('stock_5d_return', 'N/A'):.2f}%")
        elif '涨停板' in stock['strategy']:
            print(f"   连续涨停: {stock.get('consecutive_days', 'N/A')}天")
            print(f"   涨跌幅: {stock.get('pct_chg', 'N/A'):.2f}%")
        elif '资金流向' in stock['strategy']:
            print(f"   主力净流入: {stock.get('net_mf_amount', 'N/A'):.0f}元")
            print(f"   价格变化: {stock.get('price_change', 'N/A'):.2f}%")

        print()

if __name__ == "__main__":
    main()
```

## 📊 策略说明与使用要点

### 三个核心策略：

1. **板块动量策略**：识别近期表现强势的板块，并从中选择表现更好的个股
2. **涨停板策略**：基于涨停股票数据，重点关注首板和二板股票
3. **资金流向策略**：跟踪主力资金流向，选择资金大幅流入的股票

### 使用前准备：

1. **获取Tushare Token**：
   - 访问 [Tushare官网](https://tushare.pro) 注册账号
   - 在个人中心获取API Token
   - 替换代码中的 `YOUR_TUSHARE_TOKEN`

2. **安装依赖**：
```bash
pip install tushare pandas
```

### 策略优化建议：

- **风险控制**：短期强势股波动大，建议设置止损位
- **仓位管理**：分散投资，避免过度集中
- **及时止盈**：设定明确的盈利目标并及时止盈
- **结合大盘**：在大盘向好时效果更佳

### 注意事项：

- Tushare API 有调用频率限制，代码中已加入延时
- 某些高级功能需要Tushare积分才能访问
- 实际交易前建议进行充分回测和模拟测试

这个策略组合能够有效识别短期市场热点和强势股票，但请记住任何投资策略都有风险，建议在实际使用前进行充分的测试和验证。
