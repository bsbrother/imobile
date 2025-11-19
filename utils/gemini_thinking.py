"""
Google Gemini with Thinking Mode Support for DroidRun
Provides GoogleGenAI configuration with native thinking mode support.
"""

import logging
import os
from typing import Optional

from google.genai import types
from llama_index.llms.google_genai import GoogleGenAI

logger = logging.getLogger("droidrun")


def create_gemini_with_thinking(
    thinking_budget: int = -1,
    model: str = "gemini-2.5-flash",
    api_key: Optional[str] = None,
    **kwargs
) -> GoogleGenAI:
    """
    Create GoogleGenAI with native thinking mode support

    Args:
        thinking_budget: Budget for thinking tokens (-1 = unlimited, 0 = disabled)
        model: Gemini model to use
        api_key: Google API key (uses env var if not provided)
        **kwargs: Additional arguments passed to GoogleGenAI

    Returns:
        Configured GoogleGenAI instance with thinking mode
    """
    # Get API key from environment if not provided
    if api_key is None:
        api_key = os.getenv('GOOGLE_API_KEY')

    # Configure thinking mode
    thinking_config = types.ThinkingConfig(thinking_budget=thinking_budget)

    # Configure generation with thinking mode
    generation_config = types.GenerateContentConfig(
        thinking_config=thinking_config
    )

    # Create and return GoogleGenAI instance with thinking configuration
    return GoogleGenAI(
        model=model,
        api_key=api_key,
        generation_config=generation_config,
        **kwargs
    )

if __name__ == "__main__":
    import os
    import dotenv
    dotenv.load_dotenv(os.path.expanduser('.env'), verbose=True)
    GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY', 'gemini-2.5-flash')
    GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
    GEMINI_THINKING_BUDGET = os.getenv('GEMINI_THINKING_BUDGET', '-1')

    prompt = "What is 2 + 2? Please think through this step by step."
    print('Prompt: ', prompt )
    llm = create_gemini_with_thinking(
        api_key=GOOGLE_API_KEY,
        model=GEMINI_MODEL,
        thinking_budget=int(GEMINI_THINKING_BUDGET),
        temperature=0.1
    )
    response = llm.complete(prompt)
    print(f"Response: {response}")

    # Test model vision capabilities by input a simple image URL
    vision_prompt = ("Analyze the image at this URL and describe its contents in detail: "
                     "https://www.gstatic.com/webp/gallery/1.jpg")
    print('Vision Prompt: ', vision_prompt )
    llm_vision = create_gemini_with_thinking(
        api_key=GOOGLE_API_KEY,
        model='gemini-2.5-flash',
        thinking_budget=0,
        temperature=0.1,
        vision=True,         # Set to True for vision models, False for text-only
        reflection=True,     # Enable reflection for vision tasks
    )
    vision_response = llm_vision.complete(vision_prompt)
    print(f"Vision Response: {vision_response}")