"""
pip install -U pywencai
param = "{date}涨停，非涉嫌信息披露违规且非立案调查且非ST，非科创板，非北交所"
df = pywencai.get(query= param ,sort_key='成交金额', sort_order='desc')
print(df)
df.to_excel(spath, engine='xlsxwriter')
selected_columns = ['股票代码', '股票简称', '最新价','最新涨跌幅', '首次涨停时间['+date + ']', '连续涨停天数['+date + ']','涨停原因类别['+date + ']','a股市值(不含限售股)['+date + ']','涨停类型['+date + ']']
"""
from loguru import logger
import json
import requests
import pandas as pd


def show_stock_list(code):
    """显示成分股的独立组件"""
    # 构造请求URL
    url = f"https://d.10jqka.com.cn/v2/blockrank/{code}/199112/d1000.js"
    headers = {
        'Referer': 'http://q.10jqka.com.cn/',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
    }

    stocks_df = pd.DataFrame()
    response = requests.get(url, headers=headers, timeout=10)
    if response.status_code == 200:
        # 处理JSONP数据
        json_str = response.text.split('(', 1)[1].rsplit(')', 1)[0]
        data = json.loads(json_str)

        # 提取并展示数据
        stock_list = data.get('items', [])
        if stock_list:
            stocks_df = pd.DataFrame(
                [(s.get('5', '').zfill(6),
                  s.get('55', ''),
                  f"{float(s.get('8', 0)):.2f}",
                  f"{float(s.get('199112', 0)):.2f}%")
                 for s in stock_list],
                #columns=['股票代码', '股票名称', '最新价', '涨跌幅']
                columns=['con_code', 'con_name', '最新价', '涨跌幅']
            )
        else:
            logger.warning("未找到相关个股数据")
    else:
        logger.error(f"请求失败，状态码：{response.status_code}")
    return stocks_df


sector_code = '885333.TI'
df = show_stock_list(sector_code.split('.')[0])
print(df)
