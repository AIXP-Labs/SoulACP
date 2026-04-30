"""Session management example — reuse CLI sessions across requests."""

import asyncio
from soulacp import ManagedSession, ProviderSessionStore, FileCache


async def main():
    # Use file-based cache for persistent sessions across restarts
    store = ProviderSessionStore(cache=FileCache("sessions.json"))

    async with ManagedSession(
        provider="claude",
        model="claude-sonnet-4-20250514",
        session_store=store,
    ) as session:
        # First request — creates new CLI session
        r1 = await session.query("Remember the number 42.", user_id="alice")
        print(f"Response 1: {r1[:100]}")

        # Second request — reuses same CLI session
        r2 = await session.query("What number did I ask you to remember?", user_id="alice")
        print(f"Response 2: {r2[:100]}")

        # Different user — gets different CLI session
        r3 = await session.query("Hello!", user_id="bob")
        print(f"Response 3: {r3[:100]}")


if __name__ == "__main__":
    asyncio.run(main())
