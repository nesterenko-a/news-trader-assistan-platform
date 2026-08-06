from datetime import date, timedelta

from app.news.feed_parsers import parse_feed
from app.news.llm_parse import _extract_json_list

VALID_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Тест</title>
  <item><title>Заголовок 1</title><link>https://example.com/1</link>
    <description>Текст новости 1</description><pubDate>Mon, 01 Jan 2026 10:00:00 GMT</pubDate></item>
  <item><title>Заголовок 2</title><link>https://example.com/2</link>
    <description>Текст новости 2</description></item>
</channel></rss>""".encode("utf-8")

BROKEN_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Тест</title>
  <item>
    <title>Новость про Apple & Samsung</title>
    <link>https://example.com/1</link>
    <description>Текст с неэкранированными & символами и <b>html</b>
  </item>
  <item>
    <title>Вторая новость
    <link>https://example.com/2</link>
    <description>Описание без закрытия тега
</channel></rss>""".encode("utf-8")


def test_valid_rss_parsed_by_feedparser():
    result = parse_feed(VALID_RSS)
    assert result.parser == "feedparser"
    assert len(result.entries) == 2
    assert result.entries[0]["title"] == "Заголовок 1"
    assert result.entries[0]["link"] == "https://example.com/1"


def test_broken_rss_lenient_parser_extracts_entries():
    from app.news.feed_parsers import _LenientRssParser

    parser = _LenientRssParser()
    parser.feed(BROKEN_RSS.decode("utf-8", "replace"))
    parser.close()
    titles = [e.get("title", "").strip() for e in parser.entries]
    assert any("Samsung" in t for t in titles)


def test_parse_feed_handles_broken_rss_without_crash():
    result = parse_feed(BROKEN_RSS)
    assert result.entries, "конвейер должен извлечь записи даже из битой ленты"
    assert result.parser in ("feedparser", "lenient_html")


def test_empty_content_error():
    result = parse_feed("<html><body>просто страница</body></html>".encode("utf-8"))
    assert result.entries == []
    assert result.error


def test_extract_json_list_plain():
    raw = '[{"title": "A", "link": "https://a", "description": "d", "published": "p"}]'
    assert _extract_json_list(raw) == [
        {"title": "A", "link": "https://a", "description": "d", "published": "p"}
    ]


def test_extract_json_list_markdown_wrapped():
    raw = '```json\n[{"title": "B"}]\n```'
    assert _extract_json_list(raw) == [
        {"title": "B", "link": "", "description": "", "published": ""}
    ]


def test_extract_json_list_invalid():
    assert _extract_json_list("не JSON вообще") == []
    assert _extract_json_list('[{"title": ""}]') == []


async def test_check_feed_llm_fallback(monkeypatch):
    from app.news import feed_check

    async def fake_fetch(url):
        return "<html>не валидный rss</html>".encode("utf-8"), "ok"

    async def fake_llm(data):
        return [{"title": "X", "link": "", "description": "", "published": ""}]

    monkeypatch.setattr(feed_check, "fetch_feed_bytes", fake_fetch)
    monkeypatch.setattr(
        "app.news.llm_parse.parse_feed_with_llm", fake_llm
    )
    ok, desc = await feed_check.check_feed(
        "https://example.com/feed", use_llm=True
    )
    assert ok is True and "LLM" in desc


async def test_check_feed_no_llm_fails(monkeypatch):
    from app.news import feed_check

    async def fake_fetch(url):
        return "<html>не валидный rss</html>".encode("utf-8"), "ok"

    monkeypatch.setattr(feed_check, "fetch_feed_bytes", fake_fetch)
    ok, desc = await feed_check.check_feed("https://example.com/feed")
    assert ok is False and "Не похоже на RSS" in desc
