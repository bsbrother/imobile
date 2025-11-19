from datetime import date, datetime
from typing import Optional, List, Literal
import re
import json # json5 saved as key:value, not "key": value
import operator
import pandas as pd
import time

import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

from tenacity import retry, stop_after_attempt, wait_random_exponential

pd.set_option('future.no_silent_downcasting', True)

@retry(stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=1, min=2, max=6))
def fetch_with_retry(self, func, **kwargs):
    return func(**kwargs)

def convert_to_datetime(date_str: str) -> Optional[datetime]:
    """
    Convert a date string to a datetime object.

    :param date_str: Date string in formats like 'YYYY-MM-DD', 'YYYY/MM/DD', or 'YYYYMMDD'.
    :return: Corresponding datetime object or None if conversion fails.
    """
    patterns = [
        r"^([0-9]{4})[-/]?([0-9]{2})[-/]?([0-9]{2})$",  # Matches 'YYYY-MM-DD', 'YYYY/MM/DD', 'YYYYMMDD'
    ]

    for pattern in patterns:
        match = re.match(pattern, date_str)
        if match:
            groups = match.groups()
            if len(groups) == 3:
                try:
                    return datetime(
                        year=int(groups[0]), month=int(groups[1]), day=int(groups[2])
                    )
                except ValueError:
                    return None
    return None

def convert_trade_date(trade_date: str | date | datetime | None = None, format: str = '%Y%m%d') -> str | None:
    """
    Transform a trade date into a string format.

    :param
    trade_date: datetime.date or datetime.datetime or string e.g. 2016-01-01, 20160101 or 2016/01/01 etc.
    format: '%Y%m%d'(default, tushare use) else is '%Y-%m-%d'(akshare use etc.

    :return: e.g. '2016-01-01' ->'20160101' or None
    """

    if isinstance(trade_date, datetime) or isinstance(trade_date, date):
        return trade_date.strftime(format)
    elif isinstance(trade_date, str):
        pattern = re.compile(r"^([0-9]{4})[-/]?([0-9]{2})[-/]?([0-9]{2})")
        match = pattern.match(trade_date)
        if match:
            groups = match.groups()
            if len(groups) == 3:
                try:
                    date_obj = date(
                        year=int(groups[0]), month=int(groups[1]), day=int(groups[2])
                    )
                    return date_obj.strftime(format)
                except ValueError:
                    return None
    return None

def is_column_index(df: pd.DataFrame, column_name: str) -> bool:
    """
    # Example usage:
    has_ts_code_as_index = is_column_index(df, 'ts_code')
    print(f"'ts_code' is index: {has_ts_code_as_index}")
    df.set_index('ts_code', inplace=True)
    """
    if isinstance(df.index, pd.MultiIndex):
        return column_name in df.index.names
    else:
        return df.index.name == column_name

def dfs_concat(dfs: List[pd.DataFrame], ignore_index: Optional[bool] = False, axis: Literal[0, 1] = 0) -> pd.DataFrame:
    """
    Concatenate a list of DataFrames along a particular axis.

    Args:
        dfs: List of DataFrames to concatenate
        ignore_index: If True, the resulting axis will be labeled 0, 1, ..., n - 1
        axis: Axis along which to concatenate (0 for rows, 1 for columns)

    Returns:
        Concatenated DataFrame
    """
    processed_frames = []
    for df in dfs:
        if df.empty:
            # Replace empty DataFrame with a new one that has no columns
            processed_frames.append(pd.DataFrame())
        else:
            # Drop columns that are entirely NA
            processed_frames.append(df.dropna(axis=1, how='all'))

    # Ensure parameters are not None
    _ignore_index = ignore_index if ignore_index is not None else False

    return pd.concat(processed_frames, ignore_index=_ignore_index, axis=axis)


