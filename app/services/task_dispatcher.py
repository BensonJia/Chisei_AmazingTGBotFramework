from __future__ import annotations

import asyncio
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Callable, TypeVar

T = TypeVar("T")


class SessionTaskDispatcher:
    def __init__(self, max_workers: int = 8) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="bot-worker")
        self._session_locks: dict[str, Lock] = defaultdict(Lock)

    async def run(self, session_key: str, fn: Callable[..., T], *args, **kwargs) -> T:
        loop = asyncio.get_running_loop()
        lock = self._session_locks[session_key]

        def wrapped() -> T:
            with lock:
                return fn(*args, **kwargs)

        return await loop.run_in_executor(self._pool, wrapped)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

