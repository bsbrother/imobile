"""
# CLI
export GOOGLE_API_KEY=your-api-key-here
mobilerun run "Your task here" \
  --provider GoogleGenAI \
  --model gemini-3.1-flash-lite-preview
#
"""
import asyncio
import os
from mobilerun import MobileAgent
from llama_index.llms.google_genai import GoogleGenAI

# Set your key (or export it as an env var beforehand)
#os.environ["GOOGLE_API_KEY"] = "your-api-key-here"

async def main():
    llm = GoogleGenAI(model="gemini-3.1-flash-lite-preview", temperature=0.2)

    agent = MobileAgent(
        goal="Open Settings and check battery level",
        llms=llm,  # single LLM used for all agents
    )

    result = await agent.run()
    print(f"Success: {result.success}")
    print(f"Reason: {result.reason}")
    print(f"Steps: {result.steps}")

if __name__ == "__main__":
    asyncio.run(main())
