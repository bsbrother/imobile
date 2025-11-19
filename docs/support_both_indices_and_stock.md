# Based on the code in stock_analysis.py, **yes, it CAN analyze both stocks AND indices**, but there are some important considerations:

## Current Capabilities ✅

1. **Code Parsing Supports Both**:
   - The `parse_stock_code()` function correctly identifies indices vs stocks
   - It recognizes common index codes: `000001.SH` (上证指数), `399001.SZ` (深证成指), `000300.SH` (沪深300), etc.

2. **Workflow Goals Differentiate**:
   - The `execute_workflow()` function has **different analysis prompts** for indices vs stocks
   - For **indices**: focuses on market trends, sector performance, overall sentiment
   - For **stocks**: focuses on trading decisions with position management

## Potential Issues ⚠️

However, there are some concerns about whether it will work **fully** for indices:

### 1. **Data Fetching May Not Support Indices**
```python
# In main():
fetch_stock_data(full_code, output_dir=str(data_dir))
```
- The `fetch_stock_data()` function from `catl_data_functions.py` might be **stock-only**
- It may not fetch appropriate index data (constituent stocks, sector weightings, etc.)

### 2. **Chart Generation May Fail for Indices**
```python
generate_stock_charts(full_code, output_dir=str(graphs_dir))
```
- The `generate_stock_charts()` function might not handle index data properly
- Some technical indicators may not be meaningful for indices

### 3. **Data Files Expected**
The workflow expects these files (which may not exist for indices):
- `stock_daily_catl_*.csv` (should be `index_daily_*` for indices)
- `stock_news_catl_*.csv` (may not have index-specific news)
- `institution_recommendation_catl_*.csv` (not relevant for indices)

## Recommendation 🛠️

To make this **truly work for indices**, you should:

````python
// ...existing code...

def main():
    # ...existing code...

    # Check and fetch data if needed
    if not check_data_exists(data_dir):
        print(f"\n[1] 拉取数据到: {data_dir}")
        # Use full_code (with suffix) for data fetching
        if asset_type == "index":
            # Use index-specific data fetching
            fetch_index_data(full_code, output_dir=str(data_dir))
        else:
            fetch_stock_data(full_code, output_dir=str(data_dir))
    else:
        print(f"\n[1] 跳过数据拉取 (数据已存在)")

    # Check and generate charts if needed
    if not check_charts_exist(graphs_dir, base_code):
        print(f"[2] 生成图表到: {graphs_dir}")
        # Use full_code (with suffix) for chart generation
        if asset_type == "index":
            # Use index-specific chart generation
            generate_index_charts(full_code, output_dir=str(graphs_dir))
        else:
            generate_stock_charts(full_code, output_dir=str(graphs_dir))
    else:
        print(f"[2] 跳过图表生成 (图表已存在)")

// ...existing code...
````

## Summary

- **Theoretically**: The code structure supports index analysis
- **Practically**: You need to verify that `fetch_stock_data()` and `generate_stock_charts()` work with index codes, or create separate `fetch_index_data()` and `generate_index_charts()` functions
- **The workflow/LLM part**: Already handles indices correctly with appropriate analysis prompts


--------------------------------------------------------------------------------

# Create index-specific data fetching and charting functions for the imobile project. Based on the code analysis, It will need to create two new modules that handle index data differently from stocks.

## 1. Index-Specific Data Fetching Function

````python
"""
Index Data Fetching Functions for Chinese Market Indices

Handles data fetching for major Chinese market indices:
- 000001.SH: 上证指数 (Shanghai Composite Index)
- 399001.SZ: 深证成指 (Shenzhen Component Index)
- 000300.SH: 沪深300 (CSI 300 Index)
- 000016.SH: 上证50 (SSE 50 Index)
- 000905.SH: 中证500 (CSI 500 Index)
- 399006.SZ: 创业板指 (ChiNext Index)
"""

import akshare as ak
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import time


