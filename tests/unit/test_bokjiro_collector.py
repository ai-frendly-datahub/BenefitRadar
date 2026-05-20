from __future__ import annotations

import pathlib
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from benefitradar import bokjiro_collector
from benefitradar.bokjiro_collector import (
    _cache_key,
    _extract_total_count,
    _load_cached_response,
    _load_stale_cache,
    _parse_bokjiro_xml,
    _parse_last_mod_ymd,
    _save_cache,
    _text,
    _validate_xml_response,
    collect_bokjiro,
)
from benefitradar.models import Article, Source

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
        <bizChrDeptNm>국토교통부</bizChrDeptNm>
        <sprtCycNm>월 20만원</sprtCycNm>
        <lastModYmd>2026-03-15</lastModYmd>
      </item>
      <item>
        <servNm>기초연금 수급자 지원</servNm>
        <servDgst>노인 기초생활 안정 지원</servDgst>
        <servId>WLF00005678</servId>
        <bizChrDeptNm>보건복지부</bizChrDeptNm>
        <sprtCycNm>월 30만원</sprtCycNm>
        <lastModYmd>2026-03-10</lastModYmd>
      </item>
    </items>
  </body>
</response>""".encode()

_SINGLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode></header>
  <body>
    <totalCount>1</totalCount>
    <items>
      <item>
        <servNm>장애인 활동지원</servNm>
        <servDgst>일상생활 지원</servDgst>
        <servDtlLink>https://example.com/detail</servDtlLink>
        <jurMnofNm>보건복지부</jurMnofNm>
        <sprtAmt>월 10만원</sprtAmt>
        <lastModYmd>20260521</lastModYmd>
      </item>
    </items>
  </body>
</response>""".encode()


def _make_source() -> Source:
    return Source(
        name="보조금24",
        type="api",
        url="https://www.bokjiro.go.kr/ssis-teu/TWAT52005M/twataa/wlfareInfo/selectWlfareInfo.do",
    )


class TestParseBokjiroXml:
    """Unit tests for XML parsing logic (no network needed)."""

    def test_parses_two_items_from_sample(self) -> None:
        articles = _parse_bokjiro_xml(_SAMPLE_XML, source_name="보조금24", category="benefit")
        assert len(articles) == 2

    def test_first_article_title(self) -> None:
        articles = _parse_bokjiro_xml(_SAMPLE_XML, source_name="보조금24", category="benefit")
        assert articles[0].title == "청년 월세 지원"

    def test_first_article_has_link_with_serv_id(self) -> None:
        articles = _parse_bokjiro_xml(_SAMPLE_XML, source_name="보조금24", category="benefit")
        assert "WLF00001234" in articles[0].link

    def test_summary_contains_department(self) -> None:
        articles = _parse_bokjiro_xml(_SAMPLE_XML, source_name="보조금24", category="benefit")
        assert "국토교통부" in articles[0].summary

    def test_summary_contains_amount(self) -> None:
        articles = _parse_bokjiro_xml(_SAMPLE_XML, source_name="보조금24", category="benefit")
        assert "월 20만원" in articles[0].summary

    def test_source_and_category_set(self) -> None:
        articles = _parse_bokjiro_xml(_SAMPLE_XML, source_name="보조금24", category="benefit")
        assert all(a.source == "보조금24" for a in articles)
        assert all(a.category == "benefit" for a in articles)

    def test_published_is_set(self) -> None:
        articles = _parse_bokjiro_xml(_SAMPLE_XML, source_name="보조금24", category="benefit")
        assert all(a.published is not None for a in articles)

    def test_returns_empty_on_invalid_xml(self) -> None:
        articles = _parse_bokjiro_xml(b"<not valid xml", source_name="보조금24", category="benefit")
        assert articles == []

    def test_returns_empty_on_empty_items(self) -> None:
        xml = b"""<?xml version="1.0"?>
        <response><body><items></items></body></response>"""
        articles = _parse_bokjiro_xml(xml, source_name="보조금24", category="benefit")
        assert articles == []

    def test_parses_alternate_fields_direct_link_and_compact_date(self) -> None:
        articles = _parse_bokjiro_xml(_SINGLE_XML, source_name="보조금24", category="benefit")

        assert len(articles) == 1
        assert articles[0].title == "장애인 활동지원"
        assert articles[0].link == "https://example.com/detail"
        assert "월 10만원" in articles[0].summary
        assert "보건복지부" in articles[0].summary
        assert articles[0].published == datetime(2026, 5, 21, tzinfo=UTC)

    def test_ignores_item_without_title_and_uses_title_as_summary_fallback(self) -> None:
        xml = """
        <response><body><items>
          <item><servId>NO_TITLE</servId></item>
          <item><title>제목만 있는 공고</title></item>
        </items></body></response>
        """.encode()

        articles = _parse_bokjiro_xml(xml, source_name="보조금24", category="benefit")

        assert len(articles) == 1
        assert articles[0].summary == "제목만 있는 공고"


