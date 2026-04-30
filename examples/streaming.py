"""Streaming response example."""

import asyncio
from soulacp import ACPConfig, ACPConnectionPool, resolve_client_class


async def main():
    config = ACPConfig(provider="claude", model="claude-sonnet-4-20250514")
    pool = ACPConnectionPool(config, resolve_client_class("claude"))

    async with pool.acquire() as (client, session_id):
        print(f"Session: {session_id}\n")
        async for chunk in client.query_stream("Count from 1 to 5, one number per line."):
            print(chunk, end="", flush=True)

    await pool.close_all()


if __name__ == "__main__":
    asyncio.run(main())
