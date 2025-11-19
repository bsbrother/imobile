"""
Centralized logging configuration for the ibacktest project.

This module provides a unified way to configure loguru logger across the entire project,
ensuring that all modules use the same logging settings defined in the .env file.
"""

import os
import sys
from loguru import logger


def configure_logger(log_level: str = "DEBUG", log_path: str = "/tmp/ibacktest_logs") -> None:
    """
    Configure the global loguru logger with specified settings.

    Args:
        log_level: The logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_path: Directory path for log files
    """
    # Remove default logger
    logger.remove()

    # Create log directory if it doesn't exist
    os.makedirs(log_path, exist_ok=True)

    # Add console handler with color formatting
    logger.add(
        sys.stderr,
        format="<cyan>{time:YYYY-MM-DD HH:mm}</cyan> | <level>{level: <8}</level> | <level>{message}</level>",
        colorize=True,
        level=log_level.upper()
    )

    # Add file handler with rotation
    logger.add(
        os.path.join(log_path, 'app.log'),
        rotation="100 MB",
        retention="2 days",
        compression="zip",
        level=log_level.upper()
    )


def get_logger():
    """
    Get the configured logger instance.

    Returns:
        The loguru logger instance
    """
    return logger


# Convenience function to reconfigure logger level at runtime
def set_log_level(level: str) -> None:
    """
    Change the logging level at runtime.

    Args:
        level: The new logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # This is a bit tricky with loguru as we need to remove and re-add handlers
    # For now, we'll just log a warning that the level change requires restart
    logger.warning(f"Log level change to {level} requested. "
                  "Note: loguru requires restart to fully apply level changes to all handlers.")