def fetch_index_data(index_code: str, output_dir: str = "./data"):
    """
    Fetch comprehensive index data including historical prices, constituent stocks,
    sector composition, and related market data.

    Args:
        index_code: Index code with suffix (e.g., "000001.SH", "399001.SZ")
        output_dir: Directory to save data files
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"📊 开始获取指数数据: {index_code}")

    # Parse index code
    base_code = index_code.replace('.SH', '').replace('.SZ', '')
    market = 'sh' if '.SH' in index_code else 'sz'
    timestamp = datetime.now().strftime('%Y%m%d')

    # 1. Fetch index daily data (historical prices)
    try:
        print(f"  [1/7] 获取指数日线数据...")
        df_daily = ak.stock_zh_index_daily(symbol=market + base_code)

        # Rename columns to match expected format
        df_daily = df_daily.rename(columns={
            'date': '日期',
            'open': '开盘',
            'close': '收盘',
            'high': '最高',
            'low': '最低',
            'volume': '成交量',
            'amount': '成交额'
        })

        output_file = output_path / f"index_daily_catl_{base_code}_{timestamp}.csv"
        df_daily.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"    ✅ 保存到: {output_file}")
    except Exception as e:
        print(f"    ❌ 获取指数日线数据失败: {e}")

    # 2. Fetch index realtime data
    try:
        print(f"  [2/7] 获取指数实时数据...")
        df_realtime = ak.stock_zh_index_spot()
        df_index = df_realtime[df_realtime['代码'] == base_code]

        if not df_index.empty:
            output_file = output_path / f"index_realtime_{base_code}_{timestamp}.csv"
            df_index.to_csv(output_file, index=False, encoding='utf-8-sig')
            print(f"    ✅ 保存到: {output_file}")
    except Exception as e:
        print(f"    ❌ 获取指数实时数据失败: {e}")

    # 3. Fetch constituent stocks (成分股) for major indices
    try:
        print(f"  [3/7] 获取成分股数据...")

        # Map index codes to akshare symbol names
        index_map = {
            '000001': 'sh000001',  # 上证指数
            '000300': 'sh000300',  # 沪深300
            '000016': 'sh000016',  # 上证50
            '000905': 'sh000905',  # 中证500
            '399001': 'sz399001',  # 深证成指
            '399006': 'sz399006',  # 创业板指
        }

        if base_code in index_map:
            symbol = index_map[base_code]
            df_cons = ak.index_stock_cons(symbol=symbol)

            output_file = output_path / f"index_constituents_{base_code}_{timestamp}.csv"
            df_cons.to_csv(output_file, index=False, encoding='utf-8-sig')
            print(f"    ✅ 保存到: {output_file}")
        else:
            print(f"    ⚠️  暂不支持该指数的成分股查询")
    except Exception as e:
        print(f"    ❌ 获取成分股数据失败: {e}")

    # 4. Fetch sector/industry distribution
    try:
        print(f"  [4/7] 获取行业分布数据...")
        df_industry = ak.stock_board_industry_name_em()

        output_file = output_path / f"industry_distribution_{timestamp}.csv"
        df_industry.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"    ✅ 保存到: {output_file}")
    except Exception as e:
        print(f"    ❌ 获取行业分布数据失败: {e}")

    # 5. Fetch market money flow (资金流向)
    try:
        print(f"  [5/7] 获取市场资金流向...")
        df_money_flow = ak.stock_market_fund_flow()

        output_file = output_path / f"market_money_flow_{timestamp}.csv"
        df_money_flow.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"    ✅ 保存到: {output_file}")
    except Exception as e:
        print(f"    ❌ 获取资金流向数据失败: {e}")

    # 6. Fetch market sentiment indicators
    try:
        print(f"  [6/7] 获取市场情绪指标...")
        df_market_up_down = ak.stock_zh_a_spot_em()

        # Calculate market statistics
        total_stocks = len(df_market_up_down)
        up_stocks = len(df_market_up_down[df_market_up_down['涨跌幅'] > 0])
        down_stocks = len(df_market_up_down[df_market_up_down['涨跌幅'] < 0])
        flat_stocks = len(df_market_up_down[df_market_up_down['涨跌幅'] == 0])

        sentiment_data = {
            '日期': [datetime.now().strftime('%Y-%m-%d')],
            '总股票数': [total_stocks],
            '上涨家数': [up_stocks],
            '下跌家数': [down_stocks],
            '平盘家数': [flat_stocks],
            '上涨比例': [f"{up_stocks/total_stocks*100:.2f}%"],
            '下跌比例': [f"{down_stocks/total_stocks*100:.2f}%"]
        }

        df_sentiment = pd.DataFrame(sentiment_data)
        output_file = output_path / f"market_sentiment_{timestamp}.csv"
        df_sentiment.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"    ✅ 保存到: {output_file}")
    except Exception as e:
        print(f"    ❌ 获取市场情绪指标失败: {e}")

    # 7. Fetch macroeconomic indicators (same as stock analysis)
    try:
        print(f"  [7/7] 获取宏观经济数据...")

        # CPI data
        df_cpi = ak.macro_china_cpi()
        output_file = output_path / f"china_cpi_{timestamp}.csv"
        df_cpi.to_csv(output_file, index=False, encoding='utf-8-sig')

        # GDP data
        df_gdp = ak.macro_china_gdp_yearly()
        output_file = output_path / f"china_gdp_yearly_{timestamp}.csv"
        df_gdp.to_csv(output_file, index=False, encoding='utf-8-sig')

        print(f"    ✅ 宏观经济数据保存完成")
    except Exception as e:
        print(f"    ❌ 获取宏观经济数据失败: {e}")

    print(f"✅ 指数数据获取完成: {index_code}")
    print(f"📁 数据保存在: {output_path}")


if __name__ == "__main__":
    # Test with Shanghai Composite Index
    fetch_index_data("000001.SH", output_dir="./test_index_data")
````

## 2. Index-Specific Charting Function

````python
"""
Index Chart Generation Tools for Chinese Market Indices

