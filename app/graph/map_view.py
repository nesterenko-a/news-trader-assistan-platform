from xml.sax.saxutils import escape as _xesc

from app.graph.map import layout_dependency_map

NODE_W = 180
NODE_H = 54
PAD_TOP = 20
PAD = 24
LEVEL_X = 240       # расстояние между колонками
VERT_GAP = 150      # базовое вертикальное расстояние между узлами в колонке


def _esc(value: str) -> str:
    return _xesc(str(value))


def _trim(value: str, limit: int) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _anchor_point(x: int, y: int, target_x: int, target_y: int) -> tuple[float, float]:
    """Точка на границе прямоугольника узла (по центру граней) в сторону цели."""
    cx = x + NODE_W / 2
    cy = y + NODE_H / 2
    dx = target_x - cx
    dy = target_y - cy
    # выбираем грань по доминирующему направлению
    if dx <= 0 and dy <= 0:
        return (x, cy)
    if dx <= 0 and dy >= 0:
        return (x, cy)
    if dx >= 0 and dy <= 0:
        return (x + NODE_W, cy)
    return (x + NODE_W, cy)


def _edge_curve(x1: float, y1: float, x2: float, y2: float) -> str:
    """Квадратичный Безье с горизонтальным изгибом для аккуратных рёбер."""
    mid = (x1 + x2) * 0.5
    return f"M{x1},{y1} C{mid},{y1} {mid},{y2} {x2},{y2}"


def build_map_svg(graph: dict) -> dict:
    """Серверная SVG-карта зависимостей: кривые рёбра с механизмом и знак влияния.

    Возвращает {'svg': '<svg>…</svg>'}. Рёбра — кривые (Безье) от границы узла
    до границы узла со стрелкой и подписью-механизмом; под узлом — «что отслеживать».
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        return {"svg": ""}

    coords = layout_dependency_map(graph, x_gap=LEVEL_X, y_gap=VERT_GAP)

    # max по координатам + ширина/высота
    span_w = max((x for x, _ in coords.values()), default=0) + NODE_W
    span_h = max((y for _, y in coords.values()), default=0) + NODE_H

    # метрики под узлами занимают дополнительную высоту
    extra_h = 0
    for n in nodes:
        if n.get("metrics"):
            extra_h = max(extra_h, 34)
    width = span_w + PAD * 2
    height = span_h + PAD * 2 + extra_h

    node_lookup = {n["name"]: n for n in nodes}

    parts: list[str] = []
    parts.append(f'<svg class="depmap" viewBox="0 0 {width} {height}" role="img">')

    # рёбра — кривые от границы узла к границе узла
    for e in edges:
        sx, sy = coords[e["from"]]
        tx, ty = coords[e["to"]]
        x1, y1 = _anchor_point(sx, sy, tx + NODE_W / 2, ty + NODE_H / 2)
        x2, y2 = _anchor_point(tx, ty, sx + NODE_W / 2, sy + NODE_H / 2)
        pos_cls = "dep-pos" if e.get("sign", 0) >= 0 else "dep-neg"
        sign_txt = "+" if e.get("sign", 0) >= 0 else "−"
        mechanism = _trim(e.get("mechanism") or "", 64) or "связь"
        mid_x = (x1 + x2) * 0.5
        mid_y = (y1 + y2) * 0.5
        parts.append(
            f'<path d="{_edge_curve(x1, y1, x2, y2)}" class="{pos_cls}" '
            f'marker-end="url(#deparrow-{pos_cls})"/>'
        )
        parts.append(
            f'<g class="dep-mech {pos_cls}">'
            f'<rect x="{mid_x - 70}" y="{mid_y - 11}" width="140" height="22" rx="4" '
            f'class="dep-mech-bg"/>'
            f'<text x="{mid_x}" y="{mid_y + 4}" text-anchor="middle">{_esc(sign_txt)} {_esc(mechanism)}</text>'
            f'</g>'
        )

    # узлы
    for name, (x, y) in coords.items():
        node = node_lookup[name]
        cls = "dep-target" if node.get("is_target") else ("dep-key" if node.get("is_key") else "dep-node")
        parts.append(f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" rx="9" class="{cls}"/>')
        parts.append(
            f'<text x="{x + NODE_W / 2}" y="{y + NODE_H / 2 + 5}" text-anchor="middle" '
            f'class="dep-name">{_esc(name)}</text>'
        )
        metrics = node.get("metrics") or []
        if metrics:
            my = y + NODE_H + 14
            labels = ", ".join(_trim(m["label"], 40) for m in metrics[:3])
            if len(metrics) > 3:
                labels += "…"
            parts.append(
                f'<text x="{x + 2}" y="{my}" class="dep-metric">{_esc(labels)}</text>'
            )

    # дефсы стрелок с цветом по направлению
    parts.append('<defs>')
    parts.append(
        '<marker id="deparrow-dep-pos" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="9" markerHeight="9" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#2fbf71"/></marker>'
    )
    parts.append(
        '<marker id="deparrow-dep-neg" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="9" markerHeight="9" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#e5484d"/></marker>'
    )
    parts.append('</defs>')
    parts.append("</svg>")
    return {"svg": "\n".join(parts)}
