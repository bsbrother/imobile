from loguru import logger
from typing import List, Dict, Any, Optional
import pandas as pd
from datetime import datetime

from ..utils.exceptions import DataProviderError

class DataValidator:
    """Validates data integrity and completeness."""

    def __init__(self):
        self.logger = logger

    @staticmethod
    def validate_ohlcv_data(data: pd.DataFrame) -> bool:
        """
        Validate OHLCV data structure and completeness.
        Tushare daily data(open, high, low, close, vol) already validated.

        Args:
            data: DataFrame containing OHLCV data

        Returns:
            True if data is valid

        Raises:
            DataProviderError: If data is invalid
        """
        if not data or data.empty:
            raise DataProviderError("Data is empty")

        # Check required columns for stock data
        required_stock_columns = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol']
        required_index_columns = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close']

        # Check if it's stock data or index data
        has_vol = 'vol' in data.columns
        required_columns = required_stock_columns if has_vol else required_index_columns

        missing_columns = [col for col in required_columns if col not in data.columns]
        if missing_columns:
            raise DataProviderError(f"Missing required columns: {missing_columns}")

        # Validate data types
        numeric_columns = ['open', 'high', 'low', 'close']
        if has_vol:
            numeric_columns.extend(['vol'])

        for col in numeric_columns:
            if not pd.api.types.is_numeric_dtype(data[col]):
                raise DataProviderError(f"Column {col} must be numeric")

        # Validate OHLC relationships
        invalid_ohlc = data[
            (data['high'] < data['low']) |
            (data['high'] < data['open']) |
            (data['high'] < data['close']) |
            (data['low'] > data['open']) |
            (data['low'] > data['close'])
        ]

        if not invalid_ohlc.empty:
            raise DataProviderError(f"Invalid OHLC relationships found in {len(invalid_ohlc)} records")

        # Validate positive values
        negative_values = data[
            (data['open'] <= 0) |
            (data['high'] <= 0) |
            (data['low'] <= 0) |
            (data['close'] <= 0)
        ]

        if not negative_values.empty:
            raise DataProviderError(f"Non-positive price values found in {len(negative_values)} records")

        # Validate volume if present
        if has_vol:
            negative_volume = data[data['vol'] < 0]
            if not negative_volume.empty:
                raise DataProviderError(f"Negative volume values found in {len(negative_volume)} records")

        return True

    @staticmethod
    def check_missing_data(data: pd.DataFrame) -> pd.DataFrame:
        """
        Check for missing data points and return summary.

        Args:
            data: DataFrame to check for missing data

        Returns:
            DataFrame with missing data summary
        """
        if data is None or data.empty:
            return pd.DataFrame({'column': [], 'missing_count': [], 'missing_percentage': []})

        missing_summary = []

        for column in data.columns:
            missing_count = data[column].isnull().sum()
            missing_percentage = (missing_count / len(data)) * 100

            if missing_count > 0:
                missing_summary.append({
                    'column': column,
                    'missing_count': missing_count,
                    'missing_percentage': round(missing_percentage, 2)
                })

        return pd.DataFrame(missing_summary)

    @staticmethod
    def validate_date_range(data: pd.DataFrame, start_date: str, end_date: str) -> bool:
        """
        Validate that data covers the requested date range.

        Args:
            data: DataFrame with trade_date column
            start_date: Expected start date in YYYYMMDD format
            end_date: Expected end date in YYYYMMDD format

        Returns:
            True if date range is adequately covered

        Raises:
            DataProviderError: If date range validation fails
        """
        if data is None or data.empty:
            raise DataProviderError("Cannot validate date range on empty data")

        if 'trade_date' not in data.columns:
            raise DataProviderError("Data must contain 'trade_date' column for date range validation")

        # Convert dates to datetime for comparison
        try:
            format = '%Y-%m-%d' if len(start_date) == 10 else '%Y%m%d'
            start_dt = datetime.strptime(start_date, format)
            end_dt = datetime.strptime(end_date, format)
        except ValueError as e:
            raise DataProviderError(f"Invalid date format. Expected YYYY-MM-DD or YYYYMMDD: {str(e)}")

        # Get actual date range in data
        data_dates = pd.to_datetime(data['trade_date'], format='%Y%m%d')
        actual_start = data_dates.min()
        actual_end = data_dates.max()

        # Check if data covers the requested range (allowing for weekends/holidays)
        if actual_start > start_dt:
            # TODO: 002124.SZ, 20250506 > start_end(20250430)
            #raise DataProviderError(f"Data starts later than requested. Requested: {start_date}, Actual: {actual_start.strftime('%Y%m%d')}")
            pass

        if actual_end < end_dt:
            raise DataProviderError(f"Data ends earlier than requested. Requested: {end_date}, Actual: {actual_end.strftime('%Y%m%d')}")

        return True

    @staticmethod
    def normalize_ohlcv_data(data: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize and preprocess OHLCV data.

        Args:
            data: Raw OHLCV data

        Returns:
            Normalized DataFrame
        """
        if data is None or data.empty:
            return data

        # Create a copy to avoid modifying original data
        normalized_data = data.copy()

        # Ensure trade_date is in correct format
        if 'trade_date' in normalized_data.columns:
            # Convert to datetime and back to ensure consistent format
            normalized_data['trade_date'] = pd.to_datetime(
                normalized_data['trade_date'], format='%Y%m%d'
            ).dt.strftime('%Y%m%d')

        # Round numeric columns to appropriate precision
        numeric_columns = ['open', 'high', 'low', 'close']
        for col in numeric_columns:
            if col in normalized_data.columns:
                normalized_data[col] = normalized_data[col].round(2)

        # Round volume to integer if present
        if 'vol' in normalized_data.columns:
            normalized_data['vol'] = normalized_data['vol'].round(0).astype('int64')

        # Sort by symbol and date
        if 'ts_code' in normalized_data.columns and 'trade_date' in normalized_data.columns:
            normalized_data = normalized_data.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
        elif 'trade_date' in normalized_data.columns:
            normalized_data = normalized_data.sort_values('trade_date').reset_index(drop=True)

        return normalized_data

    @staticmethod
    def detect_data_anomalies(data: pd.DataFrame, symbol: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        """
        Detect potential data anomalies in OHLCV data.

        Args:
            data: OHLCV data to analyze
            symbol: Optional symbol for filtering (if data contains multiple symbols)

        Returns:
            Dictionary with anomaly types and their details
        """
        if data is None or data.empty:
            return {}

        # Filter by symbol if provided
        if symbol and 'ts_code' in data.columns:
            data = data[data['ts_code'] == symbol].copy()

        anomalies = {
            'price_gaps': [],
            'volume_spikes': [],
            'zero_volume': [],
            'price_limits': []
        }

        if len(data) < 2:
            return anomalies

        # Sort by date to ensure proper sequence
        if 'trade_date' in data.columns:
            data = data.sort_values('trade_date').reset_index(drop=True)

        # Detect price gaps (>10% change)
        data['prev_close'] = data['close'].shift(1)
        price_changes = ((data['open'] - data['prev_close']) / data['prev_close']).abs()
        large_gaps = data[price_changes > 0.1]

        for idx, row in large_gaps.iterrows():
            if pd.notna(row['prev_close']):
                anomalies['price_gaps'].append({
                    'date': row['trade_date'],
                    'symbol': row.get('ts_code', 'N/A'),
                    'gap_percentage': round(((row['open'] - row['prev_close']) / row['prev_close']) * 100, 2),
                    'prev_close': row['prev_close'],
                    'open': row['open']
                })

        # Detect volume spikes (>5x average volume)
        if 'vol' in data.columns:
            data['vol_ma'] = data['vol'].rolling(window=20, min_periods=1).mean()
            volume_spikes = data[data['vol'] > (data['vol_ma'] * 5)]

            for idx, row in volume_spikes.iterrows():
                if pd.notna(row['vol_ma']) and row['vol_ma'] > 0:
                    anomalies['volume_spikes'].append({
                        'date': row['trade_date'],
                        'symbol': row.get('ts_code', 'N/A'),
                        'volume': row['vol'],
                        'average_volume': round(row['vol_ma'], 0),
                        'spike_ratio': round(row['vol'] / row['vol_ma'], 2)
                    })

            # Detect zero volume days
            zero_volume = data[data['vol'] == 0]
            for idx, row in zero_volume.iterrows():
                anomalies['zero_volume'].append({
                    'date': row['trade_date'],
                    'symbol': row.get('ts_code', 'N/A')
                })

        # Detect potential price limit hits (10% change in China A-shares)
        daily_returns = ((data['close'] - data['prev_close']) / data['prev_close']).abs()
        limit_hits = data[daily_returns >= 0.095]  # Close to 10% limit

        for idx, row in limit_hits.iterrows():
            if pd.notna(row['prev_close']):
                anomalies['price_limits'].append({
                    'date': row['trade_date'],
                    'symbol': row.get('ts_code', 'N/A'),
                    'return_percentage': round(((row['close'] - row['prev_close']) / row['prev_close']) * 100, 2),
                    'prev_close': row['prev_close'],
                    'close': row['close']
                })

        return anomalies

    @staticmethod
    def validate_symbol_format(symbol: str) -> bool:
        """
        Validate Chinese stock symbol format.

        Args:
            symbol: Stock symbol to validate

        Returns:
            True if symbol format is valid
        """
        if not symbol or not isinstance(symbol, str):
            return False

        # Chinese stock symbol format: 6 digits + .SH or .SZ
        import re
        pattern = r'^\d{6}\.(SH|SZ)$'
        return bool(re.match(pattern, symbol))

    @staticmethod
    def get_data_quality_score(data: pd.DataFrame) -> Dict[str, Any]:
        """
        Calculate overall data quality score.

        Args:
            data: DataFrame to evaluate

        Returns:
            Dictionary with quality metrics and overall score
        """
        if data is None or data.empty:
            return {'overall_score': 0, 'metrics': {}}

        metrics = {}

        # Completeness score (percentage of non-null values)
        total_cells = data.size
        non_null_cells = data.count().sum()
        completeness = (non_null_cells / total_cells) * 100 if total_cells > 0 else 0
        metrics['completeness'] = round(completeness, 2)

        # Consistency score (valid OHLC relationships)
        try:
            DataValidator.validate_ohlcv_data(data)
            consistency = 100.0
        except DataProviderError:
            # Calculate percentage of valid OHLC relationships
            valid_ohlc = data[
                (data['high'] >= data['low']) &
                (data['high'] >= data['open']) &
                (data['high'] >= data['close']) &
                (data['low'] <= data['open']) &
                (data['low'] <= data['close']) &
                (data['open'] > 0) &
                (data['high'] > 0) &
                (data['low'] > 0) &
                (data['close'] > 0)
            ]
            consistency = (len(valid_ohlc) / len(data)) * 100 if len(data) > 0 else 0

        metrics['consistency'] = round(consistency, 2)

        # Anomaly score (lower is better)
        anomalies = DataValidator.detect_data_anomalies(data)
        total_anomalies = sum(len(anomaly_list) for anomaly_list in anomalies.values())
        anomaly_rate = (total_anomalies / len(data)) * 100 if len(data) > 0 else 0
        anomaly_score = max(0, 100 - anomaly_rate)
        metrics['anomaly_score'] = round(anomaly_score, 2)

        # Overall score (weighted average)
        overall_score = (
            completeness * 0.4 +
            consistency * 0.4 +
            anomaly_score * 0.2
        )

        return {
            'overall_score': round(overall_score, 2),
            'metrics': metrics,
            'total_records': len(data),
            'total_anomalies': total_anomalies
        }
