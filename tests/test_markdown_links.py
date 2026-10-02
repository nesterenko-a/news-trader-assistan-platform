from pathlib import Path

from tools.markdown_links import anchor, anchors, validate_file


def test_anchor_matches_github_style_russian_heading():
    assert anchor("### Проверки: API / Web!") == "проверки-api-web"


def test_duplicate_headings_get_numbered_anchors(tmp_path):
    document = tmp_path / "document.md"
    document.write_text("## Повтор\n## Повтор\n", encoding="utf-8")
    assert anchors(document) == {"повтор", "повтор-1"}


def test_validate_file_checks_relative_path_and_anchor(tmp_path):
    target = tmp_path / "target.md"
    target.write_text("## Раздел\n", encoding="utf-8")
    source = tmp_path / "source.md"
    source.write_text(
        "[good](target.md#раздел)\n[missing](target.md#нет)\n[file](none.md)\n",
        encoding="utf-8",
    )
    assert validate_file(source) == [
        f"{source}:2: не найден якорь target.md#нет",
        f"{source}:3: не найден файл none.md",
    ]


def test_validate_file_skips_external_links(tmp_path):
    document = tmp_path / "document.md"
    document.write_text(
        "[web](https://example.com) [mail](mailto:test@example.com) [phone](tel:+70000000000)\n",
        encoding="utf-8",
    )
    assert validate_file(document) == []
