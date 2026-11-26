"""Base notifier interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from omni_monitor.models import AlertRecord


@dataclass
class PushResult:
    status: str  # "sent" | "partial" | "dry_run" | "no_devices" | "failed"
    success_count: int = 0
    failure_count: int = 0
    invalid_tokens: list[str] = field(default_factory=list)
    detail: str = ""


class BaseNotifier(ABC):
    @abstractmethod
    async def send(self, alert: AlertRecord, tokens: list[str]) -> PushResult:
        """Deliver an AlertRecord to registered device tokens."""
