# Synced from Radar-Template/radar/notifier.py
# DO NOT MODIFY core Notifier classes - only domain-specific detect_benefit_notifications()

from __future__ import annotations

import re
import smtplib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.mime.text import MIMEText
from typing import Any, Protocol

import requests
import structlog

from .models import Article


logger = structlog.get_logger(__name__)


@dataclass
class NotificationPayload:
    """Payload for notification delivery (canonical from Template)."""

    category_name: str
    sources_count: int
    collected_count: int
    matched_count: int
    errors_count: int
    timestamp: datetime
    report_url: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Convert payload to dictionary for JSON serialization."""
        return {
            "category_name": self.category_name,
            "sources_count": self.sources_count,
            "collected_count": self.collected_count,
            "matched_count": self.matched_count,
            "errors_count": self.errors_count,
            "timestamp": self.timestamp.isoformat(),
            "report_url": self.report_url,
        }


@dataclass
class NotificationConfig:
    """Domain-specific configuration for BenefitRadar notifications."""

    enabled: bool
    channels: list[str]
    email_settings: dict[str, Any] = field(default_factory=dict)
    webhook_url: str = ""
    telegram_config: dict[str, str] = field(default_factory=dict)
    rules: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotificationEvent:
    """Domain-specific event for BenefitRadar notifications."""

    title: str
    message: str
    priority: str
    event_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Notifier(Protocol):
    """Protocol for notification delivery (canonical from Template)."""

    def send(self, payload: NotificationPayload) -> bool:
        """Send notification. Return True if successful, False otherwise."""
        ...


class EmailNotifier:
    """Send notifications via email using SMTP."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        from_addr: str,
        to_addrs: list[str],
    ) -> None:
        """Initialize email notifier.

        Args:
            smtp_host: SMTP server hostname
            smtp_port: SMTP server port
            smtp_user: SMTP username
            smtp_password: SMTP password
            from_addr: Sender email address
            to_addrs: List of recipient email addresses
        """
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.from_addr = from_addr
        self.to_addrs = to_addrs

    def send(self, payload: NotificationPayload) -> bool:
        """Send email notification.

        Args:
            payload: Notification payload

        Returns:
            True if successful, False otherwise
        """
        try:
            subject = f"Radar Pipeline Complete: {payload.category_name}"
            body = self._build_email_body(payload)

            msg = MIMEText(body, "plain")
            msg["Subject"] = subject
            msg["From"] = self.from_addr
            msg["To"] = ", ".join(self.to_addrs)

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            logger.info("email_notification_sent", category=payload.category_name)
            return True
        except Exception as e:
            logger.error(
                "email_notification_failed",
                category=payload.category_name,
                error=str(e),
            )
            return False

    def _build_email_body(self, payload: NotificationPayload) -> str:
        """Build email body from payload."""
        lines = [
            "Radar Pipeline Completion Report",
            "================================",
            "",
            f"Category: {payload.category_name}",
            f"Timestamp: {payload.timestamp.isoformat()}",
            "",
            "Statistics:",
            f"  Sources: {payload.sources_count}",
            f"  Collected: {payload.collected_count}",
            f"  Matched: {payload.matched_count}",
            f"  Errors: {payload.errors_count}",
        ]
        if payload.report_url:
            lines.append("")
            lines.append(f"Report: {payload.report_url}")
        return "\n".join(lines)


