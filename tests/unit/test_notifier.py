from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from benefitradar.notifier import (
    CompositeNotifier,
    EmailNotifier,
    NotificationPayload,
    WebhookNotifier,
)


class TestNotificationPayload:
    """Test NotificationPayload structure and serialization."""

    def test_payload_creation(self) -> None:
        """Test creating a notification payload."""
        now = datetime.now(timezone.utc)
        payload = NotificationPayload(
            category_name="test_category",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=2,
            timestamp=now,
            report_url="http://example.com/report.html",
        )

        assert payload.category_name == "test_category"
        assert payload.sources_count == 5
        assert payload.collected_count == 100
        assert payload.matched_count == 25
        assert payload.errors_count == 2
        assert payload.timestamp == now
        assert payload.report_url == "http://example.com/report.html"

    def test_payload_to_dict(self) -> None:
        """Test converting payload to dictionary."""
        now = datetime.now(timezone.utc)
        payload = NotificationPayload(
            category_name="test_category",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=2,
            timestamp=now,
            report_url="http://example.com/report.html",
        )

        data = payload.to_dict()
        assert data["category_name"] == "test_category"
        assert data["sources_count"] == 5
        assert data["collected_count"] == 100
        assert data["matched_count"] == 25
        assert data["errors_count"] == 2
        assert data["timestamp"] == now.isoformat()
        assert data["report_url"] == "http://example.com/report.html"


class TestEmailNotifier:
    """Test EmailNotifier with mocked smtplib."""

    def test_send_email_success(self) -> None:
        """Test successful email sending."""
        notifier = EmailNotifier(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="user@example.com",
            smtp_password="password",
            from_addr="sender@example.com",
            to_addrs=["recipient@example.com"],
        )

        payload = NotificationPayload(
            category_name="test",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=0,
            timestamp=datetime.now(timezone.utc),
            report_url="http://example.com/report.html",
        )

        with patch("smtplib.SMTP") as mock_smtp:
            mock_instance = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_instance

            result = notifier.send(payload)

            assert result is True
            mock_instance.starttls.assert_called_once()
            mock_instance.login.assert_called_once_with("user@example.com", "password")
            mock_instance.send_message.assert_called_once()

    def test_send_email_failure_smtp_error(self) -> None:
        """Test email sending with SMTP error."""
        notifier = EmailNotifier(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="user@example.com",
            smtp_password="password",
            from_addr="sender@example.com",
            to_addrs=["recipient@example.com"],
        )

        payload = NotificationPayload(
            category_name="test",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=0,
            timestamp=datetime.now(timezone.utc),
            report_url="http://example.com/report.html",
        )

        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.side_effect = Exception("SMTP connection failed")

            result = notifier.send(payload)

            assert result is False

    def test_send_email_with_errors(self) -> None:
        """Test email payload includes error count."""
        notifier = EmailNotifier(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="user@example.com",
            smtp_password="password",
            from_addr="sender@example.com",
            to_addrs=["recipient@example.com"],
        )

        payload = NotificationPayload(
            category_name="test",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=5,
            timestamp=datetime.now(timezone.utc),
            report_url="http://example.com/report.html",
        )

        with patch("smtplib.SMTP") as mock_smtp:
            mock_instance = MagicMock()
            mock_smtp.return_value.__enter__.return_value = mock_instance

            result = notifier.send(payload)

            assert result is True
            # Verify the message was sent
            mock_instance.send_message.assert_called_once()


