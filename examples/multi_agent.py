"""Sequential provider calls, not nested-agent dispatch or workflow authorization.

Requires separately installed/authenticated CLIs and available models. Run only
with synthetic input; stdout contains original responses, not redacted diagnostics.
Default permission settings can auto-approve agent requests. Read the Gemini
authentication warning in README before running: a connection can change its
native credential cache. This demo reviews only the first 500 characters and
prints excerpts, not a complete audit or full original responses.
"""

import asyncio

from soulacp import ManagedSession


async def main():
    # Claude for code generation
    async with ManagedSession(provider="claude", model="claude-acp/sonnet") as claude:
        code = await claude.query("Write a Python function that checks if a number is prime.")
        print("=== Claude (code generation) ===")
        print(code[:300])

    # Gemini for code review
    async with ManagedSession(provider="gemini", model="gemini-acp/gemini-3-flash-preview") as gemini:
        review = await gemini.query(f"Review this code for correctness and style:\n{code[:500]}")
        print("\n=== Gemini (code review) ===")
        print(review[:300])


if __name__ == "__main__":
    asyncio.run(main())
