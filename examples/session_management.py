"""Reuse logical CLI sessions when the provider supports restoration.

This does not guarantee survival of an original process or its child agents.
Use synthetic inputs: printed response text is not a redacted diagnostic.
Default permission settings can auto-approve agent requests. Logical user IDs do
not isolate CLI credentials; review README authentication and OS-account boundaries.
"""

import asyncio

from soulacp import FileCache, ManagedSession, ProviderSessionStore


async def main():
    # This demo's unencrypted local cache must never be committed or shared.
    store = ProviderSessionStore(cache=FileCache("sessions.json"))

    async with ManagedSession(
        provider="claude",
        model="claude-acp/sonnet",
        session_store=store,
    ) as session:
        # First request — creates new CLI session
        r1 = await session.query("Remember the number 42.", user_id="alice")
        print(f"Response 1: {r1[:100]}")

        # Second request — reuse the logical mapping if restoration is available
        r2 = await session.query("What number did I ask you to remember?", user_id="alice")
        print(f"Response 2: {r2[:100]}")

        # Different user — gets different CLI session
        r3 = await session.query("Hello!", user_id="bob")
        print(f"Response 3: {r3[:100]}")


if __name__ == "__main__":
    asyncio.run(main())
