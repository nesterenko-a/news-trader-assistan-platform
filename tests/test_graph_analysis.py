from app.graph.graph_analysis import parse_graph_relations


def test_parse_graph_relations_valid_array():
    raw = (
        '[{"from": "OIL", "to": "GAS", "direction": "positive", '
        '"rationale": "нефть даёт газ"}, '
        '{"from": "GAS", "to": "WALMART", "direction": "negative", '
        '"rationale": "газ дорожает"}]'
    )
    rels = parse_graph_relations(raw)
    assert len(rels) == 2
    assert rels[0].from_name == "OIL"
    assert rels[0].to_name == "GAS"
    assert rels[0].direction == "positive"
    assert rels[1].to_name == "WALMART"
    assert rels[1].direction == "negative"


def test_parse_graph_relations_single_object():
    raw = '{"from": "GAS", "to": "TRANSPORT", "direction": "positive", "rationale": "x"}'
    rels = parse_graph_relations(raw)
    assert len(rels) == 1
    assert rels[0].from_name == "GAS"
    assert rels[0].to_name == "TRANSPORT"


def test_parse_graph_relations_markdown_and_whitespace():
    raw = '```json\n[{"from": "OIL", "to": "HOUSEHOLD", "direction": "positive", "rationale": "цены"}]```'
    rels = parse_graph_relations(raw)
    assert len(rels) == 1
    assert rels[0].to_name == "HOUSEHOLD"


def test_parse_graph_relations_invalid_or_empty():
    assert parse_graph_relations("") == []
    assert parse_graph_relations("не json") == []
    assert parse_graph_relations('[{"from": "OIL", "to": ""}]') == []


async def test_analyze_graph_text(monkeypatch):
    """analyze_graph_text передаёт текст в LLM и возвращает распарсенные связи."""
    from types import SimpleNamespace

    from app.graph.graph_analysis import analyze_graph_text

    captured = {}

    class FakeLLM:
        async def chat(self, system_prompt, user_prompt):
            captured["user"] = user_prompt
            captured["system"] = system_prompt
            return (
                '[{"from": "OIL", "to": "GAS", "direction": "positive", '
                '"rationale": "нефть — сырьё для газа"}, '
                '{"from": "GAS", "to": "HOUSEHOLD", "direction": "negative", '
                '"rationale": "дорогой газ давит на бюджет"}]'
            )

    text = (
        "              OIL\n"
        "               |\n"
        "               v\n"
        "             GAS\n"
        "               |\n"
        "               v\n"
        "          HOUSEHOLD"
    )
    rels = await analyze_graph_text(text, client=FakeLLM())
    assert len(rels) == 2
    assert rels[0].from_name == "OIL" and rels[0].to_name == "GAS"
    assert rels[1].direction == "negative"
    # текст схемы ушёл в user-промпт, инструкция — в system
    assert "OIL" in captured["user"]
    assert "knowledge graph" in captured["system"] or "влияния" in captured["system"]

