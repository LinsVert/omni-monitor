"""SQLite WAL repository for tasks, observations, metric history, alerts, and FCM tokens."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from omni_monitor.models import (
    AlertPolicy,
    AlertRecord,
    DeviceToken,
    MonitorTask,
    Observation,
    RuleConfig,
    TaskState,
    utc_now,
)


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _from_iso(val: str | None) -> datetime | None:
    if not val:
        return None
    dt = datetime.fromisoformat(val)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class Storage:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    plugin_type TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    plugin_config_json TEXT NOT NULL,
                    rule_json TEXT NOT NULL,
                    alert_policy_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_states (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'IDLE',
                    last_triggered_at TEXT,
                    last_checked_at TEXT,
                    last_success_at TEXT,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    meta_alerted INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS latest_observations (
                    task_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS metric_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    value REAL,
                    metrics_json TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_metric_history_task_ts
                    ON metric_history(task_id, timestamp DESC);

                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    task_name TEXT NOT NULL,
                    plugin_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    detail_blocks_json TEXT NOT NULL,
                    triggered_at TEXT NOT NULL,
                    is_read INTEGER NOT NULL DEFAULT 0,
                    push_status TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_triggered_at
                    ON alerts(triggered_at DESC);
                CREATE INDEX IF NOT EXISTS idx_alerts_task_id
                    ON alerts(task_id, triggered_at DESC);

                CREATE TABLE IF NOT EXISTS device_tokens (
                    token TEXT PRIMARY KEY,
                    platform TEXT NOT NULL DEFAULT 'android',
                    device_name TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS seen_items (
                    task_id TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, item_key),
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                );
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def _row_to_task(self, row: sqlite3.Row) -> MonitorTask:
        return MonitorTask(
            task_id=row["task_id"],
            name=row["name"],
            enabled=bool(row["enabled"]),
            plugin_type=row["plugin_type"],
            interval_seconds=row["interval_seconds"],
            plugin_config=json.loads(row["plugin_config_json"]),
            rule=RuleConfig.model_validate(json.loads(row["rule_json"])),
            alert_policy=AlertPolicy.model_validate(json.loads(row["alert_policy_json"])),
            created_at=_from_iso(row["created_at"]) or utc_now(),
            updated_at=_from_iso(row["updated_at"]) or utc_now(),
        )

    def upsert_task(self, task: MonitorTask) -> MonitorTask:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    task_id, name, enabled, plugin_type, interval_seconds,
                    plugin_config_json, rule_json, alert_policy_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    name = excluded.name,
                    enabled = excluded.enabled,
                    plugin_type = excluded.plugin_type,
                    interval_seconds = excluded.interval_seconds,
                    plugin_config_json = excluded.plugin_config_json,
                    rule_json = excluded.rule_json,
                    alert_policy_json = excluded.alert_policy_json,
                    updated_at = excluded.updated_at
                """,
                (
                    task.task_id,
                    task.name,
                    int(task.enabled),
                    task.plugin_type,
                    task.interval_seconds,
                    json.dumps(task.plugin_config, ensure_ascii=False),
                    task.rule.model_dump_json(),
                    task.alert_policy.model_dump_json(),
                    _to_iso(task.created_at),
                    _to_iso(task.updated_at),
                ),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO task_states (task_id) VALUES (?)",
                (task.task_id,),
            )
        return task

    def get_task(self, task_id: str) -> MonitorTask | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(self, enabled_only: bool = False) -> list[MonitorTask]:
        sql = "SELECT * FROM tasks"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at ASC"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [self._row_to_task(r) for r in rows]

    def delete_task(self, task_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            return cur.rowcount > 0

    def set_task_enabled(self, task_id: str, enabled: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE tasks SET enabled = ?, updated_at = ? WHERE task_id = ?",
                (int(enabled), _to_iso(utc_now()), task_id),
            )

    # ------------------------------------------------------------------
    # Task States
    # ------------------------------------------------------------------

    def get_task_state(self, task_id: str) -> TaskState:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task_states WHERE task_id = ?", (task_id,)
            ).fetchone()
        if not row:
            return TaskState(task_id=task_id)
        return TaskState(
            task_id=row["task_id"],
            status=row["status"],
            last_triggered_at=_from_iso(row["last_triggered_at"]),
            last_checked_at=_from_iso(row["last_checked_at"]),
            last_success_at=_from_iso(row["last_success_at"]),
            consecutive_failures=row["consecutive_failures"],
            last_error=row["last_error"],
            meta_alerted=bool(row["meta_alerted"]),
        )

    def save_task_state(self, state: TaskState) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO task_states (
                    task_id, status, last_triggered_at, last_checked_at,
                    last_success_at, consecutive_failures, last_error, meta_alerted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    last_triggered_at = excluded.last_triggered_at,
                    last_checked_at = excluded.last_checked_at,
                    last_success_at = excluded.last_success_at,
                    consecutive_failures = excluded.consecutive_failures,
                    last_error = excluded.last_error,
                    meta_alerted = excluded.meta_alerted
                """,
                (
                    state.task_id,
                    state.status,
                    _to_iso(state.last_triggered_at),
                    _to_iso(state.last_checked_at),
                    _to_iso(state.last_success_at),
                    state.consecutive_failures,
                    state.last_error,
                    int(state.meta_alerted),
                ),
            )

    # ------------------------------------------------------------------
    # Observations & Metric History
    # ------------------------------------------------------------------

    def get_latest_observation(self, task_id: str) -> Observation | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM latest_observations WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if not row:
            return None
        return Observation.model_validate(json.loads(row["payload_json"]))

    def save_observation(self, obs: Observation, retention_days: int = 30) -> None:
        ts_iso = _to_iso(obs.timestamp) or _to_iso(utc_now())
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO latest_observations (task_id, timestamp, payload_json)
                VALUES (?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    timestamp = excluded.timestamp,
                    payload_json = excluded.payload_json
                """,
                (obs.task_id, ts_iso, obs.model_dump_json()),
            )
            if obs.value is not None:
                self._conn.execute(
                    """
                    INSERT INTO metric_history (task_id, timestamp, value, metrics_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        obs.task_id,
                        ts_iso,
                        obs.value,
                        json.dumps(obs.metrics, ensure_ascii=False),
                    ),
                )
                cutoff = _to_iso(utc_now() - timedelta(days=retention_days))
                self._conn.execute(
                    "DELETE FROM metric_history WHERE task_id = ? AND timestamp < ?",
                    (obs.task_id, cutoff),
                )

    def get_metric_history(
        self, task_id: str, since: datetime | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self._lock:
            if since is not None:
                rows = self._conn.execute(
                    """
                    SELECT timestamp, value, metrics_json FROM metric_history
                    WHERE task_id = ? AND timestamp >= ?
                    ORDER BY timestamp ASC
                    LIMIT ?
                    """,
                    (task_id, _to_iso(since), limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT timestamp, value, metrics_json FROM (
                        SELECT timestamp, value, metrics_json FROM metric_history
                        WHERE task_id = ?
                        ORDER BY timestamp DESC
                        LIMIT ?
                    ) ORDER BY timestamp ASC
                    """,
                    (task_id, limit),
                ).fetchall()
        return [
            {
                "timestamp": r["timestamp"],
                "value": r["value"],
                "metrics": json.loads(r["metrics_json"]),
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def _row_to_alert(self, row: sqlite3.Row) -> AlertRecord:
        return AlertRecord(
            alert_id=row["alert_id"],
            task_id=row["task_id"],
            task_name=row["task_name"],
            plugin_type=row["plugin_type"],
            severity=row["severity"],
            title=row["title"],
            summary=row["summary"],
            detail_blocks=json.loads(row["detail_blocks_json"]),
            triggered_at=_from_iso(row["triggered_at"]) or utc_now(),
            is_read=bool(row["is_read"]),
            push_status=row["push_status"],
        )

    def save_alert(self, alert: AlertRecord) -> AlertRecord:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO alerts (
                    alert_id, task_id, task_name, plugin_type, severity,
                    title, summary, detail_blocks_json, triggered_at,
                    is_read, push_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    alert.alert_id,
                    alert.task_id,
                    alert.task_name,
                    alert.plugin_type,
                    alert.severity,
                    alert.title,
                    alert.summary,
                    json.dumps(alert.detail_blocks, ensure_ascii=False),
                    _to_iso(alert.triggered_at),
                    int(alert.is_read),
                    alert.push_status,
                ),
            )
        return alert

    def update_alert_push_status(self, alert_id: str, push_status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE alerts SET push_status = ? WHERE alert_id = ?",
                (push_status, alert_id),
            )

    def list_alerts(
        self,
        task_id: str | None = None,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AlertRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if unread_only:
            clauses.append("is_read = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM alerts {where} ORDER BY triggered_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_alert(r) for r in rows]

    def get_alert(self, alert_id: str) -> AlertRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)
            ).fetchone()
        return self._row_to_alert(row) if row else None

    def mark_alert_read(self, alert_id: str, is_read: bool = True) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE alerts SET is_read = ? WHERE alert_id = ?",
                (int(is_read), alert_id),
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Device FCM Tokens
    # ------------------------------------------------------------------

    def upsert_device_token(
        self, token: str, platform: str = "android", device_name: str = ""
    ) -> DeviceToken:
        now = utc_now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO device_tokens (token, platform, device_name, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(token) DO UPDATE SET
                    platform = excluded.platform,
                    device_name = excluded.device_name,
                    updated_at = excluded.updated_at
                """,
                (token, platform, device_name, _to_iso(now)),
            )
        return DeviceToken(
            token=token, platform=platform, device_name=device_name, updated_at=now
        )

    def list_device_tokens(self) -> list[DeviceToken]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM device_tokens ORDER BY updated_at DESC"
            ).fetchall()
        return [
            DeviceToken(
                token=r["token"],
                platform=r["platform"],
                device_name=r["device_name"],
                updated_at=_from_iso(r["updated_at"]) or utc_now(),
            )
            for r in rows
        ]

    def delete_device_tokens(self, tokens: list[str]) -> int:
        if not tokens:
            return 0
        placeholders = ",".join("?" for _ in tokens)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"DELETE FROM device_tokens WHERE token IN ({placeholders})",
                tokens,
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # Permanent Seen Item Keys (idempotent dedup for new_items rules)
    # ------------------------------------------------------------------

    def get_seen_item_keys(self, task_id: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT item_key FROM seen_items WHERE task_id = ?",
                (task_id,),
            ).fetchall()
        return {r["item_key"] for r in rows}

    def mark_items_seen(self, task_id: str, item_keys: list[str] | set[str]) -> None:
        if not item_keys:
            return
        now_iso = _to_iso(utc_now())
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR IGNORE INTO seen_items (task_id, item_key, first_seen_at) VALUES (?, ?, ?)",
                [(task_id, k, now_iso) for k in item_keys],
            )