# Prepare the filtering function
def create_dataframe_filter(df: Optional[pd.DataFrame]=None, conditions: Optional[dict]=None, context_vars: Optional[dict]=None) -> pd.Series:
    """
    Creates a DataFrame filter mask based on dynamic conditions

    Args:
        df: Input DataFrame
        conditions: Conditions dictionary from JSON config
        context_vars: Dictionary of available variables (min_price, max_price, etc.)

    Returns:
        Boolean mask for DataFrame filtering
    """

    # Handle None or empty inputs safely
    if df is None or conditions is None:
        return pd.Series(dtype=bool)
    if df.empty:
        return pd.Series(False, index=df.index)

    # Ensure context_vars is a dict
    context_vars = context_vars or {}

    # Initialize a mask with all True values
    mask = pd.Series(True, index=df.index)

    # Operator mapping dictionary
    op_map = {
        '>=': operator.ge,
        '<=': operator.le,
        '>': operator.gt,
        '<': operator.lt,
        '==': operator.eq,
        '!=': operator.ne
    }

    # Regex pattern to parse conditions
    pattern = r'([><=!]+)\s*([\w\.]+)'

    # Process each column's conditions
    for column, condition_str in conditions.items():
        if column not in df.columns:
            raise ValueError(f"Column '{column}' not found in DataFrame")

        # Split multiple conditions
        condition_list = [c.strip() for c in condition_str.split(',')]
        col_mask = pd.Series(True, index=df.index)

        for cond in condition_list:
            # Parse the condition
            match = re.match(pattern, cond)
            if not match:
                raise ValueError(f"Invalid condition format: '{cond}'")

            op_str, var_name = match.groups()
            operator_fn = op_map.get(op_str)

            if not operator_fn:
                raise ValueError(f"Unsupported operator: '{op_str}'")

            # Get value from context variables
            try:
                value = context_vars[var_name]
            except Exception:
                # Try to convert to float if it's a number
                try:
                    value = float(var_name)
                except Exception as e:
                    raise ValueError(f"Convert {var_name} to float error: {e}'")

            # Apply the condition
            col_mask = col_mask & operator_fn(df[column], value)

        # Combine with overall mask
        mask = mask & col_mask

    return mask


def test_socket_connection(server_info, timeout=3):
    """测试socket连接并测量响应时间"""
    ip = server_info['ip']
    port = server_info['port']

    try:
        start_time = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, port))
        sock.close()
        response_time = time.time() - start_time

        if result == 0:
            return {
                'server': server_info,
                'status': 'success',
                'message': 'Socket连接成功',
                'response_time': response_time
            }
        else:
            return {
                'server': server_info,
                'status': 'failed',
                'message': f'Socket连接失败: {result}',
                'response_time': float('inf')
            }

    except Exception as e:
        return {
            'server': server_info,
            'status': 'error',
            'message': f'连接异常: {str(e)}',
            'response_time': float('inf')
        }

def test_tdx_api_connection(server_info, timeout=5):
    """测试通达信API连接并测量响应时间"""
    try:
        from pytdx.hq import TdxHq_API

        ip = server_info['ip']
        port = server_info['port']

        api = TdxHq_API()
        start_time = time.time()

        if api.connect(ip, port):
            # 尝试获取简单数据验证连接
            try:
                quotes = api.get_security_quotes([(0, '000001')])
                response_time = time.time() - start_time
                api.disconnect()

                if quotes and len(quotes) > 0:
                    return {
                        'server': server_info,
                        'status': 'success',
                        'message': '通达信API连接成功，数据获取正常',
                        'response_time': response_time
                    }
                else:
                    return {
                        'server': server_info,
                        'status': 'partial',
                        'message': '通达信API连接成功，但数据为空',
                        'response_time': response_time
                    }
            except Exception as e:
                response_time = time.time() - start_time
                api.disconnect()
                return {
                    'server': server_info,
                    'status': 'partial',
                    'message': f'通达信API连接成功，但数据获取失败: {str(e)}',
                    'response_time': response_time
                }
        else:
            response_time = time.time() - start_time
            return {
                'server': server_info,
                'status': 'failed',
                'message': '通达信API连接失败',
                'response_time': float('inf')
            }

    except Exception as e:
        return {
            'server': server_info,
            'status': 'error',
            'message': f'通达信API测试异常: {str(e)}',
            'response_time': float('inf')
        }

