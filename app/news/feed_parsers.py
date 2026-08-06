"""Конвейер парсинга RSS/Atom-лент.

Штатный парсер — feedparser. Если он не смог извлечь записи (невалидный XML —
битые сущности, незакрытые теги, HTML вместо XML), применяется толерантный
HTML/XML-парсер на базе html.parser (stdlib), который не падает на таких данных
и собирает item/entry-блоки.
"""

from dataclasses import dataclass, field
from html.parser import HTMLParser

import feedparser


@dataclass
class FeedParseResult:
    entries: list[dict] = field(default_factory=list)
    parser: str = "none"
    error: str | None = None


class _LenientRssParser(HTMLParser):
    """Толерантный сборщик записей RSS/Atom (item/entry).

    Не требует строгого XML: переживает битые сущности, незакрытые теги и
    HTML внутри description. Собирает title/link/description|summary/
    pubdate|published|updated.
    """

    ENTRY_TAGS = {"item", "entry"}
    FIELD_TAGS = {
        "title",
        "link",
        "description",
        "summary",
        "pubdate",
        "published",
        "updated",
        "guid",
        "id",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries: list[dict] = []
        self._cur_entry: dict | None = None
        self._entry_depth = 0
        self._cur_field: str | None = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.ENTRY_TAGS:
            self._entry_depth += 1
            if self._entry_depth == 1:
                self._cur_entry = {}
            return
        if self._cur_entry is not None and self._cur_field is None:
            if tag in self.FIELD_TAGS:
                self._cur_field = tag
                if tag == "link":
                    href = dict(attrs).get("href", "")
                    if href:
                        self._cur_entry.setdefault("link", href)

    def handle_data(self, data):
        if self._cur_entry is not None and self._cur_field is not None:
            self._cur_entry.setdefault(self._cur_field, "")
            self._cur_entry[self._cur_field] += data

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.ENTRY_TAGS and self._entry_depth > 0:
            self._entry_depth -= 1
            if self._entry_depth == 0 and self._cur_entry:
                self.entries.append(self._cur_entry)
                self._cur_entry = None
        elif self._cur_field is not None and tag == self._cur_field:
            self._cur_field = None


def _normalize_entries(entries: list[dict]) -> list[dict]:
    out = []
    for e in entries:
        title = (e.get("title") or "").strip()
        if not title:
            continue
        out.append(
            {
                "title": title,
                "link": (e.get("link") or "").strip(),
                "description": (
                    e.get("description") or e.get("summary") or ""
                ).strip(),
                "published": (
                    e.get("pubdate")
                    or e.get("published")
                    or e.get("updated")
                    or ""
                ).strip(),
            }
        )
    return out


def parse_feed(data: bytes) -> FeedParseResult:
    """Пробует распарсить ленту конвейером парсеров.

    1) feedparser — штатный строгий парсер;
    2) толерантный HTML/XML-парсер — для невалидного XML/HTML-контента.

    Возвращает записи и имя парсера, который их извлёк.
    """
    parsed = feedparser.parse(data)
    if parsed.entries:
        entries = _normalize_entries(
            [
                {
                    "title": e.get("title", ""),
                    "link": e.get("link", ""),
                    "description": e.get("summary", "")
                    or e.get("description", ""),
                    "published": e.get("published", ""),
                }
                for e in parsed.entries
            ]
        )
        return FeedParseResult(entries=entries, parser="feedparser")

    text = data.decode("utf-8", "replace")
    lenient = _LenientRssParser()
    try:
        lenient.feed(text)
        lenient.close()
    except Exception:
        lenient = _LenientRssParser()
        lenient.feed(text.replace("\x00", ""))
        lenient.close()
    entries = _normalize_entries(lenient.entries)
    if entries:
        return FeedParseResult(entries=entries, parser="lenient_html")

    reason = parsed.get("bozo_exception")
    detail = type(reason).__name__ if reason else "нет элементов item/entry"
    return FeedParseResult(entries=[], parser="none", error=detail)