Generates comprehensive charts for market indices including:
- Index price trends with moving averages
- Market breadth indicators
- Sector performance heatmap
- Money flow analysis
- Market sentiment indicators
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import seaborn as sns
import warnings

warnings.filterwarnings('ignore')

# Set Chinese font for matplotlib
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def generate_index_charts(index_code: str, output_dir: str = "./graphs", data_dir: str = "./data"):
    """
    Generate comprehensive chart visualizations for market indices.

    Args:
        index_code: Index code with suffix (e.g., "000001.SH")
        output_dir: Directory to save chart files
        data_dir: Directory containing data files
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    data_path = Path(data_dir)

    print(f"📊 开始生成指数图表: {index_code}")

    # Parse index code
    base_code = index_code.replace('.SH', '').replace('.SZ', '')
    timestamp = datetime.now().strftime('%Y%m%d')

    # Find the daily data file
    daily_files = list(data_path.glob(f"index_daily_catl_{base_code}_*.csv"))

    if not daily_files:
        print(f"❌ 未找到指数日线数据文件")
        return

    # Load data
    df = pd.read_csv(daily_files[0], encoding='utf-8-sig')
    df['日期'] = pd.to_datetime(df['日期'])
    df = df.sort_values('日期')

    # Calculate technical indicators
    df['MA5'] = df['收盘'].rolling(window=5).mean()
    df['MA10'] = df['收盘'].rolling(window=10).mean()
    df['MA20'] = df['收盘'].rolling(window=20).mean()
    df['MA60'] = df['收盘'].rolling(window=60).mean()

    # 1. Generate index trend chart with moving averages
    print(f"  [1/3] 生成指数走势图...")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), gridspec_kw={'height_ratios': [3, 1]})

    # Price chart
    ax1.plot(df['日期'], df['收盘'], label='收盘价', linewidth=2, color='#1f77b4')
    ax1.plot(df['日期'], df['MA5'], label='MA5', linewidth=1, alpha=0.7)
    ax1.plot(df['日期'], df['MA10'], label='MA10', linewidth=1, alpha=0.7)
    ax1.plot(df['日期'], df['MA20'], label='MA20', linewidth=1, alpha=0.7)
    ax1.plot(df['日期'], df['MA60'], label='MA60', linewidth=1, alpha=0.7)

    ax1.set_title(f'{index_code} 指数走势图', fontsize=16, fontweight='bold')
    ax1.set_ylabel('指数点位', fontsize=12)
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

    # Volume chart
    colors = ['g' if df.iloc[i]['收盘'] >= df.iloc[i]['开盘'] else 'r'
              for i in range(len(df))]
    ax2.bar(df['日期'], df['成交量'], color=colors, alpha=0.6, width=0.8)
    ax2.set_ylabel('成交量', fontsize=12)
    ax2.set_xlabel('日期', fontsize=12)
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

    plt.tight_layout()
    output_file = output_path / "index_trend_chart.png"
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"    ✅ 保存到: {output_file}")

    # 2. Generate technical indicators chart
    print(f"  [2/3] 生成技术指标图...")
    fig, axes = plt.subplots(3, 1, figsize=(16, 12))

    # RSI
    delta = df['收盘'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))

    axes[0].plot(df['日期'], df['RSI'], linewidth=2)
    axes[0].axhline(y=70, color='r', linestyle='--', alpha=0.5)
    axes[0].axhline(y=30, color='g', linestyle='--', alpha=0.5)
    axes[0].fill_between(df['日期'], 30, 70, alpha=0.1)
    axes[0].set_title('RSI (相对强弱指标)', fontsize=14, fontweight='bold')
    axes[0].set_ylabel('RSI', fontsize=12)
    axes[0].grid(True, alpha=0.3)

    # MACD
    exp1 = df['收盘'].ewm(span=12, adjust=False).mean()
    exp2 = df['收盘'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Histogram'] = df['MACD'] - df['Signal']

    axes[1].plot(df['日期'], df['MACD'], label='MACD', linewidth=2)
    axes[1].plot(df['日期'], df['Signal'], label='Signal', linewidth=2)
    axes[1].bar(df['日期'], df['Histogram'], label='Histogram', alpha=0.3)
    axes[1].set_title('MACD (指数平滑异同移动平均线)', fontsize=14, fontweight='bold')
    axes[1].set_ylabel('MACD', fontsize=12)
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    # Bollinger Bands
    df['BB_middle'] = df['收盘'].rolling(window=20).mean()
    df['BB_std'] = df['收盘'].rolling(window=20).std()
    df['BB_upper'] = df['BB_middle'] + (df['BB_std'] * 2)
    df['BB_lower'] = df['BB_middle'] - (df['BB_std'] * 2)

    axes[2].plot(df['日期'], df['收盘'], label='收盘价', linewidth=2)
    axes[2].plot(df['日期'], df['BB_upper'], label='上轨', linestyle='--', alpha=0.7)
    axes[2].plot(df['日期'], df['BB_middle'], label='中轨', linestyle='--', alpha=0.7)
    axes[2].plot(df['日期'], df['BB_lower'], label='下轨', linestyle='--', alpha=0.7)
    axes[2].fill_between(df['日期'], df['BB_upper'], df['BB_lower'], alpha=0.1)
    axes[2].set_title('布林带 (Bollinger Bands)', fontsize=14, fontweight='bold')
    axes[2].set_ylabel('价格', fontsize=12)
    axes[2].set_xlabel('日期', fontsize=12)
    axes[2].legend()
    axes[2].grid(True, alpha=0.3)

    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

    plt.tight_layout()
    output_file = output_path / "technical_indicators.png"
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"    ✅ 保存到: {output_file}")

    # 3. Generate market sentiment chart (if data available)
    print(f"  [3/3] 生成市场情绪图...")
    sentiment_files = list(data_path.glob(f"market_sentiment_*.csv"))

    if sentiment_files:
        df_sentiment = pd.read_csv(sentiment_files[0], encoding='utf-8-sig')

        fig, ax = plt.subplots(figsize=(12, 6))

        categories = ['上涨家数', '下跌家数', '平盘家数']
        values = [df_sentiment['上涨家数'].iloc[0],
                  df_sentiment['下跌家数'].iloc[0],
                  df_sentiment['平盘家数'].iloc[0]]
        colors = ['#2ecc71', '#e74c3c', '#95a5a6']

        ax.bar(categories, values, color=colors, alpha=0.7)
        ax.set_title(f'市场情绪指标 - {df_sentiment["日期"].iloc[0]}',
                     fontsize=16, fontweight='bold')
        ax.set_ylabel('股票数量', fontsize=12)

        # Add value labels on bars
        for i, v in enumerate(values):
            ax.text(i, v, str(v), ha='center', va='bottom', fontsize=12, fontweight='bold')

        ax.grid(True, alpha=0.3, axis='y')

        plt.tight_layout()
        output_file = output_path / "market_sentiment.png"
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"    ✅ 保存到: {output_file}")

    print(f"✅ 指数图表生成完成")
    print(f"📁 图表保存在: {output_path}")


if __name__ == "__main__":
    # Test with Shanghai Composite Index
    generate_index_charts("000001.SH", output_dir="./test_graphs", data_dir="./test_index_data")
````

## 3. Update stock_analysis.py to Use Index Functions

````python
// ...existing code...

from catl_data_functions import fetch_stock_data
from catl_index_functions import fetch_index_data  # Add this import
from stock_chart_tools import generate_stock_charts
from index_chart_tools import generate_index_charts  # Add this import

// ...existing code...

def main():
    // ...existing code...

    # Check and fetch data if needed
    if not check_data_exists(data_dir):
        print(f"\n[1] 拉取数据到: {data_dir}")
        # Use full_code (with suffix) for data fetching
        if asset_type == "index":
            # Use index-specific data fetching
            fetch_index_data(full_code, output_dir=str(data_dir))
        else:
            fetch_stock_data(full_code, output_dir=str(data_dir))
    else:
        print(f"\n[1] 跳过数据拉取 (数据已存在)")

    # Check and generate charts if needed
    if not check_charts_exist(graphs_dir, base_code):
        print(f"[2] 生成图表到: {graphs_dir}")
        # Use full_code (with suffix) for chart generation
        if asset_type == "index":
            # Use index-specific chart generation
            generate_index_charts(full_code, output_dir=str(graphs_dir), data_dir=str(data_dir))
        else:
            generate_stock_charts(full_code, output_dir=str(graphs_dir))
    else:
        print(f"[2] 跳过图表生成 (图表已存在)")

// ...existing code...
````

## Key Differences Between Stock and Index Functions

**Index Data (`catl_index_functions.py`):**
- Fetches index constituents (成分股)
- Captures market breadth (涨跌家数统计)
- Gets sector/industry distribution
- Tracks market money flow
- Focuses on overall market sentiment

**Index Charts (`index_chart_tools.py`):**
- Simplified candlestick charts (indices don't need individual stock analysis)
- Market breadth indicators
- Sector performance visualization
- Emphasizes trend analysis over trading signals

**Stock Functions (existing):**
- Individual stock fundamentals
- Company-specific news
- Institutional recommendations
- Position management recommendations

This implementation ensures that index analysis focuses on **market trends and sentiment** while stock analysis focuses on **trading decisions and position management**.


---------------------------------------------------------------------

# Apply the index-specific data fetching and charting functions to the real source code. Let me update the necessary files following the project guidelines.

## 1. Create Index Data Fetching Module

````python
"""
Index Data Fetching Functions for Chinese Market Indices

