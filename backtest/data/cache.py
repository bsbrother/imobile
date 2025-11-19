"""
Data caching system for improved performance.
"""

from loguru import logger
import os
import time
import pickle
import glob
from typing import Optional, Dict, Any
from ..utils.exceptions import DataProviderError


class DataCache:
    """Local caching mechanism for market data."""

    def __init__(self, cache_dir: str = "data_cache", default_ttl: int = 3600):
        """
        Initialize data cache.

        Args:
            cache_dir: Directory to store cache files
            default_ttl: Default time-to-live in seconds
        """
        self.cache_dir = cache_dir
        self.default_ttl = default_ttl

        # Create cache directory if it doesn't exist
        try:
            os.makedirs(cache_dir, exist_ok=True)
            logger.debug(f"Cache directory initialized: {cache_dir}")
        except Exception as e:
            raise DataProviderError(f"Failed to create cache directory {cache_dir}: {str(e)}")

    def _generate_cache_key(self, key: str) -> str:
        """
        Generate a filesystem-safe cache key.

        Args:
            key: Original cache key

        Returns:
            Hashed cache key safe for filesystem
        """
        # Create hash of the key to ensure filesystem safety
        #key_hash = hashlib.md5(key.encode()).hexdigest()
        key_hash = key
        return f"cache_{key_hash}"

    def _get_cache_path(self, key: str) -> str:
        """Get full path for cache file."""
        cache_key = self._generate_cache_key(key)
        return os.path.join(self.cache_dir, f"{cache_key}.pkl")

    def _get_metadata_path(self, key: str) -> str:
        """Get full path for cache metadata file."""
        cache_key = self._generate_cache_key(key)
        return os.path.join(self.cache_dir, f"{cache_key}_meta.pkl")

    def get(self, key: str) -> Optional[Any]:
        """
        Get cached data by key.

        Args:
            key: Cache key

        Returns:
            Cached data or None if not found/expired
        """
        try:
            cache_path = self._get_cache_path(key)
            metadata_path = self._get_metadata_path(key)

            # Check if cache files exist
            if not os.path.exists(cache_path) or not os.path.exists(metadata_path):
                logger.debug(f"Cache miss: {key}")
                return None

            # Load metadata
            with open(metadata_path, 'rb') as f:
                metadata = pickle.load(f)

            # Check if cache has expired
            current_time = time.time()
            if current_time > metadata['expires_at']:
                logger.debug(f"Cache expired: {key}")
                self._remove_cache_files(key)
                return None

            # Load cached data
            with open(cache_path, 'rb') as f:
                data = pickle.load(f)

            logger.debug(f"Cache hit: {key}")
            return data

        except Exception as e:
            logger.warning(f"Failed to retrieve cache for key {key}: {str(e)}")
            return None

    def set(self, key: str, data: Any, ttl: Optional[int] = None) -> bool:
        """
        Set cached data with TTL.

        Args:
            key: Cache key
            data: Data to cache (DataFrame or any serializable object)
            ttl: Time-to-live in seconds (uses default if None)

        Returns:
            True if successfully cached
        """
        try:
            if data is None:
                logger.debug(f"Attempted to cache None data for key: {key}")
                return False
                
            # Check if data is DataFrame and empty
            if hasattr(data, 'empty') and data.empty:
                logger.debug(f"Attempted to cache empty DataFrame for key: {key}")
                return False

            cache_path = self._get_cache_path(key)
            metadata_path = self._get_metadata_path(key)

            # Use default TTL if not specified
            if ttl is None:
                ttl = self.default_ttl

            # Create metadata
            current_time = time.time()
            metadata = {
                'key': key,
                'created_at': current_time,
                'expires_at': current_time + ttl,
                'ttl': ttl,
                'data_type': type(data).__name__
            }
            
            # Add DataFrame-specific metadata if applicable
            if hasattr(data, 'shape') and hasattr(data, 'columns'):
                metadata.update({
                    'data_shape': data.shape,
                    'data_columns': list(data.columns)
                })

            # Save data and metadata
            with open(cache_path, 'wb') as f:
                pickle.dump(data, f)

            with open(metadata_path, 'wb') as f:
                pickle.dump(metadata, f)

            logger.debug(f"Cached data for key: {key} (TTL: {ttl}s, Type: {type(data).__name__})")
            return True

        except Exception as e:
            logger.error(f"Failed to cache data for key {key}: {str(e)}")
            return False

    def invalidate(self, pattern: str) -> int:
        """
        Invalidate cached data matching pattern.

        Args:
            pattern: Pattern to match cache keys (supports wildcards)

        Returns:
            Number of cache entries invalidated
        """
        try:
            invalidated_count = 0

            # Get all metadata files
            metadata_files = glob.glob(os.path.join(self.cache_dir, "*_meta.pkl"))

            for metadata_path in metadata_files:
                try:
                    # Load metadata to get original key
                    with open(metadata_path, 'rb') as f:
                        metadata = pickle.load(f)

                    original_key = metadata.get('key', '')

                    # Check if key matches pattern (simple wildcard matching)
                    if self._matches_pattern(original_key, pattern):
                        # Extract cache key from metadata filename
                        cache_key = os.path.basename(metadata_path).replace('_meta.pkl', '')
                        self._remove_cache_files_by_cache_key(cache_key)
                        invalidated_count += 1
                        logger.debug(f"Invalidated cache: {original_key}")

                except Exception as e:
                    logger.warning(f"Failed to process metadata file {metadata_path}: {str(e)}")

            logger.info(f"Invalidated {invalidated_count} cache entries matching pattern: {pattern}")
            return invalidated_count

        except Exception as e:
            logger.error(f"Failed to invalidate cache with pattern {pattern}: {str(e)}")
            return 0

    def _matches_pattern(self, text: str, pattern: str) -> bool:
        """Simple wildcard pattern matching."""
        import fnmatch
        return fnmatch.fnmatch(text, pattern)

    def _remove_cache_files(self, key: str):
        """Remove cache and metadata files for a key."""
        cache_key = self._generate_cache_key(key)
        self._remove_cache_files_by_cache_key(cache_key)

    def _remove_cache_files_by_cache_key(self, cache_key: str):
        """Remove cache and metadata files by cache key."""
        try:
            cache_path = os.path.join(self.cache_dir, f"{cache_key}.pkl")
            metadata_path = os.path.join(self.cache_dir, f"{cache_key}_meta.pkl")

            if os.path.exists(cache_path):
                os.remove(cache_path)
            if os.path.exists(metadata_path):
                os.remove(metadata_path)

        except Exception as e:
            logger.warning(f"Failed to remove cache files for {cache_key}: {str(e)}")

    def clear_all(self) -> int:
        """
        Clear all cached data.

        Returns:
            Number of cache entries cleared
        """
        try:
            cache_files = glob.glob(os.path.join(self.cache_dir, "cache_*.pkl"))
            metadata_files = glob.glob(os.path.join(self.cache_dir, "cache_*_meta.pkl"))

            cleared_count = 0

            # Remove all cache files
            for file_path in cache_files + metadata_files:
                try:
                    os.remove(file_path)
                    cleared_count += 1
                except Exception as e:
                    logger.warning(f"Failed to remove cache file {file_path}: {str(e)}")

            logger.info(f"Cleared {cleared_count // 2} cache entries")  # Divide by 2 since each entry has 2 files
            return cleared_count // 2

        except Exception as e:
            logger.error(f"Failed to clear cache: {str(e)}")
            return 0

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        try:
            metadata_files = glob.glob(os.path.join(self.cache_dir, "*_meta.pkl"))

            total_entries = len(metadata_files)
            expired_entries = 0
            total_size = 0
            current_time = time.time()

            for metadata_path in metadata_files:
                try:
                    # Get file size
                    cache_key = os.path.basename(metadata_path).replace('_meta.pkl', '')
                    cache_path = os.path.join(self.cache_dir, f"{cache_key}.pkl")

                    if os.path.exists(cache_path):
                        total_size += os.path.getsize(cache_path)
                        total_size += os.path.getsize(metadata_path)

                    # Check if expired
                    with open(metadata_path, 'rb') as f:
                        metadata = pickle.load(f)

                    if current_time > metadata['expires_at']:
                        expired_entries += 1

                except Exception as e:
                    logger.warning(f"Failed to process metadata file {metadata_path}: {str(e)}")

            return {
                'total_entries': total_entries,
                'expired_entries': expired_entries,
                'active_entries': total_entries - expired_entries,
                'total_size_bytes': total_size,
                'cache_directory': self.cache_dir
            }

        except Exception as e:
            logger.error(f"Failed to get cache stats: {str(e)}")
            return {
                'total_entries': 0,
                'expired_entries': 0,
                'active_entries': 0,
                'total_size_bytes': 0,
                'cache_directory': self.cache_dir
            }


# Global cache instance for module-wide usage
_global_cache_instance = None


def get_global_cache() -> DataCache:
    """Get or create global cache instance."""
    global _global_cache_instance
    if _global_cache_instance is None:
        from .. import CACHE_PATH
        _global_cache_instance = DataCache(cache_dir=CACHE_PATH, default_ttl=24*3600)  # 24 hours default
    return _global_cache_instance
