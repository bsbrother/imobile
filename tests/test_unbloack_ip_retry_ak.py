"""
### 3. 配置建议

# 针对不同场景的配置
class AkshareConfig:
    # 高频数据获取配置（较短的延迟）
    HIGH_FREQUENCY = AkshareAPIWrapper(rate_limit_delay=0.2)

    # 批量数据获取配置（较长的延迟，避免被封）
    BATCH_PROCESSING = AkshareAPIWrapper(rate_limit_delay=1.0)

    # 实时监控配置（中等延迟）
    REAL_TIME_MONITOR = AkshareAPIWrapper(rate_limit_delay=0.5)

# 使用不同的配置
hot_boards = AkshareConfig.BATCH_PROCESSING.get_hot_boards()
real_time_data = AkshareConfig.REAL_TIME_MONITOR.get_stock_spot()

## 主要改进点

1. **更精确的异常处理**：只对特定类型的异常进行重试
2. **空数据重试逻辑**：使用 `retry_if_result` 对空结果也进行重试
3. **更好的日志记录**：记录具体的函数名和参数
4. **配置灵活性**：支持不同的使用场景配置
5. **类型提示**：添加了类型注解，提高代码可读性

这样的实现能够更好地处理 AKShare API 的各种异常情况，同时保持良好的性能和可维护性。
"""

import logging
import time
import adata
import pandas as pd
from tenacity import (
    retry, stop_after_attempt, wait_random_exponential,
    retry_if_exception_type, before_sleep_log, retry_if_result
)
from typing import Optional, Callable

import akshare as ak

tenacity_logger = logging.getLogger("tenacity")
tenacity_logger.setLevel(logging.INFO)

# 模块级函数供retry使用
def is_empty_dataframe(df: Optional[pd.DataFrame]) -> bool:
    """检查是否为空的DataFrame"""
    return df is None or not isinstance(df, pd.DataFrame) or df.empty

class AkshareAPIWrapper:
    def __init__(self, rate_limit_delay: float = 0.5):
        self.rate_limit_delay = rate_limit_delay

    def _is_empty_dataframe(self, df: Optional[pd.DataFrame]) -> bool:
        """实例方法版本的检查"""
        return is_empty_dataframe(df)

    @retry(
        stop=stop_after_attempt(3),  # 增加到3次尝试
        wait=wait_random_exponential(multiplier=0.5, min=1, max=5),  # 调整等待参数
        retry=(
            retry_if_exception_type((ConnectionError, TimeoutError, ValueError)) |
            retry_if_result(is_empty_dataframe)  # 对空结果也重试
        ),
        before_sleep=before_sleep_log(tenacity_logger, logging.WARNING)
    )
    def _ak_call(self, func: Callable, **kwargs) -> pd.DataFrame:
        """
        调用AKShare API的重试包装器

        Args:
            func: AKShare函数
            **kwargs: 函数参数

        Returns:
            pd.DataFrame: API返回的数据

        Raises:
            ValueError: 当API返回无效响应时
            ConnectionError: 网络连接问题
        """
        # 日期参数转换
        for date_param in ['start_date', 'end_date', 'trade_date', 'date']:
            if date_param in kwargs and kwargs[date_param]:
                kwargs[date_param] = self._convert_trade_date(kwargs[date_param])

        # 速率限制
        time.sleep(self.rate_limit_delay)

        try:
            df = func(**kwargs)

            # 检查响应有效性
            if self._is_empty_dataframe(df):
                tenacity_logger.warning(f"AKShare API返回空数据，函数: {func.__name__}, 参数: {kwargs}")
                # 这里会触发重试，因为retry_if_result条件满足
                return pd.DataFrame()  # 返回空DataFrame触发重试

            return df

        except Exception as e:
            tenacity_logger.error(f"AKShare API调用异常: {e}, 函数: {func.__name__}")
            raise

    def _convert_trade_date(self, date_str: str) -> str:
        """转换交易日期格式"""
        # 这里实现您的日期转换逻辑
        # 例如: "2024-01-01" -> "20240101"
        return date_str.replace("-", "")

    def get_hot_boards(self, date: str = None) -> pd.DataFrame:
        """获取热门板块示例"""
        if date is None:
            date = time.strftime("%Y%m%d")
        #return self._ak_call(ak.stock_board_concept_name_em)
        return self._ak_call(ak.stock_board_concept_name_ths)

    def get_board_cons(self, symbol: str) -> pd.DataFrame:
        """获取板块成分股"""
        return self._ak_call(ak.stock_board_concept_cons_em, symbol=symbol)

    def get_stock_spot(self) -> pd.DataFrame:
        """获取实时行情"""
        return self._ak_call(ak.stock_zh_a_spot_em)


