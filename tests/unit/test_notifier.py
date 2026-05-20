from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from benefitradar.config_loader import load_notification_config
from benefitradar.models import Article
from benefitradar.notifier import (
    BenefitNotifier,
    CompositeNotifier,
    EmailNotifier,
    NotificationConfig,
    NotificationPayload,
    WebhookNotifier,
    _extract_date,
    detect_benefit_notifications,
)


@pytest.mark.unit
def test_notifier_sends_webhook_channel() -> None:
    notifier = BenefitNotifier(
        NotificationConfig(
            enabled=True,
            channels=["webhook"],
            webhook_url="https://hooks.example",
        )
    )

    with patch("benefitradar.notifier.requests.post") as mock_post:
        notifier.send_event("title", "message", "high")

    mock_post.assert_called_once()


@pytest.mark.unit
def test_notifier_sends_email_channel() -> None:
    notifier = BenefitNotifier(
        NotificationConfig(
            enabled=True,
            channels=["email"],
            email_settings={
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "from_address": "from@example.com",
                "to_addresses": ["to@example.com"],
            },
        )
    )

    with patch("benefitradar.notifier.smtplib.SMTP") as mock_smtp:
        notifier.send_event("title", "message", "normal")

    mock_smtp.assert_called_once()


@pytest.mark.unit
def test_notifier_sends_telegram_channel() -> None:
    notifier = BenefitNotifier(
        NotificationConfig(
            enabled=True,
            channels=["telegram"],
            telegram_config={"bot_token": "token", "chat_id": "chat"},
        )
    )

    with patch("benefitradar.notifier.requests.post") as mock_post:
        notifier.send_event("title", "message", "high")

    mock_post.assert_called_once()


@pytest.mark.unit
def test_notification_payload_and_email_notifier_success() -> None:
    payload = NotificationPayload(
        category_name="benefit",
        sources_count=2,
        collected_count=5,
        matched_count=3,
        errors_count=1,
        timestamp=datetime(2026, 5, 21, 12, 0, tzinfo=UTC),
        report_url="https://example.com/report.html",
    )
    notifier = EmailNotifier(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="password",
        from_addr="from@example.com",
        to_addrs=["to@example.com"],
    )

    with patch("benefitradar.notifier.smtplib.SMTP") as mock_smtp:
        smtp_server = mock_smtp.return_value.__enter__.return_value
        sent = notifier.send(payload)

    assert sent is True
    assert payload.to_dict()["timestamp"] == "2026-05-21T12:00:00+00:00"
    mock_smtp.assert_called_once_with("smtp.example.com", 587)
    smtp_server.starttls.assert_called_once_with()
    smtp_server.login.assert_called_once_with("user", "password")
    message = smtp_server.send_message.call_args.args[0]
    assert message["Subject"] == "Radar Pipeline Complete: benefit"
    assert "Report: https://example.com/report.html" in notifier._build_email_body(payload)


@pytest.mark.unit
def test_email_notifier_returns_false_on_smtp_error() -> None:
    payload = NotificationPayload(
        category_name="benefit",
        sources_count=1,
        collected_count=1,
        matched_count=1,
        errors_count=0,
        timestamp=datetime.now(UTC),
    )
    notifier = EmailNotifier(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="password",
        from_addr="from@example.com",
        to_addrs=["to@example.com"],
    )

    with patch("benefitradar.notifier.smtplib.SMTP", side_effect=OSError("smtp down")):
        sent = notifier.send(payload)

    assert sent is False


@pytest.mark.unit
def test_webhook_notifier_handles_methods_status_and_exceptions() -> None:
    payload = NotificationPayload(
        category_name="benefit",
        sources_count=1,
        collected_count=2,
        matched_count=1,
        errors_count=0,
        timestamp=datetime.now(UTC),
    )

    with patch(
        "benefitradar.notifier.requests.post",
        return_value=SimpleNamespace(status_code=204),
    ) as mock_post:
        assert WebhookNotifier("https://hooks.example", headers={"X-Test": "1"}).send(payload)
    mock_post.assert_called_once_with(
        "https://hooks.example",
        json=payload.to_dict(),
        headers={"X-Test": "1"},
        timeout=10,
    )

    with patch(
        "benefitradar.notifier.requests.get",
        return_value=SimpleNamespace(status_code=200),
    ) as mock_get:
        assert WebhookNotifier("https://hooks.example", method="get").send(payload)
    mock_get.assert_called_once_with("https://hooks.example", headers={}, timeout=10)

    with patch(
        "benefitradar.notifier.requests.post",
        return_value=SimpleNamespace(status_code=500),
    ):
        assert not WebhookNotifier("https://hooks.example").send(payload)

    assert not WebhookNotifier("https://hooks.example", method="PATCH").send(payload)

    with patch("benefitradar.notifier.requests.get", side_effect=RuntimeError("network")):
        assert not WebhookNotifier("https://hooks.example", method="GET").send(payload)


class _StubNotifier:
    def __init__(self, result: bool = True, *, raises: bool = False) -> None:
        self.result = result
        self.raises = raises
        self.calls = 0

    def send(self, payload: NotificationPayload) -> bool:
        self.calls += 1
        if self.raises:
            raise RuntimeError("send failed")
        return self.result