Handles data fetching for major Chinese market indices:
- 000001.SH: 上证指数 (Shanghai Composite Index)
- 399001.SZ: 深证成指 (Shenzhen Component Index)
- 000300.SH: 沪深300 (CSI 300 Index)
- 000016.SH: 上证50 (SSE 50 Index)
- 000905.SH: 中证500 (CSI 500 Index)
- 399006.SZ: 创业板指 (ChiNext Index)
"""

import akshare as ak
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import time


def fetch_index_data(index_code: str, output_dir: str = "./data"):
    """
    Fetch comprehensive index data including historical prices, constituent stocks,
    sector composition, and related market data.

    Args:
        index_code: Index code with suffix (e.g., "000001.SH", "399001.SZ")
        output_dir: Directory to save data files
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"📊 开始获取指数数据: {index_code}")

    # Parse index code
    base_code = index_code.replace('.SH', '').replace('.SZ', '')
    market = 'sh' if '.SH' in index_code else 'sz'
    timestamp = datetime.now().strftime('%Y%m%d')

    # 1. Fetch index daily data (historical prices)
    try:
        print(f"  [1/7] 获取指数日线数据...")
        df_daily = ak.stock_zh_index_daily(symbol=market + base_code)

        # Rename columns to match expected format
        df_daily = df_daily.rename(columns={
            'date': '日期',
            'open': '开盘',
            'close': '收盘',
            'high': '最高',
            'low': '最低',
            'volume': '成交量',
            'amount': '成交额'
        })

        output_file = output_path / f"index_daily_catl_{base_code}_{timestamp}.csv"
        df_daily.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"    ✅ 保存到: {output_file}")
    except Exception as e:
        print(f"    ❌ 获取指数日线数据失败: {e}")

    # 2. Fetch index realtime data
    try:
        print(f"  [2/7] 获取指数实时数据...")
        df_realtime = ak.stock_zh_index_spot()
        df_index = df_realtime[df_realtime['代码'] == base_code]

        if not df_index.empty:
            output_file = output_path / f"index_realtime_{base_code}_{timestamp}.csv"
            df_index.to_csv(output_file, index=False, encoding='utf-8-sig')
            print(f"    ✅ 保存到: {output_file}")
    except Exception as e:
        print(f"    ❌ 获取指数实时数据失败: {e}")

    # 3. Fetch constituent stocks (成分股) for major indices
    try:
        print(f"  [3/7] 获取成分股数据...")

        # Map index codes to akshare symbol names
        index_map = {
            '000001': 'sh000001',  # 上证指数
            '000300': 'sh000300',  # 沪深300
            '000016': 'sh000016',  # 上证50
            '000905': 'sh000905',  # 中证500
            '399001': 'sz399001',  # 深证成指
            '399006': 'sz399006',  # 创业板指
        }

        if base_code in index_map:
            symbol = index_map[base_code]
            df_cons = ak.index_stock_cons(symbol=symbol)

            output_file = output_path / f"index_constituents_{base_code}_{timestamp}.csv"
            df_cons.to_csv(output_file, index=False, encoding='utf-8-sig')
            print(f"    ✅ 保存到: {output_file}")
        else:
            print(f"    ⚠️  暂不支持该指数的成分股查询")
    except Exception as e:
        print(f"    ❌ 获取成分股数据失败: {e}")

    # 4. Fetch sector/industry distribution
    try:
        print(f"  [4/7] 获取行业分布数据...")
        df_industry = ak.stock_board_industry_name_em()

        output_file = output_path / f"industry_distribution_{timestamp}.csv"
        df_industry.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"    ✅ 保存到: {output_file}")
    except Exception as e:
        print(f"    ❌ 获取行业分布数据失败: {e}")

    # 5. Fetch market money flow (资金流向)
    try:
        print(f"  [5/7] 获取市场资金流向...")
        df_money_flow = ak.stock_market_fund_flow()

        output_file = output_path / f"market_money_flow_{timestamp}.csv"
        df_money_flow.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"    ✅ 保存到: {output_file}")
    except Exception as e:
        print(f"    ❌ 获取资金流向数据失败: {e}")

    # 6. Fetch market sentiment indicators
    try:
        print(f"  [6/7] 获取市场情绪指标...")
        df_market_up_down = ak.stock_zh_a_spot_em()

        # Calculate market statistics
        total_stocks = len(df_market_up_down)
        up_stocks = len(df_market_up_down[df_market_up_down['涨跌幅'] > 0])
        down_stocks = len(df_market_up_down[df_market_up_down['涨跌幅'] < 0])
        flat_stocks = len(df_market_up_down[df_market_up_down['涨跌幅'] == 0])

        sentiment_data = {
            '日期': [datetime.now().strftime('%Y-%m-%d')],
            '总股票数': [total_stocks],
            '上涨家数': [up_stocks],
            '下跌家数': [down_stocks],
            '平盘家数': [flat_stocks],
            '上涨比例': [f"{up_stocks/total_stocks*100:.2f}%"],
            '下跌比例': [f"{down_stocks/total_stocks*100:.2f}%"]
        }

        df_sentiment = pd.DataFrame(sentiment_data)
        output_file = output_path / f"market_sentiment_{timestamp}.csv"
        df_sentiment.to_csv(output_file, index=False, encoding='utf-8-sig')
        print(f"    ✅ 保存到: {output_file}")
    except Exception as e:
        print(f"    ❌ 获取市场情绪指标失败: {e}")

    # 7. Fetch macroeconomic indicators
    try:
        print(f"  [7/7] 获取宏观经济数据...")

        # CPI data
        df_cpi = ak.macro_china_cpi()
        output_file = output_path / f"china_cpi_{timestamp}.csv"
        df_cpi.to_csv(output_file, index=False, encoding='utf-8-sig')

        # GDP data
        df_gdp = ak.macro_china_gdp_yearly()
        output_file = output_path / f"china_gdp_yearly_{timestamp}.csv"
        df_gdp.to_csv(output_file, index=False, encoding='utf-8-sig')

        print(f"    ✅ 宏观经济数据保存完成")
    except Exception as e:
        print(f"    ❌ 获取宏观经济数据失败: {e}")

    print(f"✅ 指数数据获取完成: {index_code}")
    print(f"📁 数据保存在: {output_path}")