class TestWebhookNotifier:
    """Test WebhookNotifier with mocked requests."""

    def test_send_webhook_post_success(self) -> None:
        """Test successful webhook POST."""
        notifier = WebhookNotifier(
            url="http://example.com/webhook",
            method="POST",
            headers={"Authorization": "Bearer token"},
        )

        payload = NotificationPayload(
            category_name="test",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=0,
            timestamp=datetime.now(timezone.utc),
            report_url="http://example.com/report.html",
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = notifier.send(payload)

            assert result is True
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "http://example.com/webhook"
            assert call_args[1]["json"] == payload.to_dict()
            assert call_args[1]["headers"] == {"Authorization": "Bearer token"}

    def test_send_webhook_get_success(self) -> None:
        """Test successful webhook GET."""
        notifier = WebhookNotifier(
            url="http://example.com/webhook",
            method="GET",
            headers={},
        )

        payload = NotificationPayload(
            category_name="test",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=0,
            timestamp=datetime.now(timezone.utc),
            report_url="http://example.com/report.html",
        )

        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = notifier.send(payload)

            assert result is True
            mock_get.assert_called_once()

    def test_send_webhook_failure_http_error(self) -> None:
        """Test webhook sending with HTTP error."""
        notifier = WebhookNotifier(
            url="http://example.com/webhook",
            method="POST",
            headers={},
        )

        payload = NotificationPayload(
            category_name="test",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=0,
            timestamp=datetime.now(timezone.utc),
            report_url="http://example.com/report.html",
        )

        with patch("requests.post") as mock_post:
            mock_post.side_effect = Exception("Connection failed")

            result = notifier.send(payload)

            assert result is False

    def test_send_webhook_failure_bad_status(self) -> None:
        """Test webhook sending with bad HTTP status."""
        notifier = WebhookNotifier(
            url="http://example.com/webhook",
            method="POST",
            headers={},
        )

        payload = NotificationPayload(
            category_name="test",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=0,
            timestamp=datetime.now(timezone.utc),
            report_url="http://example.com/report.html",
        )

        with patch("requests.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 500
            mock_post.return_value = mock_response

            result = notifier.send(payload)

            assert result is False


class TestCompositeNotifier:
    """Test CompositeNotifier with multiple notifiers."""

    def test_send_all_notifiers_success(self) -> None:
        """Test sending to multiple notifiers all succeed."""
        email_notifier = Mock()
        email_notifier.send.return_value = True

        webhook_notifier = Mock()
        webhook_notifier.send.return_value = True

        composite = CompositeNotifier([email_notifier, webhook_notifier])

        payload = NotificationPayload(
            category_name="test",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=0,
            timestamp=datetime.now(timezone.utc),
            report_url="http://example.com/report.html",
        )

        result = composite.send(payload)

        assert result is True
        email_notifier.send.assert_called_once_with(payload)
        webhook_notifier.send.assert_called_once_with(payload)

    def test_send_partial_failure(self) -> None:
        """Test sending with one notifier failing."""
        email_notifier = Mock()
        email_notifier.send.return_value = False

        webhook_notifier = Mock()
        webhook_notifier.send.return_value = True

        composite = CompositeNotifier([email_notifier, webhook_notifier])

        payload = NotificationPayload(
            category_name="test",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=0,
            timestamp=datetime.now(timezone.utc),
            report_url="http://example.com/report.html",
        )

        result = composite.send(payload)

        # Should return False if any notifier fails
        assert result is False
        email_notifier.send.assert_called_once_with(payload)
        webhook_notifier.send.assert_called_once_with(payload)

    def test_send_all_notifiers_fail(self) -> None:
        """Test sending with all notifiers failing."""
        email_notifier = Mock()
        email_notifier.send.return_value = False

        webhook_notifier = Mock()
        webhook_notifier.send.return_value = False

        composite = CompositeNotifier([email_notifier, webhook_notifier])

        payload = NotificationPayload(
            category_name="test",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=0,
            timestamp=datetime.now(timezone.utc),
            report_url="http://example.com/report.html",
        )

        result = composite.send(payload)

        assert result is False

    def test_send_empty_notifiers(self) -> None:
        """Test composite with no notifiers."""
        composite = CompositeNotifier([])

        payload = NotificationPayload(
            category_name="test",
            sources_count=5,
            collected_count=100,
            matched_count=25,
            errors_count=0,
            timestamp=datetime.now(timezone.utc),
            report_url="http://example.com/report.html",
        )

        result = composite.send(payload)

        # Empty composite should return True (no-op success)
        assert result is True