def test_cache_helpers_ignore_service_key_and_handle_fresh_expired_and_corrupt_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    endpoint = "https://apis.example/list"
    params = {"serviceKey": "secret", "pageNo": "1", "numOfRows": "10"}
    other_key_params = {**params, "serviceKey": "different"}
    cache_file = tmp_path / "response.xml"

    assert _cache_key(endpoint, params) == _cache_key(endpoint, other_key_params)

    _save_cache(cache_file, b"<response/>")
    assert _load_cached_response(cache_file) == b"<response/>"
    assert _load_stale_cache(cache_file) == b"<response/>"

    monkeypatch.setattr(bokjiro_collector, "_CACHE_TTL_SECONDS", 1)
    cache_file.with_suffix(".meta.json").write_text(
        '{"cached_at": 0}',
        encoding="utf-8",
    )
    assert _load_cached_response(cache_file) is None
    assert _load_stale_cache(cache_file) == b"<response/>"

    cache_file.with_suffix(".meta.json").write_text("{not-json", encoding="utf-8")
    assert _load_cached_response(cache_file) is None


def test_cache_helpers_handle_missing_and_io_errors(tmp_path: pathlib.Path) -> None:
    missing = tmp_path / "missing.xml"
    cache_file = tmp_path / "response.xml"
    cache_file.write_bytes(b"<response/>")

    assert _load_cached_response(missing) is None
    assert _load_stale_cache(missing) is None

    with patch.object(pathlib.Path, "read_bytes", side_effect=OSError("read failed")):
        assert _load_stale_cache(cache_file) is None

    with patch.object(pathlib.Path, "mkdir", side_effect=OSError("write failed")):
        _save_cache(tmp_path / "blocked" / "response.xml", b"<response/>")


