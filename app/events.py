"""In-process pub/sub that feeds the admin's Server-Sent Events stream.

Bookings are written from worker threads (sync endpoints), so `publish` hands
events to the event loop with `call_soon_threadsafe`. This keeps the stack
simple and is why the app runs as a single Uvicorn worker (see README).
"""

from __future__ import annotations

import asyncio
from collections import deque


class EventBus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue] = set()
        self.recent: deque[dict] = deque(maxlen=50)

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, maxsize: int = 100) -> asyncio.Queue:
        """Call from the event loop."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._queues.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)

    def publish(self, event: dict) -> None:
        """Thread-safe. Events are dropped silently if nobody is listening."""
        self.recent.append(event)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._fanout(event)
        else:
            loop.call_soon_threadsafe(self._fanout, event)

    def _fanout(self, event: dict) -> None:
        for queue in list(self._queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a stalled client resyncs when it reconnects
