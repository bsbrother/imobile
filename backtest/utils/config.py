"""
Configuration management system for API keys and strategy parameters.
"""

import os
import json5 as json # Support comments, e.g. /*..*/ //..., but not jsonref.
from typing import Dict, Any, Optional
from pathlib import Path

from .exceptions import ConfigurationError


class ConfigManager:
    """Manages configuration for API keys and strategy parameters."""

    def __init__(self, config_file: Optional[str] = None):
        """
        Initialize configuration manager.

        Args:
            config_file: Path to configuration file. If None, uses default locations.
        """
        self.config_file = config_file if config_file else './config.json'
        self.config = {}
        self.load_config()

    def load_config(self):
        """Load configuration from file."""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
                self.config = resolve_refs(raw_data)
        except Exception as e:
            raise ConfigurationError(f"Failed to load config from {self.config_file}: {str(e)}")

    def save_config(self):
        """Save configuration to file."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)

            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            raise ConfigurationError(f"Failed to save config to {self.config_file}: {str(e)}")

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value.

        Args:
            key: Configuration key (supports dot notation like 'tushare.token')
            default: Default value if key not found

        Returns:
            Configuration value
        """
        keys = key.split('.')
        value = self.config

        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default

    def set(self, key: str, value: Any):
        """
        Set configuration value.

        Args:
            key: Configuration key (supports dot notation)
            value: Value to set
        """
        keys = key.split('.')
        config = self.config

        # Navigate to parent of target key
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        # Set the value
        config[keys[-1]] = value

    def get_tushare_token(self) -> str:
        """
        Get Tushare API token.

        Returns:
            Tushare token

        Raises:
            ConfigurationError: If token not found
        """
        # Try environment variable first
        token = os.getenv('TUSHARE_TOKEN')
        if token:
            return token

        # Try configuration file
        token = self.get('tushare.token')
        if token:
            return token

        raise ConfigurationError(
            "Tushare token not found. Please set TUSHARE_TOKEN environment variable "
            "or add 'tushare.token' to configuration file."
        )

    def get_strategy_config(self, strategy_name: str) -> Dict[str, Any]:
        """
        Get strategy configuration.

        Args:
            strategy_name: Name of the strategy

        Returns:
            Strategy configuration dictionary
        """
        return self.get(f'strategies.{strategy_name}', {})


def resolve_refs(data, root=None):
    """
    Support JSON pointer syntax, e.g. "$ref": "x.y.z",
    """
    if root is None:
        root = data
    if isinstance(data, dict):
        # Handle references
        if "$ref" in data:
            ref_path = data["$ref"].split('.')
            current = root
            for part in ref_path:
                current = current[part]
            return current
        # Process children
        return {k: resolve_refs(v, root) for k, v in data.items()}
    elif isinstance(data, list):
        return [resolve_refs(item, root) for item in data]
    else:
        return data