def test_response_validation_total_count_date_and_text_helpers() -> None:
    valid_empty = b"<response><header><resultCode>00</resultCode></header><body /></response>"
    invalid_code = b"<response><header><resultCode>99</resultCode></header><body /></response>"
    invalid_xml = b"<response"
    total_cnt = b"<response><body><totalCnt>12</totalCnt></body></response>"
    no_total = b"<response><body><totalCount>n/a</totalCount></body></response>"

    assert _validate_xml_response(valid_empty)
    assert not _validate_xml_response(invalid_code)
    assert not _validate_xml_response(invalid_xml)
    assert _extract_total_count(_SINGLE_XML) == 1
    assert _extract_total_count(total_cnt) == 12
    assert _extract_total_count(no_total) is None
    assert _extract_total_count(invalid_xml) is None
    assert _parse_last_mod_ymd("2026/05/21") == datetime(2026, 5, 21, tzinfo=UTC)
    assert _parse_last_mod_ymd("2026-05-21 13:14:15") == datetime(
        2026, 5, 21, 13, 14, 15, tzinfo=UTC
    )
    assert _parse_last_mod_ymd("") is None
    assert _parse_last_mod_ymd("bad-date") is None

    element = bokjiro_collector.ElementTree.fromstring("<item><name> value </name></item>")
    assert _text(element, "name") == "value"
    assert _text(element, "missing") == ""


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
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setenv("BOKJIRO_API_KEY", "test-key-123")
        monkeypatch.setattr("benefitradar.bokjiro_collector._CACHE_DIR", tmp_path / "api_cache")

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

    def test_uses_fresh_cache_before_network(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setenv("BOKJIRO_API_KEY", "test-key-123")
        monkeypatch.setattr("benefitradar.bokjiro_collector._CACHE_DIR", tmp_path)

        source = _make_source()
        endpoint = (
            "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist"
        )
        cache_file = (
            tmp_path
            / f"bokjiro_{_cache_key(endpoint, {'serviceKey': 'test-key-123', 'pageNo': '1', 'numOfRows': '2'})}.xml"
        )
        cache_file.write_bytes(_SAMPLE_XML)
        cache_file.with_suffix(".meta.json").write_text(
            f'{{"cached_at": {time.time()}}}',
            encoding="utf-8",
        )

        with patch("benefitradar.bokjiro_collector.requests.get") as mock_get:
            articles = collect_bokjiro(source, category="benefit", limit=2)

        assert len(articles) == 2
        mock_get.assert_not_called()

    @patch("benefitradar.bokjiro_collector.requests.get")
    def test_falls_back_to_stale_cache_on_timeout(
        self,
        mock_get: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setenv("BOKJIRO_API_KEY", "test-key-123")
        monkeypatch.setattr("benefitradar.bokjiro_collector._CACHE_DIR", tmp_path)
        mock_get.side_effect = bokjiro_collector.requests.exceptions.Timeout()

        source = _make_source()
        endpoint = (
            "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist"
        )
        cache_file = (
            tmp_path
            / f"bokjiro_{_cache_key(endpoint, {'serviceKey': 'test-key-123', 'pageNo': '1', 'numOfRows': '5'})}.xml"
        )
        cache_file.write_bytes(_SINGLE_XML)
        cache_file.with_suffix(".meta.json").write_text('{"cached_at": 0}', encoding="utf-8")

        articles = collect_bokjiro(source, category="benefit", limit=5, timeout=1)

        assert [article.title for article in articles] == ["장애인 활동지원"]

    @patch("benefitradar.bokjiro_collector.requests.get")
    def test_returns_empty_on_invalid_schema_without_cache(
        self,
        mock_get: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setenv("BOKJIRO_API_KEY", "test-key-123")
        monkeypatch.setattr("benefitradar.bokjiro_collector._CACHE_DIR", tmp_path)
        mock_response = MagicMock()
        mock_response.content = b"<response><header><resultCode>99</resultCode></header></response>"
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = collect_bokjiro(_make_source(), category="benefit", limit=5)

        assert result == []

    @patch("benefitradar.bokjiro_collector.requests.get")
    def test_returns_partial_results_when_later_page_fails(
        self,
        mock_get: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        monkeypatch.setenv("BOKJIRO_API_KEY", "test-key-123")
        monkeypatch.setattr("benefitradar.bokjiro_collector._CACHE_DIR", tmp_path)
        article = Article(
            title="페이지 결과",
            link="https://example.com/page",
            summary="summary",
            published=None,
            source="보조금24",
            category="benefit",
        )
        monkeypatch.setattr(
            bokjiro_collector,
            "_parse_bokjiro_xml",
            lambda content, *, source_name, category: [article] * 100,
        )
        first_response = MagicMock()
        first_response.content = (
            b"<response><header><resultCode>00</resultCode></header><body /></response>"
        )
        first_response.status_code = 200
        first_response.raise_for_status = MagicMock()
        mock_get.side_effect = [
            first_response,
            bokjiro_collector.requests.exceptions.ConnectionError("down"),
        ]

        articles = collect_bokjiro(_make_source(), category="benefit", limit=150)

        assert len(articles) == 100
        assert mock_get.call_count == 2