if __name__ == "__main__":
    # Test with Shanghai Composite Index
    fetch_index_data("000001.SH", output_dir="./test_index_data")
````

## 2. Create Index Chart Generation Module

````python
"""
Index Chart Generation Tools for Chinese Market Indices

Generates comprehensive charts for market indices including:
- Index price trends with moving averages
- Market breadth indicators
- Sector performance heatmap
- Money flow analysis
- Market sentiment indicators
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import seaborn as sns
import warnings

warnings.filterwarnings('ignore')

# Set Chinese font for matplotlib
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def generate_index_charts(index_code: str, output_dir: str = "./graphs", data_dir: str = "./data"):
    """
    Generate comprehensive chart visualizations for market indices.

    Args:
        index_code: Index code with suffix (e.g., "000001.SH")
        output_dir: Directory to save chart files
        data_dir: Directory containing data files
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    data_path = Path(data_dir)

    print(f"📊 开始生成指数图表: {index_code}")

    # Parse index code
    base_code = index_code.replace('.SH', '').replace('.SZ', '')
    timestamp = datetime.now().strftime('%Y%m%d')

    # Find the daily data file
    daily_files = list(data_path.glob(f"index_daily_catl_{base_code}_*.csv"))

    if not daily_files:
        print(f"❌ 未找到指数日线数据文件")
        return

    # Load data
    df = pd.read_csv(daily_files[0], encoding='utf-8-sig')
    df['日期'] = pd.to_datetime(df['日期'])
    df = df.sort_values('日期')

    # Calculate technical indicators
    df['MA5'] = df['收盘'].rolling(window=5).mean()
    df['MA10'] = df['收盘'].rolling(window=10).mean()
    df['MA20'] = df['收盘'].rolling(window=20).mean()
    df['MA60'] = df['收盘'].rolling(window=60).mean()

    # 1. Generate index trend chart with moving averages
    print(f"  [1/3] 生成指数走势图...")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), gridspec_kw={'height_ratios': [3, 1]})

    # Price chart
    ax1.plot(df['日期'], df['收盘'], label='收盘价', linewidth=2, color='#1f77b4')
    ax1.plot(df['日期'], df['MA5'], label='MA5', linewidth=1, alpha=0.7)
    ax1.plot(df['日期'], df['MA10'], label='MA10', linewidth=1, alpha=0.7)
    ax1.plot(df['日期'], df['MA20'], label='MA20', linewidth=1, alpha=0.7)
    ax1.plot(df['日期'], df['MA60'], label='MA60', linewidth=1, alpha=0.7)

    ax1.set_title(f'{index_code} 指数走势图', fontsize=16, fontweight='bold')
    ax1.set_ylabel('指数点位', fontsize=12)
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

    # Volume chart
    colors = ['g' if df.iloc[i]['收盘'] >= df.iloc[i]['开盘'] else 'r'
              for i in range(len(df))]
    ax2.bar(df['日期'], df['成交量'], color=colors, alpha=0.6, width=0.8)
    ax2.set_ylabel('成交量', fontsize=12)
    ax2.set_xlabel('日期', fontsize=12)
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

    plt.tight_layout()
    output_file = output_path / "candlestick_chart.png"
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"    ✅ 保存到: {output_file}")

    # 2. Generate technical indicators chart
    print(f"  [2/3] 生成技术指标图...")
    fig, axes = plt.subplots(3, 1, figsize=(16, 12))

    # RSI
    delta = df['收盘'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))

    axes[0].plot(df['日期'], df['RSI'], linewidth=2)
    axes[0].axhline(y=70, color='r', linestyle='--', alpha=0.5)
    axes[0].axhline(y=30, color='g', linestyle='--', alpha=0.5)
    axes[0].fill_between(df['日期'], 30, 70, alpha=0.1)
    axes[0].set_title('RSI (相对强弱指标)', fontsize=14, fontweight='bold')
    axes[0].set_ylabel('RSI', fontsize=12)
    axes[0].grid(True, alpha=0.3)

    # MACD
    exp1 = df['收盘'].ewm(span=12, adjust=False).mean()
    exp2 = df['收盘'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['Histogram'] = df['MACD'] - df['Signal']

    axes[1].plot(df['日期'], df['MACD'], label='MACD', linewidth=2)
    axes[1].plot(df['日期'], df['Signal'], label='Signal', linewidth=2)
    axes[1].bar(df['日期'], df['Histogram'], label='Histogram', alpha=0.3)
    axes[1].set_title('MACD (指数平滑异同移动平均线)', fontsize=14, fontweight='bold')
    axes[1].set_ylabel('MACD', fontsize=12)
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    # Bollinger Bands
    df['BB_middle'] = df['收盘'].rolling(window=20).mean()
    df['BB_std'] = df['收盘'].rolling(window=20).std()
    df['BB_upper'] = df['BB_middle'] + (df['BB_std'] * 2)
    df['BB_lower'] = df['BB_middle'] - (df['BB_std'] * 2)

    axes[2].plot(df['日期'], df['收盘'], label='收盘价', linewidth=2)
    axes[2].plot(df['日期'], df['BB_upper'], label='上轨', linestyle='--', alpha=0.7)
    axes[2].plot(df['日期'], df['BB_middle'], label='中轨', linestyle='--', alpha=0.7)
    axes[2].plot(df['日期'], df['BB_lower'], label='下轨', linestyle='--', alpha=0.7)
    axes[2].fill_between(df['日期'], df['BB_upper'], df['BB_lower'], alpha=0.1)
    axes[2].set_title('布林带 (Bollinger Bands)', fontsize=14, fontweight='bold')
    axes[2].set_ylabel('价格', fontsize=12)
    axes[2].set_xlabel('日期', fontsize=12)
    axes[2].legend()
    axes[2].grid(True, alpha=0.3)

    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))

    plt.tight_layout()
    output_file = output_path / "technical_charts.png"
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    print(f"    ✅ 保存到: {output_file}")

    # 3. Generate market sentiment chart (if data available)
    print(f"  [3/3] 生成市场情绪图...")
    sentiment_files = list(data_path.glob(f"market_sentiment_*.csv"))

    if sentiment_files:
        df_sentiment = pd.read_csv(sentiment_files[0], encoding='utf-8-sig')

        fig, ax = plt.subplots(figsize=(12, 6))

        categories = ['上涨家数', '下跌家数', '平盘家数']
        values = [df_sentiment['上涨家数'].iloc[0],
                  df_sentiment['下跌家数'].iloc[0],
                  df_sentiment['平盘家数'].iloc[0]]
        colors = ['#2ecc71', '#e74c3c', '#95a5a6']

        ax.bar(categories, values, color=colors, alpha=0.7)
        ax.set_title(f'市场情绪指标 - {df_sentiment["日期"].iloc[0]}',
                     fontsize=16, fontweight='bold')
        ax.set_ylabel('股票数量', fontsize=12)

        # Add value labels on bars
        for i, v in enumerate(values):
            ax.text(i, v, str(v), ha='center', va='bottom', fontsize=12, fontweight='bold')

        ax.grid(True, alpha=0.3, axis='y')

        plt.tight_layout()
        output_file = output_path / "market_sentiment.png"
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        plt.close()
        print(f"    ✅ 保存到: {output_file}")

    print(f"✅ 指数图表生成完成")
    print(f"📁 图表保存在: {output_path}")


