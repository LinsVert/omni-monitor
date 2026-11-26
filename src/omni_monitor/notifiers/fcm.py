"""Google Firebase Cloud Messaging (FCM) notifier with automatic dry-run fallback."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from omni_monitor.models import AlertRecord
from omni_monitor.notifiers.base import BaseNotifier, PushResult

logger = logging.getLogger("omni_monitor.fcm")


class FcmNotifier(BaseNotifier):
    """
    Sends Android High-Priority FCM messages via firebase-admin SDK.
    - When `credentials_path` is empty or missing, runs safely in dry-run mode
      (logs notifications to stdout so local development works without Firebase setup).
    - Automatically collects unregistered/expired tokens so Storage can prune them.
    """

    def __init__(self, credentials_path: str = "") -> None:
        self.credentials_path = credentials_path.strip()
        self._firebase_app = None
        self._enabled = False
        self._init_firebase()

    @property
    def is_live_fcm(self) -> bool:
        return self._enabled

    def _init_firebase(self) -> None:
        if not self.credentials_path:
            logger.info("OMNI_FCM_CREDENTIALS_PATH not set; FcmNotifier running in dry-run mode.")
            return
        cred_file = Path(self.credentials_path)
        if not cred_file.exists():
            logger.warning(
                "FCM credentials file %s does not exist; falling back to dry-run mode.",
                cred_file,
            )
            return

        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(str(cred_file))
        try:
            self._firebase_app = firebase_admin.get_app("omni-monitor")
        except ValueError:
            self._firebase_app = firebase_admin.initialize_app(cred, name="omni-monitor")
        self._enabled = True
        logger.info("Initialized Google FCM notifier using %s", cred_file)

    def _send_sync(self, alert: AlertRecord, tokens: list[str]) -> PushResult:
        from firebase_admin import messaging

        # Compact data payload (<4KB) so APK can open exact detail page via GET /api/alerts/{alert_id}
        data_payload = {
            "alert_id": alert.alert_id,
            "task_id": alert.task_id,
            "plugin_type": alert.plugin_type,
            "severity": alert.severity,
            "title": alert.title[:200],
            "summary": alert.summary[:500],
            "triggered_at": alert.triggered_at.isoformat(),
        }

        channel_id = (
            "omni_alerts_high" if alert.severity == "high" else "omni_alerts_default"
        )
        msg = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(
                title=alert.title[:200],
                body=alert.summary[:500],
            ),
            data=data_payload,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    channel_id=channel_id,
                    click_action="OPEN_ALERT_DETAIL",
                ),
            ),
        )
        response = messaging.send_each_for_multicast(msg, app=self._firebase_app)

        invalid_tokens: list[str] = []
        for token, send_resp in zip(tokens, response.responses):
            if not send_resp.success and send_resp.exception is not None:
                exc_name = type(send_resp.exception).__name__
                if exc_name in ("UnregisteredError", "SenderIdMismatchError"):
                    invalid_tokens.append(token)
                logger.warning("FCM send failed for token %s...: %s", token[:12], send_resp.exception)

        if response.success_count == len(tokens):
            status = "sent"
        elif response.success_count > 0:
            status = "partial"
        else:
            status = "failed"

        return PushResult(
            status=status,
            success_count=response.success_count,
            failure_count=response.failure_count,
            invalid_tokens=invalid_tokens,
            detail=f"ok={response.success_count}, fail={response.failure_count}",
        )

    async def send(self, alert: AlertRecord, tokens: list[str]) -> PushResult:
        if not tokens:
            logger.info("[PUSH:no_devices] [%s] %s — %s", alert.severity, alert.title, alert.summary)
            return PushResult(status="no_devices", detail="No registered FCM tokens")

        if not self._enabled:
            logger.info(
                "[PUSH:dry_run -> %d device(s)] [%s] %s — %s",
                len(tokens),
                alert.severity,
                alert.title,
                alert.summary,
            )
            return PushResult(
                status="dry_run",
                success_count=len(tokens),
                detail=f"Dry-run logged for {len(tokens)} device(s)",
            )

        try:
            return await asyncio.to_thread(self._send_sync, alert, tokens)
        except Exception as exc:
            logger.exception("Unexpected error sending FCM push for %s", alert.alert_id)
            return PushResult(status="failed", failure_count=len(tokens), detail=repr(exc))