class WebhookNotifier:
    """Send notifications via HTTP webhook."""

    def __init__(
        self,
        url: str,
        method: str = "POST",
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize webhook notifier.

        Args:
            url: Webhook URL
            method: HTTP method (POST or GET)
            headers: Optional HTTP headers
        """
        self.url = url
        self.method = method.upper()
        self.headers = headers or {}

    def send(self, payload: NotificationPayload) -> bool:
        """Send webhook notification.

        Args:
            payload: Notification payload

        Returns:
            True if successful, False otherwise
        """
        try:
            if self.method == "POST":
                response = requests.post(
                    self.url,
                    json=payload.to_dict(),
                    headers=self.headers,
                    timeout=10,
                )
            elif self.method == "GET":
                response = requests.get(
                    self.url,
                    headers=self.headers,
                    timeout=10,
                )
            else:
                logger.error(
                    "webhook_invalid_method",
                    method=self.method,
                    url=self.url,
                )
                return False

            if response.status_code >= 400:
                logger.error(
                    "webhook_notification_failed",
                    url=self.url,
                    status_code=response.status_code,
                )
                return False

            logger.info("webhook_notification_sent", url=self.url)
            return True
        except Exception as e:
            logger.error(
                "webhook_notification_failed",
                url=self.url,
                error=str(e),
            )
            return False


class CompositeNotifier:
    """Send notifications to multiple notifiers."""

    def __init__(self, notifiers: list[object]) -> None:
        """Initialize composite notifier.

        Args:
            notifiers: List of notifiers to send to
        """
        self.notifiers = notifiers

    def send(self, payload: NotificationPayload) -> bool:
        """Send notification to all notifiers.

        Args:
            payload: Notification payload

        Returns:
            True if all notifiers succeeded, False if any failed
        """
        if not self.notifiers:
            return True

        results = []
        for notifier in self.notifiers:
            try:
                result = notifier.send(payload)
                results.append(result)
            except Exception:
                results.append(False)
        return all(results) if results else True


class BenefitNotifier:
    """Domain-specific notifier for BenefitRadar using NotificationConfig."""

    def __init__(self, config: NotificationConfig):
        self.config = config

    def send_event(
        self,
        title: str,
        message: str,
        priority: str = "normal",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send domain-specific notification event.

        Args:
            title: Notification title
            message: Notification message
            priority: Priority level (normal, high)
            metadata: Additional metadata
        """
        if not self.config.enabled:
            return

        payload = {
            "title": title,
            "message": message,
            "priority": priority,
            "metadata": metadata or {},
        }
        channels = {channel.strip().lower() for channel in self.config.channels}

        if "email" in channels:
            self._send_email(payload)
        if "webhook" in channels:
            self._send_webhook(payload)
        if "telegram" in channels:
            self._send_telegram(payload)

    def _send_email(self, payload: dict[str, Any]) -> None:
        settings = self.config.email_settings
        smtp_host = str(settings.get("smtp_host", "")).strip()
        smtp_port = int(settings.get("smtp_port", 587) or 587)
        from_address = str(settings.get("from_address", "")).strip()
        to_addresses = settings.get("to_addresses", [])
        username = str(settings.get("username", "")).strip()
        password = str(settings.get("password", "")).strip()

        if (
            not smtp_host
            or not from_address
            or not isinstance(to_addresses, list)
            or not to_addresses
        ):
            return

        try:
            msg = MIMEText(str(payload["message"]), "plain", "utf-8")
            msg["Subject"] = str(payload["title"])
            msg["From"] = from_address
            msg["To"] = ", ".join(str(addr) for addr in to_addresses)

            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                if username and password:
                    server.login(username, password)
                server.send_message(msg)
            logger.info("benefit_email_sent", title=payload["title"])
        except Exception as e:
            logger.error("benefit_email_failed", error=str(e))

    def _send_webhook(self, payload: dict[str, Any]) -> None:
        if not self.config.webhook_url:
            return
        try:
            requests.post(self.config.webhook_url, json=payload, timeout=10)
            logger.info("benefit_webhook_sent", url=self.config.webhook_url)
        except Exception as e:
            logger.error("benefit_webhook_failed", error=str(e))

    def _send_telegram(self, payload: dict[str, Any]) -> None:
        token = self.config.telegram_config.get("bot_token", "")
        chat_id = self.config.telegram_config.get("chat_id", "")
        if not token or not chat_id:
            return

        try:
            text = f"[{payload['priority'].upper()}] {payload['title']}\n{payload['message']}"
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=10,
            )
            logger.info("benefit_telegram_sent", chat_id=chat_id)
        except Exception as e:
            logger.error("benefit_telegram_failed", error=str(e))


def detect_benefit_notifications(
    articles: list[Article],
    *,
    known_links: set[str],
    rules: dict[str, Any],
) -> list[NotificationEvent]:
    events: list[NotificationEvent] = []

    deadline_days = int(rules.get("deadline_days", 7))
    condition_keywords = [
        str(keyword).strip().lower()
        for keyword in rules.get("condition_keywords", [])
        if str(keyword).strip()
    ]

    now = datetime.now(UTC).date()
    for article in articles:
        if article.link and article.link not in known_links:
            events.append(
                NotificationEvent(
                    title=f"[BenefitRadar] 신규 지원금 정보: {article.title}",
                    message=f"새로운 복지 정보가 등록되었습니다.\n링크: {article.link}",
                    priority="high",
                    event_type="new_benefit",
                    metadata={"link": article.link},
                )
            )

        deadline = _extract_date(article.title) or _extract_date(article.summary)
        if deadline is not None:
            remaining_days = (deadline - now).days
            if 0 <= remaining_days <= deadline_days:
                events.append(
                    NotificationEvent(
                        title=f"[BenefitRadar] 마감 임박: {article.title}",
                        message=f"신청 마감까지 {remaining_days}일 남았습니다.\n링크: {article.link}",
                        priority="high" if remaining_days <= 3 else "normal",
                        event_type="deadline_soon",
                        metadata={"link": article.link, "remaining_days": remaining_days},
                    )
                )

        if condition_keywords:
            haystack = f"{article.title}\n{article.summary}".lower()
            matched = [keyword for keyword in condition_keywords if keyword in haystack]
            if matched:
                events.append(
                    NotificationEvent(
                        title=f"[BenefitRadar] 조건 매칭 혜택: {article.title}",
                        message=(
                            f"설정한 조건 키워드와 일치합니다: {', '.join(matched)}\n"
                            f"링크: {article.link}"
                        ),
                        priority="normal",
                        event_type="condition_match",
                        metadata={"link": article.link, "matched_keywords": matched},
                    )
                )

    return events


def _extract_date(text: str) -> date | None:
    pattern = re.compile(r"(20\d{2})[.-](\d{1,2})[.-](\d{1,2})")
    match = pattern.search(text)
    if not match:
        return None

    year, month, day = match.groups()
    try:
        return datetime(int(year), int(month), int(day), tzinfo=UTC).date()
    except ValueError:
        return None