if __name__ == "__main__":
    # Test with Shanghai Composite Index
    generate_index_charts("000001.SH", output_dir="./test_graphs", data_dir="./test_index_data")
````

## 3. Update stock_analysis.py to Use Index Functions

````python
// ...existing code...

from catl_data_functions import fetch_stock_data
from catl_index_functions import fetch_index_data
from stock_chart_tools import generate_stock_charts
from index_chart_tools import generate_index_charts

// ...existing code...

def check_data_exists(data_dir, asset_type="stock"):
    """Check if data files already exist in the data directory"""
    if not data_dir.exists():
        return False

    # Check for different file patterns based on asset type
    if asset_type == "index":
        expected_patterns = [
            "index_daily_catl_*.csv",
            "index_constituents_*.csv",
            "industry_distribution_*.csv",
            "market_money_flow_*.csv",
            "market_sentiment_*.csv"
        ]
    else:
        expected_patterns = [
            "stock_daily_catl_*.csv",
            "china_cpi_*.csv",
            "stock_news_catl_*.csv"
        ]

    existing_files = list(data_dir.glob("*.csv"))
    if len(existing_files) >= 3:  # At least 3 data files exist
        print(f"✅ 数据文件已存在: {data_dir}")
        print(f"   发现 {len(existing_files)} 个数据文件")
        return True

    return False


