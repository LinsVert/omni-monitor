"""Lightweight in-process asyncio scheduler driving MonitorEngine."""

from __future__ import annotations

import asyncio
import logging

from omni_monitor.engine import MonitorEngine
from omni_monitor.models import MonitorTask, utc_now
from omni_monitor.storage import Storage

logger = logging.getLogger("omni_monitor.scheduler")


class MonitorScheduler:
    def __init__(
        self,
        storage: Storage,
        engine: MonitorEngine,
        tick_seconds: int = 10,
    ) -> None:
        self.storage = storage
        self.engine = engine
        self.tick_seconds = max(1, tick_seconds)
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._running_ids: set[str] = set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._loop(), name="omni-monitor-scheduler")
            logger.info("MonitorScheduler started (tick=%ds)", self.tick_seconds)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("MonitorScheduler stopped")

    def _select_due_tasks(self) -> list[MonitorTask]:
        now = utc_now()
        due: list[MonitorTask] = []
        for task in self.storage.list_tasks(enabled_only=True):
            if task.task_id in self._running_ids:
                continue
            state = self.storage.get_task_state(task.task_id)
            if state.last_checked_at is None:
                due.append(task)
                continue
            elapsed = (now - state.last_checked_at).total_seconds()
            if elapsed >= task.interval_seconds:
                due.append(task)
        return due

    async def tick_once(self) -> int:
        due = self._select_due_tasks()
        if not due:
            return 0
        ids = {t.task_id for t in due}
        self._running_ids.update(ids)
        try:
            await self.engine.run_tasks(due, force=False)
        finally:
            self._running_ids.difference_update(ids)
        return len(due)

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unhandled error in scheduler tick")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.tick_seconds)
            except asyncio.TimeoutError:
                pass
