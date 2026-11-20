"""Alert manager: severity, de-duplication, the 30 s "I'm OK" window, and SMS escalation.

Pure logic driven by the caller's clock (`now` in seconds) so it is deterministic in tests.
Critical alerts: buzzer + LED and a countdown; if nobody presses "I'm OK" the alert becomes
`sms_due` and the service sends the SMS. Warnings/info only notify the app.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Alerts

LEVELS = ("info", "warning", "critical")


@dataclass
class Alert:
    id: str
    level: str
    kind: str
    message: str
    t: float                          # raise time (engine clock)
    context: dict[str, Any] = field(default_factory=dict)
    # active -> (cancelled | sms_pending -> sms_sent | sms_failed);  notified = no SMS needed
    status: str = "notified"
    countdown_ends: float | None = None
    sms_due_t: float | None = None
    sms_sent: int = 0
    acknowledged_by: str | None = None

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "level": self.level, "kind": self.kind, "message": self.message,
                "raised_t": self.t, "status": self.status, "sms_sent": self.sms_sent,
                "countdown_ends": self.countdown_ends, "acknowledged_by": self.acknowledged_by}


class AlertManager:
    def __init__(self, cfg: Alerts, on_change: Callable[[Alert, str], None] | None = None):
        self.cfg = cfg
        self.on_change = on_change or (lambda a, why: None)
        self.last_raised: dict[str, float] = {}
        self.alerts: list[Alert] = []

    # ---- raising ----------------------------------------------------------------------------
    def raise_alert(self, kind: str, level: str, message: str, now: float,
                    context: dict[str, Any] | None = None,
                    countdown_s: float | None = None) -> Alert | None:
        if level not in LEVELS:
            raise ValueError(level)
        cooldown = self.cfg.cooldown_s.get(level, 600.0)
        last = self.last_raised.get(kind)
        if last is not None and now - last < cooldown:
            return None                                   # de-duplicated
        self.last_raised[kind] = now
        a = Alert(str(uuid.uuid4()), level, kind, message, now, context or {})
        if level == "critical":
            cd = self.cfg.countdown_s if countdown_s is None else countdown_s
            a.status = "active"
            a.countdown_ends = now + cd
        self.alerts.append(a)
        self.on_change(a, "raised")
        return a            # a zero countdown (SOS) is picked up by the very next tick()

    # ---- time passes ------------------------------------------------------------------------
    def tick(self, now: float) -> list[Alert]:
        """Move expired countdowns to sms_pending. Returns alerts whose SMS must be sent now."""
        due = []
        for a in self.alerts:
            if a.status == "active" and a.countdown_ends is not None and now >= a.countdown_ends:
                self._due(a, now)
                due.append(a)
        return due

    def _due(self, a: Alert, now: float) -> None:
        a.status, a.sms_due_t = "sms_pending", now
        self.on_change(a, "sms_due")

    # ---- user actions -----------------------------------------------------------------------
    def cancel(self, now: float, by: str = "button") -> list[Alert]:
        """'I'm OK': cancel every critical alert still inside its countdown."""
        out = []
        for a in self.alerts:
            if a.status == "active":
                a.status, a.acknowledged_by = "cancelled", by
                self.on_change(a, "cancelled")
                out.append(a)
        return out

    def ack(self, alert_id: str, by: str) -> Alert | None:
        for a in self.alerts:
            if a.id == alert_id:
                a.acknowledged_by = by
                self.on_change(a, "acknowledged")
                return a
        return None

    def sms_done(self, a: Alert, delivered: int) -> None:
        a.sms_sent = delivered
        a.status = "sms_sent" if delivered else "sms_failed"
        self.on_change(a, "sms_done")

    # ---- queries ----------------------------------------------------------------------------
    @property
    def pending_countdown(self) -> list[Alert]:
        return [a for a in self.alerts if a.status == "active"]

    @property
    def unacknowledged(self) -> list[Alert]:
        return [a for a in self.alerts
                if a.level != "info" and a.acknowledged_by is None and a.status != "cancelled"]

    def recent(self, n: int = 20) -> list[Alert]:
        return list(reversed(self.alerts[-n:]))
