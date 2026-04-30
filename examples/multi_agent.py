"""Multi-agent example — use different agents for different tasks."""

import asyncio
from soulacp import ManagedSession


async def main():
    # Claude for code generation
    async with ManagedSession(provider="claude", model="claude-sonnet-4-20250514") as claude:
        code = await claude.query("Write a Python function that checks if a number is prime.")
        print("=== Claude (code generation) ===")
        print(code[:300])

    # Gemini for code review
    async with ManagedSession(provider="gemini", model="gemini-3-flash-preview") as gemini:
        review = await gemini.query(f"Review this code for correctness and style:\n{code[:500]}")
        print("\n=== Gemini (code review) ===")
        print(review[:300])


if __name__ == "__main__":
    asyncio.run(main())
