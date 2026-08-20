from xml.sax.saxutils import escape as _xesc

from app.graph.map import layout_dependency_map

NODE_W = 170
NODE_H = 46
PAD = 20


def _esc(value: str) -> str:
    return _xesc(str(value))


def build_map_svg(graph: dict, x_gap: int = 230, y_gap: int = 120) -> dict:
    """Серверная SVG-разметка карты зависимостей.

    Возвращает {'svg': '<svg>…</svg>'} — слоистый направленный граф: узлы-
    прямоугольники (target/ключевые подсвечены), стрелки с сокращённым
    механизмом (rationale) и знаком ±, под узлом — список «что отслеживать».
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        return {"svg": ""}

    coords = layout_dependency_map(graph, x_gap=x_gap, y_gap=y_gap)
    max_x = max((x for x, _ in coords.values()), default=0) + NODE_W + PAD
    max_y = max((y for _, y in coords.values()), default=0) + NODE_H + PAD
    width = max_x + PAD
    height = max_y + PAD

    parts: list[str] = []
    parts.append(f'<svg class="depmap" viewBox="0 0 {width} {height}" preserveAspectRatio="none">')

    node_lookup = {n["name"]: n for n in nodes}

    # рёбра
    for e in edges:
        sx, sy = coords[e["from"]]
        tx, ty = coords[e["to"]]
        x1 = sx + NODE_W
        y1 = sy + NODE_H / 2
        x2 = tx
        y2 = ty + NODE_H / 2
        mid_x = (x1 + x2) / 2
        mid_y = (y1 + y2) / 2
        pos_cls = "dep-pos" if e.get("sign", 0) >= 0 else "dep-neg"
        sign_txt = "+" if e.get("sign", 0) >= 0 else "−"
        mechanism = (e.get("mechanism") or "").strip()
        label = mechanism if mechanism else ("прямая связь" if e.get("kind") == "direct" else "косвенная связь")
        if len(label) > 60:
            label = label[:57] + "…"
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="{pos_cls}"'
            f' marker-end="url(#deparrow)"/>'
        )
        parts.append(
            f'<text x="{mid_x}" y="{mid_y - 4}" class="dep-mech {pos_cls}">{_esc(sign_txt) } {_esc(label)}</text>'
        )

    # узлы
    for name, (x, y) in coords.items():
        node = node_lookup[name]
        cls = "dep-target" if node.get("is_target") else ("dep-key" if node.get("is_key") else "dep-node")
        parts.append(
            f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="8" class="{cls}"/>'
        )
        parts.append(
            f'<text x="{x + NODE_W / 2}" y="{y + NODE_H / 2 + 5}" text-anchor="middle" class="dep-name">{_esc(name)}</text>'
        )
        # «что отслеживать»
        metrics = node.get("metrics") or []
        if metrics:
            my = y + NODE_H + 12
            labels = ", ".join(m["label"] for m in metrics[:3])
            if len(metrics) > 3:
                labels += "…"
            parts.append(
                f'<text x="{x + 4}" y="{my}" class="dep-metric">{_esc(labels)}</text>'
            )

    parts.append(
        '<defs><marker id="deparrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="currentColor"/></marker></defs>'
    )
    parts.append("</svg>")
    return {"svg": "\n".join(parts)}
