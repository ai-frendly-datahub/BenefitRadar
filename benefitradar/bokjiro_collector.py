from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import List
from xml.etree import ElementTree

import requests

from .models import Article, Source


def collect_bokjiro(
    source: Source,
    *,
    category: str,
    limit: int = 30,
    timeout: int = 15,
) -> List[Article]:
    """Collect government subsidy program data from 보조금24 (bokjiro.go.kr).

    Requires BOKJIRO_API_KEY environment variable.
    Returns empty list gracefully if API key is not set.
    """
    api_key = os.environ.get("BOKJIRO_API_KEY", "")
    if not api_key:
        # Return empty list gracefully; not an error, just unconfigured.
        return []

    base_url = "http://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations"
    endpoint = f"{base_url}/LcgvWelfarelist"

    params = {
        "serviceKey": api_key,
        "pageNo": "1",
        "numOfRows": str(min(limit, 100)),
    }

    response = requests.get(endpoint, params=params, timeout=timeout)
    response.raise_for_status()

    return _parse_bokjiro_xml(response.content, source_name=source.name, category=category)


def _parse_bokjiro_xml(
    content: bytes,
    *,
    source_name: str,
    category: str,
) -> List[Article]:
    """Parse 보조금24 XML response into Article objects."""
    articles: List[Article] = []

    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return articles

    items = root.findall(".//servList") or root.findall(".//item")
    if not items:
        items = root.findall("./body/items/item")

    for item in items:
        title = _text(item, "servNm") or _text(item, "wlfareInfoNm") or _text(item, "title") or ""
        summary_parts = []

        target = _text(item, "servDgst") or _text(item, "lifeNmArray") or ""
        if target:
            summary_parts.append(target)

        amount = _text(item, "sprtCycNm") or _text(item, "sprtAmt") or ""
        if amount:
            summary_parts.append(f"지원: {amount}")

        dept = _text(item, "jurMnofNm") or _text(item, "charger") or ""
        if dept:
            summary_parts.append(f"담당: {dept}")

        link = _text(item, "servDtlLink") or _text(item, "infoUrl") or ""
        if not link:
            serv_id = _text(item, "servId") or ""
            if serv_id:
                link = f"https://www.bokjiro.go.kr/ssis-teu/twataa/wlfareInfo/moveTWAT52011M.do?wlfareInfoId={serv_id}"

        summary = " | ".join(summary_parts) if summary_parts else title

        if title.strip():
            articles.append(
                Article(
                    title=title.strip(),
                    link=link.strip() if link else "",
                    summary=summary.strip(),
                    published=datetime.now(timezone.utc),
                    source=source_name,
                    category=category,
                )
            )

    return articles


def _text(element: ElementTree.Element, tag: str) -> str:
    """Safely extract text from an XML element's child tag."""
    child = element.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""
