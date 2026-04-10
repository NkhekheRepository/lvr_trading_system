"""
Alert system for notifications via Telegram, Slack, and Email.

Telegram Alert Routing:
    - CRITICAL: Telegram + Slack + Email + Log
    - WARNING:  Telegram + Slack + Log
    - INFO:     Log only

Setup:
    1. Message @BotFather on Telegram -> /newbot -> get token
    2. Add bot to group or message directly -> get chat_id
    3. Set telegram_bot_token and telegram_chat_id in config.yaml
"""

import asyncio
import logging
import time
from collections import deque
from typing import Optional

from app.schemas import Alert, AlertSeverity

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


class AlertManager:
    """
    Manages alert dispatch with rate limiting and Telegram integration.
    """

    def __init__(
        self,
        rate_limit_per_minute: int = 10,
        slack_webhook: str = None,
        email_recipients: list = None,
        telegram_bot_token: str = None,
        telegram_chat_id: str = None,
        telegram_enabled: bool = True,
    ):
        self.rate_limit = rate_limit_per_minute
        self.slack_webhook = slack_webhook
        self.email_recipients = email_recipients or []

        self.telegram_bot_token = telegram_bot_token
        self.telegram_chat_id = telegram_chat_id
        self.telegram_enabled = telegram_enabled and bool(telegram_bot_token) and bool(telegram_chat_id)

        self._alert_history: deque = deque(maxlen=1000)
        self._alerts_per_minute: deque = deque(maxlen=60)
        self._last_alert_time = 0

        if self.telegram_enabled:
            logger.info(f"Telegram alerts enabled for chat_id={self.telegram_chat_id}")
        else:
            logger.info("Telegram alerts disabled")

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
        """Dispatch alert to channels based on severity."""
        logger.log(
            self._severity_to_log_level(alert.severity),
            f"[{alert.category}] {alert.message}",
            extra={"alert_id": alert.alert_id, "details": alert.details}
        )

        if alert.severity in (AlertSeverity.WARNING, AlertSeverity.CRITICAL):
            if self.telegram_enabled:
                self._async_dispatch(self._send_telegram(alert))

            if self.slack_webhook:
                self._async_dispatch(self._send_slack(alert))

        if alert.severity == AlertSeverity.CRITICAL and self.email_recipients:
            self._async_dispatch(self._send_email(alert))

    def _async_dispatch(self, coro) -> None:
        """Safely dispatch async coroutine."""
        try:
            loop = asyncio.get_running_loop()
            asyncio.create_task(coro)
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(coro)
                else:
                    loop.run_until_complete(coro)
            except Exception:
                pass

    async def _send_telegram(self, alert: Alert) -> None:
        """Send alert to Telegram via Bot API."""
        if not self.telegram_bot_token or not self.telegram_chat_id:
            return

        try:
            import aiohttp

            emoji = self._severity_emoji(alert.severity)
            text = self._format_telegram_message(alert, emoji)

            url = f"{TELEGRAM_API_BASE}/bot{self.telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if not data.get("ok"):
                            logger.error(f"Telegram API error: {data.get('description', 'unknown')}")
                    else:
                        error_text = await resp.text()
                        logger.error(f"Telegram HTTP {resp.status}: {error_text[:200]}")

        except asyncio.TimeoutError:
            logger.error("Telegram send timeout")
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")

    def _format_telegram_message(self, alert: Alert, emoji: str) -> str:
        """Format alert as Telegram HTML message."""
        severity_label = alert.severity.value.upper()
        lines = [
            f"{emoji} <b>[{severity_label}] {alert.category}</b>",
            f"",
            f"{alert.message}",
        ]

        if alert.details:
            lines.append("")
            for key, value in alert.details.items():
                if isinstance(value, float):
                    lines.append(f"  <code>{key}:</code> {value:.6f}")
                else:
                    lines.append(f"  <code>{key}:</code> {value}")

        lines.append("")
        lines.append(f"<i>Source: {alert.source_module}</i>")

        if alert.trace_id:
            lines.append(f"<i>Trace: {alert.trace_id[:8]}</i>")

        return "\n".join(lines)

    def _severity_emoji(self, severity: AlertSeverity) -> str:
        """Get emoji for alert severity."""
        emojis = {
            AlertSeverity.INFO: "\u2139\ufe0f",
            AlertSeverity.WARNING: "\u26a0\ufe0f",
            AlertSeverity.CRITICAL: "\U0001f6a8",
        }
        return emojis.get(severity, "\u2753")

    async def _send_slack(self, alert: Alert) -> None:
        """Send alert to Slack."""
        try:
            import aiohttp

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
            async with aiohttp.ClientSession() as session:
                async with session.post(self.slack_webhook, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    pass
        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")

    async def _send_email(self, alert: Alert) -> None:
        """Send email alert."""
        pass

    async def send_trade_alert(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        order_id: str = "",
        fill_price: float = None,
        fee: float = None,
        slippage: float = None,
    ) -> Optional[Alert]:
        """Send a formatted trade execution alert to Telegram."""
        fill_price_str = f"{fill_price:.2f}" if fill_price else "N/A"
        fee_str = f"{fee:.4f}" if fee else "N/A"
        slippage_str = f"{slippage:.4f}" if slippage else "N/A"
        notional = quantity * (fill_price or price)

        message = (
            f"Order Filled: {side.upper()} {quantity} {symbol}\n"
            f"Price: {fill_price_str} | Notional: {notional:.2f} USDT\n"
            f"Fee: {fee_str} | Slippage: {slippage_str}"
        )

        details = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": fill_price or price,
            "notional": notional,
            "fee": fee or 0,
            "slippage": slippage or 0,
        }

        if order_id:
            details["order_id"] = order_id

        return self.send_alert(
            severity=AlertSeverity.WARNING,
            category="TRADE_FILL",
            message=message,
            source_module="execution",
            details=details,
        )

    async def send_signal_alert(
        self,
        symbol: str,
        direction: str,
        strength: float,
        confidence: float,
        edge: float,
    ) -> Optional[Alert]:
        """Send a formatted signal generation alert to Telegram."""
        message = (
            f"Signal Generated: {direction.upper()} {symbol}\n"
            f"Strength: {strength:.3f} | Confidence: {confidence:.3f}\n"
            f"Expected Edge: {edge:.5f}"
        )

        return self.send_alert(
            severity=AlertSeverity.INFO,
            category="SIGNAL",
            message=message,
            source_module="signal_generator",
            details={
                "symbol": symbol,
                "direction": direction,
                "strength": strength,
                "confidence": confidence,
                "edge": edge,
            },
        )

    async def send_rejection_alert(
        self,
        symbol: str,
        reason: str,
        error_code: str = "",
    ) -> Optional[Alert]:
        """Send a formatted order rejection alert to Telegram."""
        message = f"Order Rejected: {symbol}\nReason: {reason}"
        if error_code:
            message += f" | Code: {error_code}"

        return self.send_alert(
            severity=AlertSeverity.CRITICAL,
            category="ORDER_REJECTED",
            message=message,
            source_module="execution",
            details={
                "symbol": symbol,
                "reason": reason,
                "error_code": error_code,
            },
        )

    async def send_position_alert(
        self,
        symbol: str,
        quantity: float,
        entry_price: float,
        unrealized_pnl: float = None,
    ) -> Optional[Alert]:
        """Send a formatted position update alert to Telegram."""
        direction = "LONG" if quantity > 0 else "SHORT"
        message = (
            f"Position Update: {direction} {abs(quantity)} {symbol}\n"
            f"Entry: {entry_price:.2f}"
        )
        if unrealized_pnl is not None:
            pnl_emoji = "+" if unrealized_pnl >= 0 else ""
            message += f" | PnL: {pnl_emoji}{unrealized_pnl:.2f}"

        return self.send_alert(
            severity=AlertSeverity.INFO,
            category="POSITION",
            message=message,
            source_module="portfolio",
            details={
                "symbol": symbol,
                "quantity": quantity,
                "entry_price": entry_price,
                "unrealized_pnl": unrealized_pnl or 0,
            },
        )

    async def send_drawdown_alert(
        self,
        drawdown_pct: float,
        max_drawdown_pct: float,
        daily_pnl: float,
    ) -> Optional[Alert]:
        """Send a formatted drawdown alert to Telegram."""
        severity = AlertSeverity.CRITICAL if drawdown_pct > max_drawdown_pct * 0.8 else AlertSeverity.WARNING
        message = (
            f"Drawdown Alert: {drawdown_pct:.2%}\n"
            f"Max Allowed: {max_drawdown_pct:.2%} | Daily PnL: {daily_pnl:.2f}"
        )

        return self.send_alert(
            severity=severity,
            category="DRAWDOWN",
            message=message,
            source_module="risk",
            details={
                "drawdown_pct": drawdown_pct,
                "max_drawdown_pct": max_drawdown_pct,
                "daily_pnl": daily_pnl,
            },
        )

    async def send_system_status(
        self,
        status: str,
        mode: str,
        uptime_sec: float = None,
        total_trades: int = None,
        total_pnl: float = None,
    ) -> Optional[Alert]:
        """Send a formatted system status alert to Telegram."""
        message = f"System Status: {status}\nMode: {mode}"
        if uptime_sec is not None:
            hours = uptime_sec / 3600
            message += f" | Uptime: {hours:.1f}h"
        if total_trades is not None:
            message += f" | Trades: {total_trades}"
        if total_pnl is not None:
            message += f" | PnL: {total_pnl:.2f}"

        return self.send_alert(
            severity=AlertSeverity.INFO,
            category="SYSTEM",
            message=message,
            source_module="trading_system",
            details={
                "status": status,
                "mode": mode,
                "uptime_sec": uptime_sec or 0,
                "total_trades": total_trades or 0,
                "total_pnl": total_pnl or 0,
            },
        )

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