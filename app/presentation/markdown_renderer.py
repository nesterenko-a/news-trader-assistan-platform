"""Серверный рендер Markdown-ответа LLM в HTML для страницы «Теханализ в LLM».

markdown-it-py конвертирует Markdown в HTML; Pygments подсвечивает блоки кода;
nh3 санитизирует итоговый HTML (безопасно вставляется в шаблон как `| safe`).

Поддерживается:
- таблицы, зачёркивание, ссылки (GFM);
- блоки кода ```lang — подсветка через Pygments;
- ```mermaid — оставляется как `<pre class="mermaid">` (рендерит Mermaid на клиенте);
- формулы $...$ / $$...$$ — остаются текстом, рендерит KaTeX (auto-render) на клиенте.
"""

from markdown_it import MarkdownIt
import nh3
from pygments import highlight
from pygments.formatters.html import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

# Теги и атрибуты, разрешённые после санитизации
ALLOWED_TAGS = {
    "p", "br", "strong", "em", "del", "s", "u", "ins",
    "a", "code", "pre", "blockquote", "ul", "ol", "li", "h1",
    "h2", "h3", "h4", "h5", "h6", "hr", "table", "thead",
    "tbody", "tr", "th", "td", "img", "span", "div", "sup",
    "sub", "mark",
}

# Атрибуты на элементах; class разрешаем на большинстве (для Pygments и mermaid)
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "code": {"class"},
    "span": {"class"},
    "pre": {"class"},
    "div": {"class"},
    "th": {"align"},
    "td": {"align"},
}

_formatter = HtmlFormatter(nowrap=False)

_md: MarkdownIt | None = None


def _build_md() -> MarkdownIt:
    """Собирает MarkdownIt с GFM-расширениями и кастомным �дером кода."""
    md = MarkdownIt("commonmark").enable("table").enable("strikethrough")
    # ссылки-преобразование (linkify) — эмодзи/URL авто-ссылки
    try:
        from linkify_it import LinkifyIt  # есть транзитивно с markdown-it-py
        md.use(
            "linkify-it-py",
            validate=lambda *a, **k: True,
        )
    except Exception:  # pragma: no cover
        pass

    default_fence = md.renderer.rules["fence"]

    def fence_render(tokens, idx, options, env):
        token = tokens[idx]
        info = (token.info or "").strip().split(maxsplit=1)
        lang = info[0].lower() if info else ""
        code = token.content
        if lang == "mermaid":
            # оставляем для Mermaid на клиенте (санитизация разрешит pre.mermaid)
            return f'<pre class="mermaid">{code}</pre>\n'
        if lang:
            try:
                lexer = get_lexer_by_name(lang, stripall=False)
                return highlight(code, lexer, _formatter) + "\n"
            except ClassNotFound:
                pass
        return default_fence(tokens, idx, options, env)

    md.renderer.rules["fence"] = fence_render
    return md


def render_markdown(text: str | None) -> str:
    """Преобразует Markdown в безопасный HTML (markdown-it-py + nh3).

    Результат можно вставлять в Jinja шаблон через `| safe`.
    """
    global _md
    if _md is None:
        _md = _build_md()
    if not text:
        return ""
    html = _md.render(text)
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https", "mailto", "relative"},
        link_rel=None,
    )


__all__ = ["render_markdown"]
