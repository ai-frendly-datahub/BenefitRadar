from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from benefitradar.config_loader import load_notification_config
from benefitradar.models import Article
from benefitradar.notifier import BenefitNotifier, NotificationConfig, detect_benefit_notifications


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
    deadline = (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat()
    article = Article(
        title=f"청년 주거지원 신청 {deadline} 마감",
        link="https://example.com/benefit/1",
        summary="저소득 청년 대상",
        published=datetime.now(timezone.utc),
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