@pytest.mark.unit
def test_composite_notifier_aggregates_success_failure_and_empty() -> None:
    payload = NotificationPayload(
        category_name="benefit",
        sources_count=1,
        collected_count=1,
        matched_count=1,
        errors_count=0,
        timestamp=datetime.now(UTC),
    )
    successful = _StubNotifier()
    failed = _StubNotifier(False)
    broken = _StubNotifier(raises=True)

    assert CompositeNotifier([]).send(payload) is True
    assert CompositeNotifier([successful]).send(payload) is True
    assert CompositeNotifier([successful, failed]).send(payload) is False
    assert CompositeNotifier([broken]).send(payload) is False
    assert successful.calls == 2
    assert failed.calls == 1
    assert broken.calls == 1


@pytest.mark.unit
def test_benefit_notifier_skips_disabled_and_incomplete_channel_settings() -> None:
    disabled = BenefitNotifier(
        NotificationConfig(enabled=False, channels=["email", "webhook", "telegram"])
    )
    with (
        patch.object(BenefitNotifier, "_send_email") as mock_email,
        patch.object(BenefitNotifier, "_send_webhook") as mock_webhook,
        patch.object(BenefitNotifier, "_send_telegram") as mock_telegram,
    ):
        disabled.send_event("title", "message")
    mock_email.assert_not_called()
    mock_webhook.assert_not_called()
    mock_telegram.assert_not_called()

    incomplete = BenefitNotifier(
        NotificationConfig(
            enabled=True,
            channels=[" email ", "webhook", "telegram"],
            email_settings={},
            webhook_url="",
            telegram_config={},
        )
    )
    with (
        patch("benefitradar.notifier.smtplib.SMTP") as mock_smtp,
        patch("benefitradar.notifier.requests.post") as mock_post,
    ):
        incomplete.send_event("title", "message")
    mock_smtp.assert_not_called()
    mock_post.assert_not_called()


@pytest.mark.unit
def test_benefit_notifier_channel_error_paths_do_not_raise() -> None:
    email_notifier = BenefitNotifier(
        NotificationConfig(
            enabled=True,
            channels=["email"],
            email_settings={
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "username": "user",
                "password": "password",
                "from_address": "from@example.com",
                "to_addresses": ["to@example.com"],
            },
        )
    )
    with patch("benefitradar.notifier.smtplib.SMTP") as mock_smtp:
        smtp_server = mock_smtp.return_value.__enter__.return_value
        email_notifier.send_event("title", "message")
    smtp_server.login.assert_called_once_with("user", "password")

    with patch("benefitradar.notifier.smtplib.SMTP", side_effect=OSError("smtp down")):
        email_notifier.send_event("title", "message")

    network_notifier = BenefitNotifier(
        NotificationConfig(
            enabled=True,
            channels=["webhook", "telegram"],
            webhook_url="https://hooks.example",
            telegram_config={"bot_token": "token", "chat_id": "chat"},
        )
    )
    with patch("benefitradar.notifier.requests.post", side_effect=RuntimeError("network")):
        network_notifier.send_event("title", "message", "high")


@pytest.mark.unit
def test_load_notification_config_resolves_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example")
    config_file = tmp_path / "notifications.yaml"
    _ = config_file.write_text(
        """
notifications:
  enabled: true
  channels: [webhook]
  webhook_url: "${WEBHOOK_URL}"
""".strip(),
        encoding="utf-8",
    )

    config = load_notification_config(config_file)
    assert config.enabled is True
    assert config.webhook_url == "https://hooks.example"


@pytest.mark.unit
def test_detect_benefit_notifications_priority_and_types() -> None:
    deadline = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    article = Article(
        title=f"청년 주거지원 신청 {deadline} 마감",
        link="https://example.com/benefit/1",
        summary="저소득 청년 대상",
        published=datetime.now(UTC),
        source="test",
        category="benefit",
    )

    events = detect_benefit_notifications(
        [article],
        known_links=set(),
        rules={"deadline_days": 7, "condition_keywords": ["청년", "저소득"]},
    )

    event_types = {event.event_type for event in events}
    assert "new_benefit" in event_types
    assert "deadline_soon" in event_types
    assert "condition_match" in event_types
    assert any(event.priority == "high" for event in events)


@pytest.mark.unit
def test_detect_benefit_notifications_handles_known_links_and_normal_deadline() -> None:
    deadline = (datetime.now(UTC) + timedelta(days=5)).date().isoformat()
    article = Article(
        title=f"청년 주거지원 신청 {deadline} 마감",
        link="https://example.com/benefit/known",
        summary="신청 가능",
        published=datetime.now(UTC),
        source="test",
        category="benefit",
    )

    events = detect_benefit_notifications(
        [article],
        known_links={article.link},
        rules={"deadline_days": 7, "condition_keywords": []},
    )

    assert [event.event_type for event in events] == ["deadline_soon"]
    assert events[0].priority == "normal"


@pytest.mark.unit
def test_extract_date_handles_missing_and_invalid_dates() -> None:
    assert _extract_date("마감 2026.05.21").isoformat() == "2026-05-21"
    assert _extract_date("마감일 없음") is None
    assert _extract_date("마감 2026-02-30") is None
