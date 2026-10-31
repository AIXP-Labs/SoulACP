"""ManagedSession example requiring an installed, authenticated provider CLI.

Use synthetic input: stdout contains the original reply, not redacted diagnostics.
"""

import asyncio

from soulacp import ManagedSession


async def main():
    async with ManagedSession(provider="claude", model="claude-acp/sonnet") as session:
        response = await session.query("Hello! What can you do?")
        print(response)


if __name__ == "__main__":
    asyncio.run(main())
