"""Тесты серверного рендера Markdown (app/presentation/markdown_renderer.py)."""

from app.presentation.markdown_renderer import render_markdown


def test_render_basic_markdown():
    html = render_markdown("# Заголовок\n\nВот **жирный** и *курсив*.")
    assert "<h1" in html and "Заголовок" in html
    assert "<strong>жирный</strong>" in html
    assert "<em>курсив</em>" in html


def test_render_table():
    md = "| A | B |\n|--|--|\n| 1 | 2 |"
    html = render_markdown(md)
    assert "<table>" in html and "<th>" in html and "<td>1</td>" in html


def test_sanitizes_script_and_event_handlers():
    md = "<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>"
    html = render_markdown(md)
    assert "<script" not in html
    assert "onerror" not in html
    assert "alert(1)" not in html


def test_code_highlight_python():
    html = render_markdown("```python\nx = 1\nprint(x)\n```")
    # Pygments формирует пред-обёртку; класс сохранён после санитизации
    assert "<pre" in html
    assert "highlight" in html  # div.highlight от Pygments
    assert "x" in html


def test_mermaid_block_preserved():
    md = "```mermaid\ngraph TD; A-->B\n```"
    html = render_markdown(md)
    assert 'class="mermaid"' in html
    assert "graph TD" in html


def test_empty():
    assert render_markdown("") == ""
    assert render_markdown(None) == ""
