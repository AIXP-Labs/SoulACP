"""Streaming example requiring an installed, authenticated provider CLI.

Use synthetic input: yielded text is original content, not redacted diagnostics.
"""

import asyncio

from soulacp import ACPConfig, ACPConnectionPool, resolve_client_class


async def main():
    config = ACPConfig(provider="claude", model="claude-acp/sonnet")
    pool = ACPConnectionPool(config, resolve_client_class("claude"))

    async with pool, pool.acquire() as (client, _session_id):
        print("Session established\n")
        async for chunk in client.query_stream("Count from 1 to 5, one number per line."):
            print(chunk, end="", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
