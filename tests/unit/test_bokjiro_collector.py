from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from benefitradar.bokjiro_collector import _parse_bokjiro_xml, collect_bokjiro
from benefitradar.models import Source

_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header>
    <resultCode>00</resultCode>
    <resultMsg>NORMAL SERVICE.</resultMsg>
  </header>
  <body>
    <items>
      <item>
        <servNm>청년 월세 지원</servNm>
        <servDgst>청년 월세 부담 경감 지원 사업</servDgst>
        <servId>WLF00001234</servId>
        <jurMnofNm>국토교통부</jurMnofNm>
        <sprtCycNm>월 20만원</sprtCycNm>
      </item>
      <item>
        <servNm>기초연금 수급자 지원</servNm>
        <servDgst>노인 기초생활 안정 지원</servDgst>
        <servId>WLF00005678</servId>
        <jurMnofNm>보건복지부</jurMnofNm>
        <sprtCycNm>월 30만원</sprtCycNm>
      </item>
    </items>
  </body>
</response>""".encode("utf-8")


def _make_source() -> Source:
    return Source(
        name="보조금24",
        type="api",
        url="https://www.bokjiro.go.kr/ssis-teu/TWAT52005M/twataa/wlfareInfo/selectWlfareInfo.do",
    )


class TestParseBokjiroXml:
    """Unit tests for XML parsing logic (no network needed)."""

    def test_parses_two_items_from_sample(self) -> None:
        articles = _parse_bokjiro_xml(
            _SAMPLE_XML, source_name="보조금24", category="benefit"
        )
        assert len(articles) == 2

    def test_first_article_title(self) -> None:
        articles = _parse_bokjiro_xml(
            _SAMPLE_XML, source_name="보조금24", category="benefit"
        )
        assert articles[0].title == "청년 월세 지원"

    def test_first_article_has_link_with_serv_id(self) -> None:
        articles = _parse_bokjiro_xml(
            _SAMPLE_XML, source_name="보조금24", category="benefit"
        )
        assert "WLF00001234" in articles[0].link

    def test_summary_contains_department(self) -> None:
        articles = _parse_bokjiro_xml(
            _SAMPLE_XML, source_name="보조금24", category="benefit"
        )
        assert "국토교통부" in articles[0].summary

    def test_summary_contains_amount(self) -> None:
        articles = _parse_bokjiro_xml(
            _SAMPLE_XML, source_name="보조금24", category="benefit"
        )
        assert "월 20만원" in articles[0].summary

    def test_source_and_category_set(self) -> None:
        articles = _parse_bokjiro_xml(
            _SAMPLE_XML, source_name="보조금24", category="benefit"
        )
        assert all(a.source == "보조금24" for a in articles)
        assert all(a.category == "benefit" for a in articles)

    def test_published_is_set(self) -> None:
        articles = _parse_bokjiro_xml(
            _SAMPLE_XML, source_name="보조금24", category="benefit"
        )
        assert all(a.published is not None for a in articles)

    def test_returns_empty_on_invalid_xml(self) -> None:
        articles = _parse_bokjiro_xml(
            b"<not valid xml", source_name="보조금24", category="benefit"
        )
        assert articles == []

    def test_returns_empty_on_empty_items(self) -> None:
        xml = b"""<?xml version="1.0"?>
        <response><body><items></items></body></response>"""
        articles = _parse_bokjiro_xml(
            xml, source_name="보조금24", category="benefit"
        )
        assert articles == []


class TestCollectBokjiro:
    """Tests for the collect_bokjiro function (API key handling)."""

    def test_returns_empty_without_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BOKJIRO_API_KEY", raising=False)
        source = _make_source()
        result = collect_bokjiro(source, category="benefit")
        assert result == []

    @patch("benefitradar.bokjiro_collector.requests.get")
    def test_collects_with_api_key(
        self,
        mock_get: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BOKJIRO_API_KEY", "test-key-123")

        mock_response = MagicMock()
        mock_response.content = _SAMPLE_XML
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        source = _make_source()
        articles = collect_bokjiro(source, category="benefit")

        assert len(articles) == 2
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert "serviceKey" in call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
