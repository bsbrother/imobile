"""
Gemini Free API — llama_index GoogleGenAI wrapper for free tier models.
All config from .env: GOOGLE_API_KEY, GEMINI_MODEL.

Usage:
    from utils.gemini_free_api import create_free_llm
    llm = create_free_llm()
    # or for DroidRun MobileAgent:
    agent = MobileAgent(goal="...", llms=llm)
"""

import os
import logging
from typing import Optional

from llama_index.llms.google_genai import GoogleGenAI

logger = logging.getLogger("droidrun")


def create_free_llm(
    model: Optional[str] = None,
    temperature: float = 0.2,
    api_key: Optional[str] = None,
) -> GoogleGenAI:
    """
    Create GoogleGenAI for free Gemini tier — minimal config, no thinking mode.
    Reads GOOGLE_API_KEY and GEMINI_MODEL from .env if not passed explicitly.

    Args:
        model: Gemini model (default: GEMINI_MODEL from .env)
        temperature: Generation temperature (default: 0.2)
        api_key: Google API key (default: GOOGLE_API_KEY from .env)

    Returns:
        GoogleGenAI instance compatible with DroidRun MobileAgent
    """
    if model is None:
        model = os.getenv("GEMINI_MODEL")
        if not model:
            raise ValueError("GEMINI_MODEL not set in .env")

    if api_key is None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY not set in .env")

    return GoogleGenAI(
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_tokens=8192,
        context_window=1048576,
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
        model = os.getenv("GEMINI_MODEL")
        if not model:
            raise ValueError("GEMINI_MODEL not set in .env")

    if api_key is None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY not set in .env")

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
