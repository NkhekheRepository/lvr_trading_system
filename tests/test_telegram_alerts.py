"""
Tests for Telegram alert integration.
"""

import asyncio
import pytest

from app.schemas import AlertSeverity
from monitoring.alerts import AlertManager, AlertThrottler


class TestTelegramAlertManager:
    """Test AlertManager with Telegram support."""

    @pytest.fixture
    def manager_no_telegram(self):
        return AlertManager(rate_limit_per_minute=100)

    @pytest.fixture
    def manager_with_telegram(self):
        return AlertManager(
            rate_limit_per_minute=100,
            telegram_bot_token="000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            telegram_chat_id="123456789",
            telegram_enabled=True,
        )

    def test_telegram_disabled_by_default(self, manager_no_telegram):
        assert manager_no_telegram.telegram_enabled is False

    def test_telegram_enabled_with_config(self, manager_with_telegram):
        assert manager_with_telegram.telegram_enabled is True

    def test_telegram_disabled_when_token_missing(self):
        am = AlertManager(telegram_bot_token=None, telegram_chat_id="123")
        assert am.telegram_enabled is False

    def test_telegram_disabled_when_chat_id_missing(self):
        am = AlertManager(telegram_bot_token="abc", telegram_chat_id=None)
        assert am.telegram_enabled is False

    def test_send_alert_no_telegram(self, manager_no_telegram):
        alert = manager_no_telegram.send_alert(
            severity=AlertSeverity.WARNING,
            category="TEST",
            message="Test message",
            source_module="test",
        )
        assert alert is not None
        assert alert.severity == AlertSeverity.WARNING

    def test_send_critical_alert(self, manager_no_telegram):
        alert = manager_no_telegram.send_alert(
            severity=AlertSeverity.CRITICAL,
            category="TRADE_FILL",
            message="Order filled",
            source_module="execution",
            details={"symbol": "BTCUSDT", "price": 50000},
        )
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL

    def test_rate_limiting(self, manager_no_telegram):
        manager_no_telegram.rate_limit = 10
        sent = 0
        for i in range(50):
            a = manager_no_telegram.send_alert(
                severity=AlertSeverity.INFO,
                category="RATE_TEST",
                message=f"Message {i}",
                source_module="test",
            )
            if a:
                sent += 1
        assert sent < 50
        assert sent > 0

    def test_trade_alert(self, manager_no_telegram):
        alert = asyncio.run(manager_no_telegram.send_trade_alert(
            symbol="BTCUSDT",
            side="buy",
            quantity=0.002,
            price=50000.0,
            fill_price=50001.0,
            fee=0.02,
            slippage=0.0001,
        ))
        assert alert is not None
        assert alert.category == "TRADE_FILL"

    def test_signal_alert(self, manager_no_telegram):
        alert = asyncio.run(manager_no_telegram.send_signal_alert(
            symbol="ETHUSDT",
            direction="sell",
            strength=0.75,
            confidence=0.6,
            edge=0.003,
        ))
        assert alert is not None
        assert alert.category == "SIGNAL"

    def test_rejection_alert(self, manager_no_telegram):
        alert = asyncio.run(manager_no_telegram.send_rejection_alert(
            symbol="BTCUSDT",
            reason="Insufficient margin",
            error_code="2019",
        ))
        assert alert is not None
        assert alert.category == "ORDER_REJECTED"
        assert alert.severity == AlertSeverity.CRITICAL

    def test_drawdown_alert(self, manager_no_telegram):
        alert = asyncio.run(manager_no_telegram.send_drawdown_alert(
            drawdown_pct=0.06,
            max_drawdown_pct=0.10,
            daily_pnl=-500.0,
        ))
        assert alert is not None
        assert alert.category == "DRAWDOWN"

    def test_drawdown_alert_critical_threshold(self, manager_no_telegram):
        alert = asyncio.run(manager_no_telegram.send_drawdown_alert(
            drawdown_pct=0.09,
            max_drawdown_pct=0.10,
            daily_pnl=-2000.0,
        ))
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL

    def test_system_status_alert(self, manager_no_telegram):
        alert = asyncio.run(manager_no_telegram.send_system_status(
            status="RUNNING",
            mode="TESTNET",
            uptime_sec=3600,
            total_trades=42,
            total_pnl=1500.0,
        ))
        assert alert is not None
        assert alert.category == "SYSTEM"

    def test_position_alert(self, manager_no_telegram):
        alert = asyncio.run(manager_no_telegram.send_position_alert(
            symbol="BTCUSDT",
            quantity=0.002,
            entry_price=50000.0,
            unrealized_pnl=25.0,
        ))
        assert alert is not None
        assert alert.category == "POSITION"

    def test_telegram_message_format(self, manager_no_telegram):
        alert = manager_no_telegram.send_alert(
            severity=AlertSeverity.CRITICAL,
            category="TRADE_FILL",
            message="Order filled at 50000",
            source_module="execution",
            details={"symbol": "BTCUSDT", "price": 50000.0},
        )
        msg = manager_no_telegram._format_telegram_message(
            alert, manager_no_telegram._severity_emoji(alert.severity)
        )
        assert "<b>[CRITICAL]" in msg
        assert "TRADE_FILL" in msg
        assert "Order filled at 50000" in msg
        assert "BTCUSDT" in msg
        assert "<code>symbol:</code>" in msg
        assert "<i>Source: execution</i>" in msg

    def test_severity_emoji(self, manager_no_telegram):
        assert manager_no_telegram._severity_emoji(AlertSeverity.INFO) is not None
        assert manager_no_telegram._severity_emoji(AlertSeverity.WARNING) is not None
        assert manager_no_telegram._severity_emoji(AlertSeverity.CRITICAL) is not None

    def test_alert_history(self, manager_no_telegram):
        manager_no_telegram.send_alert(
            severity=AlertSeverity.INFO, category="H1",
            message="msg1", source_module="test",
        )
        manager_no_telegram.send_alert(
            severity=AlertSeverity.WARNING, category="H2",
            message="msg2", source_module="test",
        )
        recent = manager_no_telegram.get_recent_alerts(limit=2)
        assert len(recent) == 2

    def test_acknowledge_alert(self, manager_no_telegram):
        alert = manager_no_telegram.send_alert(
            severity=AlertSeverity.INFO, category="ACK",
            message="ack test", source_module="test",
        )
        assert manager_no_telegram.acknowledge_alert(alert.alert_id) is True
        assert alert.acknowledged is True

    def test_acknowledge_nonexistent_alert(self, manager_no_telegram):
        assert manager_no_telegram.acknowledge_alert("nonexistent") is False

    def test_telegram_api_call_format(self, manager_with_telegram):
        async def _test():
            manager_with_telegram.send_alert(
                severity=AlertSeverity.WARNING,
                category="TEST",
                message="API format test",
                source_module="test",
            )
            await asyncio.sleep(0.5)

        asyncio.run(_test())


class TestAlertThrottler:
    """Test AlertThrottler."""

    def test_first_send_allowed(self):
        throttler = AlertThrottler(cooldown_seconds=60)
        assert throttler.should_send("key1") is True

    def test_duplicate_blocked(self):
        throttler = AlertThrottler(cooldown_seconds=60)
        throttler.should_send("key1")
        assert throttler.should_send("key1") is False

    def test_different_key_allowed(self):
        throttler = AlertThrottler(cooldown_seconds=60)
        throttler.should_send("key1")
        assert throttler.should_send("key2") is True

    def test_reset_key(self):
        throttler = AlertThrottler(cooldown_seconds=60)
        throttler.should_send("key1")
        throttler.reset("key1")
        assert throttler.should_send("key1") is True

    def test_reset_all(self):
        throttler = AlertThrottler(cooldown_seconds=60)
        throttler.should_send("key1")
        throttler.should_send("key2")
        throttler.reset()
        assert throttler.should_send("key1") is True
        assert throttler.should_send("key2") is True