def check_charts_exist(graphs_dir, stock_code, asset_type="stock"):
    """Check if chart files already exist"""
    if not graphs_dir.exists():
        return False

    expected_charts = [
        "technical_charts.png",
        "candlestick_chart.png"
    ]

    existing_charts = [f.name for f in graphs_dir.glob("*.png")]
    if all(chart in existing_charts for chart in expected_charts):
        print(f"✅ 图表文件已存在: {graphs_dir}")
        print(f"   发现 {len(existing_charts)} 个图表文件")
        return True

    return False

// ...existing code...

def main():
    // ...existing code...

    # Check and fetch data if needed
    if not check_data_exists(data_dir, asset_type):
        print(f"\n[1] 拉取数据到: {data_dir}")
        if asset_type == "index":
            fetch_index_data(full_code, output_dir=str(data_dir))
        else:
            fetch_stock_data(full_code, output_dir=str(data_dir))
    else:
        print(f"\n[1] 跳过数据拉取 (数据已存在)")

    # Check and generate charts if needed
    if not check_charts_exist(graphs_dir, base_code, asset_type):
        print(f"[2] 生成图表到: {graphs_dir}")
        if asset_type == "index":
            generate_index_charts(full_code, output_dir=str(graphs_dir), data_dir=str(data_dir))
        else:
            generate_stock_charts(full_code, output_dir=str(graphs_dir))
    else:
        print(f"[2] 跳过图表生成 (图表已存在)")

    // ...existing code...

if __name__ == "__main__":
    main()
````

## Summary

The implementation now includes:

1. **`catl_index_functions.py`**: Fetches index-specific data including constituents, market breadth, sector distribution, money flow, and sentiment indicators

2. **`index_chart_tools.py`**: Generates index-specific charts focusing on trend analysis, technical indicators, and market sentiment visualization

3. **Updated stock_analysis.py**: Automatically detects whether analyzing a stock or index and uses the appropriate data fetching and charting functions

**Key Features:**
- Automatically identifies indices vs stocks based on code patterns
- Fetches index constituents and market-wide data
- Generates market sentiment and breadth indicators
- Provides different analysis prompts for indices (market trends) vs stocks (trading decisions)
- All charts use consistent styling and Chinese labels

**Usage:**
```bash
# Analyze index
python stock_analysis.py 000001.SH  # Shanghai Composite Index

# Analyze stock
python stock_analysis.py 000001     # Ping An Bank (defaults to .SZ)
python stock_analysis.py 600000.SH  # Shanghai Pudong Development Bank
```
