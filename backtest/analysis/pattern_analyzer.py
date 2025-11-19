"""
Historical pattern analysis and validation system.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta

from .pattern_detector import ChinaMarketPatternDetector
from ..core.interfaces import DataProvider


class PatternAnalyzer:
    """
    Analyzes historical market patterns and validates pattern detection accuracy.
    
    This class provides functionality to:
    - Analyze historical market patterns using index data (沪深300)
    - Detect pattern transitions and apply smoothing algorithms
    - Validate pattern detection against known historical market periods
    """
    
    def __init__(self, data_provider: DataProvider, pattern_detector: ChinaMarketPatternDetector):
        """
        Initialize the pattern analyzer.
        
        Args:
            data_provider: Data provider for fetching historical index data
            pattern_detector: Market pattern detector instance
        """
        self.data_provider = data_provider
        self.pattern_detector = pattern_detector
        
        # Known historical market periods for validation (CSI 300 index)
        self.known_periods = {
            'bull_market': [
                ('2014-07-01', '2015-06-12'),  # 2014-2015 bull market
                ('2019-01-01', '2021-02-18'),  # 2019-2021 bull run
            ],
            'bear_market': [
                ('2015-06-15', '2016-01-27'),  # 2015 crash and aftermath
                ('2018-01-29', '2019-01-03'),  # 2018 bear market
                ('2021-02-18', '2022-04-27'),  # 2021-2022 correction
            ],
            'volatile_market': [
                ('2015-06-12', '2015-08-26'),  # 2015 crash period
                ('2020-01-01', '2020-04-01'),  # COVID-19 volatility
            ]
        }
    
    def analyze_historical_patterns(self, index_code: str = '000300.SH', 
                                  start_date: str = '2014-01-01', 
                                  end_date: str = '2023-12-31') -> pd.DataFrame:
        """
        Analyze historical market patterns using index data.
        
        Args:
            index_code: Index code (default: 沪深300 - CSI 300)
            start_date: Analysis start date
            end_date: Analysis end date
            
        Returns:
            DataFrame with columns: date, pattern, confidence, raw_pattern
        """
        # Fetch historical index data
        index_data = self.data_provider.get_index_data(index_code, start_date, end_date)
        
        if index_data.empty:
            raise ValueError(f"No data available for index {index_code}")
        
        # Get trading calendar
        trading_dates = self.data_provider.get_trading_calendar(start_date, end_date)
        
        results = []
        
        for trade_date in trading_dates:
            try:
                # Detect pattern for this date
                raw_pattern = self.pattern_detector.detect_pattern(index_data, trade_date)
                confidence = self.pattern_detector.get_confidence(raw_pattern, 
                                                                index_data.loc[:trade_date])
                
                results.append({
                    'date': pd.to_datetime(trade_date),
                    'raw_pattern': raw_pattern,
                    'confidence': confidence
                })
                
            except Exception as e:
                # Skip dates with insufficient data or other issues
                continue
        
        if not results:
            raise ValueError("No valid pattern detections found")
        
        # Convert to DataFrame
        pattern_df = pd.DataFrame(results)
        pattern_df.set_index('date', inplace=True)
        
        # Apply smoothing to reduce noise
        pattern_df['pattern'] = self._apply_pattern_smoothing(pattern_df['raw_pattern'])
        
        return pattern_df
    
    def _apply_pattern_smoothing(self, raw_patterns: pd.Series, window: int = 5) -> pd.Series:
        """
        Apply smoothing algorithm to reduce pattern transition noise.
        
        Args:
            raw_patterns: Series of raw pattern detections
            window: Smoothing window size
            
        Returns:
            Smoothed pattern series
        """
        smoothed_patterns = raw_patterns.copy()
        
        # Apply majority voting within sliding window
        for i in range(len(raw_patterns)):
            start_idx = max(0, i - window // 2)
            end_idx = min(len(raw_patterns), i + window // 2 + 1)
            
            window_patterns = raw_patterns.iloc[start_idx:end_idx]
            
            # Find most common pattern in window
            pattern_counts = window_patterns.value_counts()
            if len(pattern_counts) > 0:
                most_common = pattern_counts.index[0]
                smoothed_patterns.iloc[i] = most_common
        
        return smoothed_patterns
    
    def detect_pattern_transitions(self, pattern_df: pd.DataFrame) -> pd.DataFrame:
        """
        Detect pattern transitions and their characteristics.
        
        Args:
            pattern_df: DataFrame from analyze_historical_patterns
            
        Returns:
            DataFrame with transition information
        """
        transitions = []
        
        if len(pattern_df) < 2:
            return pd.DataFrame(transitions)
        
        current_pattern = pattern_df['pattern'].iloc[0]
        pattern_start = pattern_df.index[0]
        
        for i in range(1, len(pattern_df)):
            date = pattern_df.index[i]
            pattern = pattern_df['pattern'].iloc[i]
            
            if pattern != current_pattern:
                # Pattern transition detected
                pattern_end = pattern_df.index[i-1]
                duration = (pattern_end - pattern_start).days
                
                # Calculate average confidence during the pattern period
                period_mask = (pattern_df.index >= pattern_start) & (pattern_df.index <= pattern_end)
                avg_confidence = pattern_df.loc[period_mask, 'confidence'].mean()
                
                transitions.append({
                    'start_date': pattern_start,
                    'end_date': pattern_end,
                    'pattern': current_pattern,
                    'duration_days': duration,
                    'avg_confidence': avg_confidence,
                    'next_pattern': pattern
                })
                
                current_pattern = pattern
                pattern_start = date
        
        # Add the last pattern period
        if len(pattern_df) > 0:
            pattern_end = pattern_df.index[-1]
            duration = (pattern_end - pattern_start).days
            period_mask = (pattern_df.index >= pattern_start) & (pattern_df.index <= pattern_end)
            avg_confidence = pattern_df.loc[period_mask, 'confidence'].mean()
            
            transitions.append({
                'start_date': pattern_start,
                'end_date': pattern_end,
                'pattern': current_pattern,
                'duration_days': duration,
                'avg_confidence': avg_confidence,
                'next_pattern': None
            })
        
        return pd.DataFrame(transitions)
    
    def validate_against_known_periods(self, pattern_df: pd.DataFrame) -> Dict[str, float]:
        """
        Validate pattern detection against known historical market periods.
        
        Args:
            pattern_df: DataFrame from analyze_historical_patterns
            
        Returns:
            Dictionary with validation metrics for each pattern type
        """
        validation_results = {}
        
        for pattern_type, periods in self.known_periods.items():
            correct_detections = 0
            total_days = 0
            
            for start_date, end_date in periods:
                start_dt = pd.to_datetime(start_date)
                end_dt = pd.to_datetime(end_date)
                
                # Get pattern detections for this period
                period_mask = (pattern_df.index >= start_dt) & (pattern_df.index <= end_dt)
                period_patterns = pattern_df.loc[period_mask, 'pattern']
                
                if len(period_patterns) > 0:
                    # Count correct detections
                    correct_count = (period_patterns == pattern_type).sum()
                    correct_detections += correct_count
                    total_days += len(period_patterns)
            
            # Calculate accuracy for this pattern type
            accuracy = correct_detections / total_days if total_days > 0 else 0.0
            validation_results[pattern_type] = accuracy
        
        # Calculate overall accuracy as average of individual pattern accuracies
        pattern_accuracies = [acc for pattern, acc in validation_results.items()]
        validation_results['overall'] = np.mean(pattern_accuracies) if pattern_accuracies else 0.0
        
        return validation_results
    
    def generate_pattern_statistics(self, pattern_df: pd.DataFrame) -> Dict[str, Dict]:
        """
        Generate comprehensive statistics about detected patterns.
        
        Args:
            pattern_df: DataFrame from analyze_historical_patterns
            
        Returns:
            Dictionary with pattern statistics
        """
        stats = {}
        
        # Overall pattern distribution
        pattern_counts = pattern_df['pattern'].value_counts()
        total_days = len(pattern_df)
        
        for pattern in ['normal_market', 'bull_market', 'bear_market', 'volatile_market']:
            count = pattern_counts.get(pattern, 0)
            percentage = (count / total_days) * 100 if total_days > 0 else 0
            
            # Calculate average confidence for this pattern
            pattern_mask = pattern_df['pattern'] == pattern
            avg_confidence = pattern_df.loc[pattern_mask, 'confidence'].mean() if pattern_mask.any() else 0
            
            stats[pattern] = {
                'days': count,
                'percentage': percentage,
                'avg_confidence': avg_confidence
            }
        
        # Pattern transition statistics
        transitions_df = self.detect_pattern_transitions(pattern_df)
        
        if not transitions_df.empty:
            # Average duration for each pattern
            for pattern in stats.keys():
                pattern_transitions = transitions_df[transitions_df['pattern'] == pattern]
                if not pattern_transitions.empty:
                    avg_duration = pattern_transitions['duration_days'].mean()
                    stats[pattern]['avg_duration_days'] = avg_duration
                else:
                    stats[pattern]['avg_duration_days'] = 0
            
            # Most common transitions
            transition_pairs = []
            for _, row in transitions_df.iterrows():
                if row['next_pattern'] is not None:
                    transition_pairs.append(f"{row['pattern']} -> {row['next_pattern']}")
            
            if transition_pairs:
                transition_counts = pd.Series(transition_pairs).value_counts()
                stats['common_transitions'] = transition_counts.head(5).to_dict()
            else:
                stats['common_transitions'] = {}
        
        return stats
    
    def create_pattern_report(self, index_code: str = '000300.SH',
                            start_date: str = '2014-01-01',
                            end_date: str = '2023-12-31') -> Dict:
        """
        Create a comprehensive pattern analysis report.
        
        Args:
            index_code: Index code for analysis
            start_date: Analysis start date
            end_date: Analysis end date
            
        Returns:
            Dictionary containing complete analysis report
        """
        # Perform historical analysis
        pattern_df = self.analyze_historical_patterns(index_code, start_date, end_date)
        
        # Generate all analysis components
        transitions_df = self.detect_pattern_transitions(pattern_df)
        validation_results = self.validate_against_known_periods(pattern_df)
        statistics = self.generate_pattern_statistics(pattern_df)
        
        # Create comprehensive report
        report = {
            'analysis_period': {
                'start_date': start_date,
                'end_date': end_date,
                'index_code': index_code,
                'total_days': len(pattern_df)
            },
            'pattern_data': pattern_df,
            'transitions': transitions_df,
            'validation_results': validation_results,
            'statistics': statistics,
            'summary': {
                'most_common_pattern': pattern_df['pattern'].mode().iloc[0] if not pattern_df.empty else 'normal_market',
                'avg_confidence': pattern_df['confidence'].mean(),
                'total_transitions': len(transitions_df) - 1 if len(transitions_df) > 0 else 0,
                'validation_accuracy': validation_results.get('overall', 0.0)
            }
        }
        
        return report