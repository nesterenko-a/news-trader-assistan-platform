/* Общий инициализатор Cytoscape-карты зависимостей.
   Используется на карточке бумаги и на странице /map:
   initDependencyMap(containerEl, graph, options)
     graph — объект {nodes:[{data:{id,label,type,is_target,is_key,metrics}}],
                     edges:[{data:{source,target,sign,strength,label,kind}}]}
   Возвращает контроллер { cy, applyStrength(min) }:
     applyStrength(min) — «all» | "weak" | "medium" | "strong": скрывает рёбра
     слабее выбранного порога, изолированные узлы делает полупрозрачными.
   Данные обычно попадают в window.__DEP_GRAPH из шаблона. */
window.initDependencyMap = function (container, graph, options) {
  if (!window.cytoscape) return null;
  if (!graph || !graph.nodes || !graph.nodes.length) return null;
  if (!container) return null;

  var STR_ORDER = { weak: 1, medium: 2, strong: 3 };

  var style = [
    { selector: "node", style: {
        "label": "data(label)",
        "width": 160, "height": 60,
        "shape": "round-rectangle",
        "background-color": "#202a40",
        "border-color": "#2b3750", "border-width": 1,
        "color": "#e6ebf5", "font-size": 13, "text-valign": "center",
        "text-wrap": "wrap", "text-max-width": 150,
        "padding": 6, "opacity": 1
    }},
    { selector: "node[is_key]", style: { "border-color": "#f5a623", "border-width": 2 }},
    { selector: "node[is_target]", style: {
        "border-color": "#4f8cff", "border-width": 2.5, "background-color": "#1e2f4d"
    }},
    { selector: "node.dep-dim", style: { "opacity": 0.2 }},
    { selector: "edge", style: {
        "width": 2, "line-color": "#2fbf71",
        "target-arrow-color": "#2fbf71", "target-arrow-shape": "triangle",
        "curve-style": "bezier",
        "label": "data(label)",
        "font-size": 10, "color": "#9fb3c8",
        "text-background-color": "#1a2233", "text-background-opacity": 0.85,
        "text-background-padding": 3, "text-rotation": "autorotate",
        "text-wrap": "wrap", "text-max-width": 150
    }},
    { selector: "edge[sign < 0]", style: {
        "line-color": "#e5484d", "target-arrow-color": "#e5484d", "color": "#e5484d"
    }}
  ];

  // префикс знака и механизм + сила
  graph.edges.forEach(function (e) {
    e.data.s = STR_ORDER[e.data.strength] || 2;
    e.data.label = (e.data.sign < 0 ? "− " : "+ ") + e.data.label;
  });

  var cy = cytoscape({
    container: container,
    elements: graph.nodes.concat(graph.edges),
    style: style,
    layout: { name: "breadthfirst", directed: true, spacingFactor: 1.15 },
    minZoom: 0.3, maxZoom: 3,
    wheelSensitivity: 0.15,
    boxSelectionEnabled: false
  });

  // «что отслеживать» — дописываем к подписи узла
  cy.nodes().forEach(function (nd) {
    var name = nd.data("label");
    var m = nd.data("metrics") || [];
    if (m.length) {
      var labels = m.slice(0, 3).map(function (x) { return x.label; });
      var extra = labels.join(", ") + (m.length > 3 ? "…" : "");
      nd.data("label", name + "\n(" + extra + ")");
    }
  });

  function applyStrength(min) {
    if (min === "all" || !min) { min = "weak"; }
    var threshold = STR_ORDER[min] || 1;
    cy.edges().forEach(function (el) {
      var visible = (el.data("s") || 1) >= threshold;
      el.style("display", visible ? "element" : "none");
    });
    // узлы без видимых рёбер — затемняем
    cy.nodes().forEach(function (nd) {
      var connected = nd.connectedEdges().filter(function (e) {
        return e.style("display") !== "none";
      }).length;
      if (connected === 0) { nd.addClass("dep-dim"); } else { nd.removeClass("dep-dim"); }
    });
  }

  applyStrength((options && options.strength) || "all");
  return { cy: cy, applyStrength: applyStrength };
};