def refresh_tdx_config(config_path):
    """
    快速通达信服务器测试
    使用多线程并行测试服务器连接，按速度排序并保存前10个最快的服务器
    """
    print("🚀 快速通达信服务器测试")
    print("=" * 70)

    # Read full servers: [('长城国瑞电信1', '218.85.139.19', 7709), ...]
    from pytdx.config.hosts import hq_hosts
    servers = []
    for host in hq_hosts:
        servers.append({'ip': host[1], 'port': host[2], 'name': host[0]})

    print(f"📊 开始测试 {len(servers)} 个服务器...")
    print("第一阶段: Socket连接测试 (并行)")

    socket_results = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_server = {executor.submit(test_socket_connection, server): server for server in servers}

        completed = 0
        for future in as_completed(future_to_server):
            completed += 1
            result = future.result()
            socket_results.append(result)

            if result['status'] == 'success':
                name = result['server'].get('name', f"{result['server']['ip']}:{result['server']['port']}")
                print(f"[{completed}/{len(servers)}] ✅ {name} ({result['response_time']:.3f}s)")
            else:
                name = result['server'].get('name', f"{result['server']['ip']}:{result['server']['port']}")
                print(f"[{completed}/{len(servers)}] ❌ {name}")

    # 按响应时间排序socket成功的服务器
    socket_working = [r for r in socket_results if r['status'] == 'success']
    socket_working.sort(key=lambda x: x['response_time'])

    print(f"\n📊 Socket测试结果: {len(socket_working)}/{len(servers)} 服务器可连接")

    if socket_working:
        print(f"\n第二阶段: 通达信API测试 (前{min(50, len(socket_working))}个最快的服务器)")

        api_results = []
        test_servers = socket_working[:50]  # 只测试前50个最快的

        for i, socket_result in enumerate(test_servers, 1):
            server = socket_result['server']
            name = server.get('name', f"{server['ip']}:{server['port']}")
            print(f"[{i}/{len(test_servers)}] 测试通达信API: {name}...")

            api_result = test_tdx_api_connection(server)
            # 将socket响应时间也加入到结果中
            api_result['socket_response_time'] = socket_result['response_time']
            api_results.append(api_result)

            if api_result['status'] == 'success':
                print(f"  ✅ {api_result['message']} ({api_result['response_time']:.3f}s)")
            elif api_result['status'] == 'partial':
                print(f"  ⚠️ {api_result['message']} ({api_result['response_time']:.3f}s)")
            else:
                print(f"  ❌ {api_result['message']}")

        # 按API响应时间排序可用的服务器
        api_working = [r for r in api_results if r['status'] in ['success', 'partial']]
        api_working.sort(key=lambda x: x['response_time'])

        # 只保留前10个最快的服务器
        top_10_servers = api_working[:10]

        print("\n📊 最终结果:")
        print(f"  Socket可连接: {len(socket_working)} 个")
        print(f"  通达信API可用: {len(api_working)} 个")
        print("  保存前10个最快的服务器")

        if top_10_servers:
            # 准备保存的服务器列表 (添加速度信息)
            servers_with_speed = []
            for result in top_10_servers:
                server_info = result['server'].copy()
                server_info['api_response_time'] = result['response_time']
                server_info['socket_response_time'] = result['socket_response_time']
                server_info['total_response_time'] = result['response_time'] + result['socket_response_time']
                servers_with_speed.append(server_info)

            # 保存可用服务器配置
            config_data = {
                'top_10_fastest_servers': servers_with_speed,
                'working_servers': [r['server'] for r in api_working],  # 保持向后兼容
                'socket_working_servers': [r['server'] for r in socket_working],
                'test_time': datetime.now().isoformat(),
                'total_tested': len(servers),
                'socket_working_count': len(socket_working),
                'api_working_count': len(api_working),
                'top_10_count': len(top_10_servers)
            }

            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)

            print(f"\n✅ 配置已保存到 {config_path}")

            print("\n� 前10个最快的服务器:")
            for i, server in enumerate(servers_with_speed, 1):
                name = server.get('name', f"{server['ip']}:{server['port']}")
                api_time = server['api_response_time']
                socket_time = server['socket_response_time']
                total_time = server['total_response_time']
                print(f"  {i}. {name}")
                print(f"     Socket: {socket_time:.3f}s, API: {api_time:.3f}s, 总计: {total_time:.3f}s")

            print("\n💡 使用建议:")
            print("  1. 优先使用前3个最快的服务器")
            print("  2. 如果连接失败，自动切换到下一个最快的服务器")
            print("  3. 定期重新测试服务器速度排序")

            return True
        else:
            print("\n❌ 没有找到可用的通达信API服务器")
            return False
    else:
        print("\n❌ 没有找到可连接的服务器")
        print("💡 可能的原因:")
        print("  1. 网络防火墙阻止了连接")
        print("  2. 服务器地址已过期")
        print("  3. 当前网络环境不支持")
        return False

def _safe_fillna(series: pd.Series, value):
    """
    Fill NA and explicitly infer objects to avoid FutureWarning on downcasting.

    Usage:
    merged[col] = _safe_fillna(merged[col], default_val)
    """
    result = series.fillna(value)
    if result.dtype == 'object':
        result = result.infer_objects(copy=False)
    return result


if __name__ == '__main__':
  # Cron to refresh Tdx API servers, saved in ./tdx_servers_config.json
  # refresh_tdx_config('tdx_servers_config.json')
  ymd = '20251023'
  y_m_d = convert_to_datetime(ymd)
  ymd2 = convert_trade_date(y_m_d)
  print(ymd, y_m_d, ymd2)
  exit(0)

  config = json.load('config.json')

  # Prepare your context variables
  context = {
      'min_price': 10.0,
      'max_price': 100.0,
      'min_market_cap': 1_000_000,
      'max_market_cap': 10_000_000_000,
  }

  df = pd.DataFrame({})

  # Apply the filter to your DataFrame
  filter_mask = create_dataframe_filter(
      df,
      config['remove_obvious_bad'],
      context
  )
  filtered_df = df[filter_mask]


