"""
Alert system for notifications.
"""

import asyncio
import logging
import time
from collections import deque
from typing import Optional

from app.schemas import Alert, AlertSeverity

logger = logging.getLogger(__name__)


class AlertManager:
    """
    Manages alert dispatch with rate limiting.
    """

    def __init__(
        self,
        rate_limit_per_minute: int = 10,
        slack_webhook: str = None,
        email_recipients: list = None
    ):
        self.rate_limit = rate_limit_per_minute
        self.slack_webhook = slack_webhook
        self.email_recipients = email_recipients or []

        self._alert_history: deque = deque(maxlen=1000)
        self._alerts_per_minute: deque = deque(maxlen=60)
        self._last_alert_time = 0

    def send_alert(
        self,
        severity: AlertSeverity,
        category: str,
        message: str,
        source_module: str,
        details: dict = None,
        trace_id: str = None
    ) -> Optional[Alert]:
        """Send alert if within rate limit."""
        now = int(time.time())

        self._alerts_per_minute.append(now)
        recent_count = sum(1 for t in self._alerts_per_minute if now - t < 60)

        if recent_count >= self.rate_limit:
            logger.warning(f"Alert rate limited: {message[:50]}")
            return None

        alert = Alert(
            severity=severity,
            category=category,
            message=message,
            source_module=source_module,
            details=details or {},
            trace_id=trace_id
        )

        self._alert_history.append(alert)

        self._dispatch(alert)

        return alert

    def _dispatch(self, alert: Alert) -> None:
        """Dispatch alert to channels."""
        logger.log(
            self._severity_to_log_level(alert.severity),
            f"[{alert.category}] {alert.message}",
            extra={"alert_id": alert.alert_id, "details": alert.details}
        )

        if self.slack_webhook and alert.severity in (AlertSeverity.WARNING, AlertSeverity.CRITICAL):
            asyncio.create_task(self._send_slack(alert))

        if alert.severity == AlertSeverity.CRITICAL and self.email_recipients:
            asyncio.create_task(self._send_email(alert))

    async def _send_slack(self, alert: Alert) -> None:
        """Send alert to Slack."""
        try:
            import httpx
            payload = {
                "text": f"[{alert.severity.value.upper()}] {alert.message}",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*{alert.category}*\n{alert.message}"
                        }
                    }
                ]
            }
            async with httpx.AsyncClient() as client:
                await client.post(self.slack_webhook, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")

    async def _send_email(self, alert: Alert) -> None:
        """Send email alert."""
        pass

    def _severity_to_log_level(self, severity: AlertSeverity) -> int:
        """Convert severity to log level."""
        levels = {
            AlertSeverity.INFO: logging.INFO,
            AlertSeverity.WARNING: logging.WARNING,
            AlertSeverity.CRITICAL: logging.CRITICAL
        }
        return levels.get(severity, logging.INFO)

    def get_recent_alerts(
        self,
        severity: AlertSeverity = None,
        limit: int = 10
    ) -> list[Alert]:
        """Get recent alerts."""
        alerts = list(self._alert_history)
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        return alerts[-limit:]

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Acknowledge an alert."""
        for alert in self._alert_history:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                return True
        return False


class AlertThrottler:
    """Throttles duplicate alerts."""

    def __init__(self, cooldown_seconds: int = 60):
        self.cooldown_seconds = cooldown_seconds
        self._last_alert: dict[str, int] = {}

    def should_send(self, key: str) -> bool:
        """Check if alert should be sent."""
        now = int(time.time())
        last_time = self._last_alert.get(key, 0)

        if now - last_time < self.cooldown_seconds:
            return False

        self._last_alert[key] = now
        return True

    def reset(self, key: str = None) -> None:
        """Reset throttle state."""
        if key:
            if key in self._last_alert:
                del self._last_alert[key]
        else:
            self._last_alert.clear()
