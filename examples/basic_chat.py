"""Basic chat example using ManagedSession."""

import asyncio
from soulacp import ManagedSession


async def main():
    async with ManagedSession(provider="claude", model="claude-sonnet-4-20250514") as session:
        response = await session.query("Hello! What can you do?")
        print(response)


if __name__ == "__main__":
    asyncio.run(main())
