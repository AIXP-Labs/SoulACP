"""Backwards-compatible re-export of ACPClient from adapters.protocol.

The canonical Protocol definition lives in ``soulacp.adapters.protocol``.
This module re-exports it as ``ACPClientProtocol`` for any code that
imported from here.
"""

from soulacp.adapters.protocol import ACPClient as ACPClientProtocol

__all__ = ["ACPClientProtocol"]
