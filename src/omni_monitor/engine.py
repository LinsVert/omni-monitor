"""Core execution engine: batched fetching, sanity checks, rule evaluation, alert persistence, and FCM dispatch."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx

from omni_monitor.config import Settings
from omni_monitor.models import AlertRecord, MonitorTask, Observation, TaskState, utc_now
from omni_monitor.notifiers.base import BaseNotifier
from omni_monitor.registry import CollectorRegistry
from omni_monitor.rules import apply_alert_policy, evaluate_rule
from omni_monitor.storage import Storage

logger = logging.getLogger("omni_monitor.engine")


@dataclass
class TaskRunOutcome:
    task_id: str
    ok: bool
    skipped: bool = False
    alert_id: str | None = None
    error: str | None = None
    observation_summary: dict[str, Any] | None = None


class MonitorEngine:
    def __init__(
        self,
        storage: Storage,
        registry: CollectorRegistry,
        notifier: BaseNotifier,
        settings: Settings,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.storage = storage
        self.registry = registry
        self.notifier = notifier
        self.settings = settings
        self._external_client = http_client

    async def _emit_alert(self, alert: AlertRecord) -> AlertRecord:
        # Persist alert FIRST so push records are never lost even if push transport fails
        self.storage.save_alert(alert)
        tokens = [d.token for d in self.storage.list_device_tokens()]
        push_res = await self.notifier.send(alert, tokens)
        self.storage.update_alert_push_status(alert.alert_id, push_res.status)
        if push_res.invalid_tokens:
            pruned = self.storage.delete_device_tokens(push_res.invalid_tokens)
            logger.info("Pruned %d invalid FCM token(s)", pruned)
        alert.push_status = push_res.status
        return alert

    async def _handle_task_failure(
        self, task: MonitorTask, state: TaskState, err: Exception
    ) -> TaskRunOutcome:
        now = utc_now()
        state.last_checked_at = now
        state.consecutive_failures += 1
        state.last_error = f"{type(err).__name__}: {err}"
        logger.warning(
            "Task %s (%s) failed (%d consecutive): %s",
            task.task_id,
            task.plugin_type,
            state.consecutive_failures,
            state.last_error,
        )

        alert_id: str | None = None
        if (
            state.consecutive_failures >= self.settings.failure_alert_threshold
            and not state.meta_alerted
        ):
            state.meta_alerted = True
            meta_alert = AlertRecord(
                alert_id=f"sys_{uuid.uuid4().hex[:12]}",
                task_id=task.task_id,
                task_name=task.name,
                plugin_type=task.plugin_type,
                severity="high",
                title=f"⚠️ 监控源异常: {task.name}",
                summary=f"已连续失败 {state.consecutive_failures} 次: {state.last_error[:120]}",
                detail_blocks=[
                    {
                        "type": "kv_grid",
                        "items": [
                            {"label": "任务 ID", "value": task.task_id},
                            {"label": "插件类型", "value": task.plugin_type},
                            {"label": "连续失败次数", "value": str(state.consecutive_failures)},
                            {"label": "最近错误", "value": state.last_error},
                        ],
                    }
                ],
                triggered_at=now,
            )
            await self._emit_alert(meta_alert)
            alert_id = meta_alert.alert_id

        self.storage.save_task_state(state)
        return TaskRunOutcome(
            task_id=task.task_id,
            ok=False,
            alert_id=alert_id,
            error=state.last_error,
        )

    async def _process_observation(
        self, task: MonitorTask, obs: Observation
    ) -> TaskRunOutcome:
        now = utc_now()
        state = self.storage.get_task_state(task.task_id)
        prev = self.storage.get_latest_observation(task.task_id)

        # Collection snapshot sanity guard (prevents empty/truncated upstream glitches from spamming diffs)
        if (
            task.rule.type == "collection_diff"
            and prev is not None
            and prev.items is not None
            and len(prev.items) >= 20
        ):
            curr_count = len(obs.items or {})
            min_allowed = int(len(prev.items) * task.rule.min_retention_ratio)
            if curr_count < min_allowed:
                return await self._handle_task_failure(
                    task,
                    state,
                    RuntimeError(
                        f"Snapshot sanity check failed: items dropped from {len(prev.items)} to {curr_count}"
                    ),
                )

        # Fetch history when needed for delta evaluation or chart detail blocks
        history = None
        if obs.value is not None:
            lookback = max(task.rule.window_seconds or 0, 24 * 3600)
            history = self.storage.get_metric_history(
                task.task_id, since=now - timedelta(seconds=lookback), limit=200
            )
            # Append current point virtually so line_chart includes latest reading
            history_with_curr = history + [
                {"timestamp": obs.timestamp.isoformat(), "value": obs.value, "metrics": obs.metrics}
            ]
        else:
            history_with_curr = None

        seen_keys: set[str] | None = None
        if task.rule.type == "new_items":
            seen_keys = self.storage.get_seen_item_keys(task.task_id)
            if not seen_keys and prev is not None and prev.items is not None:
                # One-time migration from existing latest_observations baseline
                self.storage.mark_items_seen(task.task_id, set(prev.items.keys()))
                seen_keys = set(prev.items.keys())

        eval_res = evaluate_rule(
            task, obs, prev, history=history_with_curr, seen_keys=seen_keys
        )
        should_send, next_state, should_disable = apply_alert_policy(
            task.alert_policy, state, eval_res, now, rule=task.rule
        )

        # Reset error counters on success
        next_state.last_success_at = now
        next_state.consecutive_failures = 0
        next_state.last_error = None
        next_state.meta_alerted = False

        # Save observation + state (+ permanent seen keys for new_items dedup)
        self.storage.save_observation(obs, retention_days=self.settings.history_retention_days)
        if task.rule.type == "new_items" and obs.items:
            self.storage.mark_items_seen(task.task_id, set(obs.items.keys()))
        self.storage.save_task_state(next_state)
        if should_disable:
            self.storage.set_task_enabled(task.task_id, False)

        alert_id: str | None = None
        if should_send:
            alert = AlertRecord(
                alert_id=f"alt_{uuid.uuid4().hex[:12]}",
                task_id=task.task_id,
                task_name=task.name,
                plugin_type=task.plugin_type,
                severity=eval_res.severity,  # type: ignore[arg-type]
                title=eval_res.title,
                summary=eval_res.summary,
                detail_blocks=eval_res.detail_blocks,
                triggered_at=now,
            )
            await self._emit_alert(alert)
            alert_id = alert.alert_id

        summary: dict[str, Any] = {
            "timestamp": obs.timestamp.isoformat(),
            "value": obs.value,
            "unit": obs.unit,
            "state": obs.state,
            "items_count": len(obs.items) if obs.items is not None else None,
            "rule_matched": eval_res.matched,
            "alert_emitted": alert_id is not None,
        }
        return TaskRunOutcome(
            task_id=task.task_id,
            ok=True,
            alert_id=alert_id,
            observation_summary=summary,
        )

    async def run_tasks(
        self, tasks: list[MonitorTask], *, force: bool = False
    ) -> list[TaskRunOutcome]:
        """Run a set of tasks, batching by plugin_type with timeout & error isolation."""
        if not tasks:
            return []

        now = utc_now()
        outcomes: list[TaskRunOutcome] = []
        grouped: dict[str, list[tuple[MonitorTask, Any]]] = defaultdict(list)

        for task in tasks:
            try:
                collector = self.registry.get(task.plugin_type)
                cfg = collector.parse_config(task.plugin_config)
            except Exception as exc:
                state = self.storage.get_task_state(task.task_id)
                outcomes.append(await self._handle_task_failure(task, state, exc))
                continue

            if not force and not collector.should_run(now, cfg):
                outcomes.append(TaskRunOutcome(task_id=task.task_id, ok=True, skipped=True))
                continue

            grouped[task.plugin_type].append((task, cfg))

        async def _execute_with_client(client: httpx.AsyncClient) -> None:
            for plugin_type, entries in grouped.items():
                collector = self.registry.get(plugin_type)
                batch_input = [(t.task_id, cfg) for t, cfg in entries]
                task_map = {t.task_id: t for t, _ in entries}
                try:
                    batch_results = await asyncio.wait_for(
                        collector.fetch_batch(batch_input, client),
                        timeout=self.settings.default_fetch_timeout,
                    )
                except Exception as exc:
                    for t, _ in entries:
                        state = self.storage.get_task_state(t.task_id)
                        outcomes.append(await self._handle_task_failure(t, state, exc))
                    continue

                for tid, res in batch_results.items():
                    task = task_map[tid]
                    if isinstance(res, Exception):
                        state = self.storage.get_task_state(tid)
                        outcomes.append(await self._handle_task_failure(task, state, res))
                    else:
                        outcomes.append(await self._process_observation(task, res))

        if self._external_client is not None:
            await _execute_with_client(self._external_client)
        else:
            async with httpx.AsyncClient(
                timeout=self.settings.default_fetch_timeout, follow_redirects=True
            ) as client:
                await _execute_with_client(client)

        return outcomes

    async def run_task_by_id(self, task_id: str, *, force: bool = True) -> TaskRunOutcome:
        task = self.storage.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        results = await self.run_tasks([task], force=force)
        return results[0]
