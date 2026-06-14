"""
Gemini Free API — llama_index GoogleGenAI wrapper for free tier models.
Works with gemini-3.1-flash-lite-preview (GOOGLE_API_KEY from .env).

Usage:
    from utils.gemini_free_api import create_free_llm
    llm = create_free_llm()
    # or for DroidRun MobileAgent:
    agent = MobileAgent(goal="...", llms=llm)

Compared to gemini_thinking.py:
    - No thinking_config (causes 405 on free tier)
    - Uses gemini-3.1-flash-lite-preview default
    - Minimal config: just model + temperature
"""

import os
import logging
from typing import Optional

from llama_index.llms.google_genai import GoogleGenAI

logger = logging.getLogger("droidrun")

# ── Free Gemini model ──
# gemini-3.1-flash-lite-preview: free tier, vision-enabled, llama_index compatible
# gemini-2.5-flash:             free tier, proven stable (fallback)
DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"


def create_free_llm(
    model: Optional[str] = None,
    temperature: float = 0.2,
    api_key: Optional[str] = None,
) -> GoogleGenAI:
    """
    Create GoogleGenAI for free Gemini tier — minimal config, no thinking mode.

    Args:
        model: Gemini model (default: gemini-3.1-flash-lite-preview)
        temperature: Generation temperature (default: 0.2)
        api_key: Google API key (reads GOOGLE_API_KEY from .env if None)

    Returns:
        GoogleGenAI instance compatible with DroidRun MobileAgent
    """
    if model is None:
        model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)

    if api_key is None:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

    return GoogleGenAI(
        model=model,
        api_key=api_key,
        temperature=temperature,
    )


def create_with_thinking(
    model: Optional[str] = None,
    thinking_budget: int = 0,
    temperature: float = 0.1,
    api_key: Optional[str] = None,
) -> GoogleGenAI:
    """
    Create GoogleGenAI with thinking mode (for paid API keys).
    Falls back to create_free_llm() if thinking causes 405.

    Args:
        model: Gemini model
        thinking_budget: -1=unlimited, 0=disabled
        temperature: Generation temperature
        api_key: Google API key
    """
    if model is None:
        model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)

    if api_key is None:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

    try:
        from google.genai import types
        thinking_config = types.ThinkingConfig(thinking_budget=thinking_budget)
        generation_config = types.GenerateContentConfig(thinking_config=thinking_config)

        return GoogleGenAI(
            model=model,
            api_key=api_key,
            temperature=temperature,
            generation_config=generation_config,
        )
    except Exception as e:
        logger.warning(f"Thinking mode failed ({e}), falling back to free mode")
        return create_free_llm(model=model, temperature=temperature, api_key=api_key)
