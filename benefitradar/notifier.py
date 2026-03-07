from __future__ import annotations

import re
import smtplib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.mime.text import MIMEText
from typing import Any

import requests

from .models import Article


@dataclass
class NotificationConfig:
    enabled: bool
    channels: list[str]
    email_settings: dict[str, Any] = field(default_factory=dict)
    webhook_url: str = ""
    telegram_config: dict[str, str] = field(default_factory=dict)
    rules: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotificationEvent:
    title: str
    message: str
    priority: str
    event_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Notifier:
    def __init__(self, config: NotificationConfig):
        self.config = config

    def send(
        self,
        title: str,
        message: str,
        priority: str = "normal",
        metadata: dict[str, Any] | None = None,
    ) -> None:
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

        msg = MIMEText(str(payload["message"]), "plain", "utf-8")
        msg["Subject"] = str(payload["title"])
        msg["From"] = from_address
        msg["To"] = ", ".join(str(addr) for addr in to_addresses)

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(msg)

    def _send_webhook(self, payload: dict[str, Any]) -> None:
        if not self.config.webhook_url:
            return
        requests.post(self.config.webhook_url, json=payload, timeout=10)

    def _send_telegram(self, payload: dict[str, Any]) -> None:
        token = self.config.telegram_config.get("bot_token", "")
        chat_id = self.config.telegram_config.get("chat_id", "")
        if not token or not chat_id:
            return

        text = f"[{payload['priority'].upper()}] {payload['title']}\n{payload['message']}"
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )


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

    now = datetime.now(timezone.utc).date()
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
        return datetime(int(year), int(month), int(day), tzinfo=timezone.utc).date()
    except ValueError:
        return None
