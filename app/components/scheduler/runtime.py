"""Run work on the API's main event loop from the reaper's background thread.

The reaper runs in its own thread/event loop (so it can never block the API), but the
live WebSocket objects, the Redis bus client and the per-process caches belong to the API's
main loop.  ``on_main_loop`` forwards a coroutine there when called from another loop and
simply awaits it otherwise (CLI / tests / same loop).
"""

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _main_loop
    _main_loop = loop


async def on_main_loop(coro: Coroutine[Any, Any, T], timeout: float = 10.0) -> T:
    """Await ``coro`` on the API main loop (or here if we already are on it / it is unset)."""
    main = _main_loop
    if main is None or main.is_closed() or main is asyncio.get_running_loop():
        return await coro
    future = asyncio.run_coroutine_threadsafe(coro, main)
    return await asyncio.wait_for(asyncio.wrap_future(future), timeout)