class RobustAkshareAPI:
    def __init__(self, rate_limit_delay: float = 1.0):
        self.rate_limit_delay = rate_limit_delay
        self.tenacity_logger = logging.getLogger("tenacity.akshare")

    def _is_empty_dataframe(self, df: Optional[pd.DataFrame]) -> bool:
        """实例方法版本的检查"""
        return is_empty_dataframe(df)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=1, min=2, max=10),
        retry=(
            retry_if_exception_type((ConnectionError, TimeoutError, ValueError)) |
            retry_if_result(is_empty_dataframe)  # 使用模块级函数
        ),
        before_sleep=before_sleep_log(tenacity_logger, logging.WARNING)
    )
    def safe_ak_call(self, func: Callable, **kwargs) -> pd.DataFrame:
        """
        安全的AKShare API调用
        """
        # 速率限制
        time.sleep(self.rate_limit_delay)

        try:
            self.tenacity_logger.info(f"调用AKShare函数: {func.__name__}")
            df = func(**kwargs)

            if self._is_empty_dataframe(df):
                self.tenacity_logger.warning(f"返回空数据，参数: {kwargs}")
                return pd.DataFrame()  # 触发重试

            self.tenacity_logger.info(f"成功获取 {len(df)} 行数据")
            return df

        except Exception as e:
            self.tenacity_logger.error(f"API调用异常: {e}")
            raise

    def get_alternative_concept_data(self):
        """获取概念板块数据的替代方案"""
        alternatives = [
            ak.stock_board_industry_name_em,
            ak.stock_board_concept_name_ths,
            ak.stock_board_concept_summary_ths,
        ]

        for func in alternatives:
            try:
                result = self.safe_ak_call(func)
                if not self._is_empty_dataframe(result):
                    return result
            except Exception as e:
                self.tenacity_logger.warning(f"备用接口 {func.__name__} 失败: {e}")
                continue

        raise ValueError("所有备用接口都失败了")

# 使用示例
if __name__ == "__main__":
    api = RobustAkshareAPI(rate_limit_delay=1.5)
    try:
        # 尝试获取概念板块
        concept_data = api.get_alternative_concept_data()
        print(f"成功获取概念板块数据: {len(concept_data)} 行")
        print(concept_data.head())
    except Exception as e:
        print(f"最终失败: {e}")

    ak_wrapper = AkshareAPIWrapper(rate_limit_delay=0.3)
    try:
        # 获取热门板块
        hot_boards = ak_wrapper.get_hot_boards()
        print(f"获取到 {len(hot_boards)} 个板块")

        # 获取特定板块成分股
        if not hot_boards.empty:
            import pdb;pdb.set_trace()
            if 'code' in hot_boards.columns:
                sample_board = hot_boards.iloc[0]['code']
            else:
                sample_board = hot_boards.iloc[0]['板块代码']
            #board_cons = ak_wrapper.get_board_cons(sample_board)
            board_cons = adata.stock.info.concept_constituent_ths(concept_code=sample_board)
            print(f"板块 {sample_board} 有 {len(board_cons)} 只成分股") # pyright: ignore

        # 获取实时行情
        spot_data = ak_wrapper.get_stock_spot()
        print(f"获取到 {len(spot_data)} 只股票实时数据")
    except Exception as e:
        print(f"API调用最终失败: {e}")

