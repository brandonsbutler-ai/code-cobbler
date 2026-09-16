"""The interactive map: an SVG graph with the source one click away.

Inline SVG rather than a canvas or a library, for three reasons. It is text, so
it lives in the same self-contained file as everything else. Every node is a
real DOM element, so clicking, filtering and linking need no hit-testing. And it
scales without going blurry when somebody zooms in on a 40-module diagram.

Colour is load-bearing here, so the legend states what each one is DERIVED from
rather than what it feels like. Grey is deliberately not a shade of red: "no
static path reaches this" is an inference about a language that dispatches
through registries and getattr, while the others are read from the syntax.
"""

import html
import json

from .layout import NODE_H, NODE_W, state_of

_PALETTE = {
    "clean":     ("#2d6a4f", "#e7f2ec", "reachable, no unfinished-work signals"),
    "warm":      ("#8a5a00", "#fdf4e3", "carries signals of unfinished work"),
    "hot":       ("#a13d2d", "#fbecea", "several signals of unfinished work"),
    "broken":    ("#7b2d26", "#f7dedb", "this file does not parse"),
    "orphan":    ("#5a4b8a", "#efecf8", "nothing imports it, nothing starts from it"),
    "unreached": ("#6b6b66", "#ececea", "no static path from an entry point (inference)"),
}


def _e(v):
    return html.escape("" if v is None else str(v))


def render(graph, project, frontier_by_module, snippets_by_module,
           deadends_by_module, origins):
    """SVG plus the JSON payload the panel reads. Returns (svg, payload)."""
    nodes, edges = graph["nodes"], graph["edges"]

    edge_svg = []
    for edge in edges:
        stub = bool(deadends_by_module.get(edge["to"]))
        cls = "edge deadend" if stub else ("edge back" if edge["back"] else "edge")
        edge_svg.append(
            f'<path class="{cls}" d="{edge["path"]}" '
            f'data-from="{_e(edge["from"])}" data-to="{_e(edge["to"])}" '
            f'marker-end="url(#{"stub" if stub else "arrow"})"/>')

    node_svg = []
    for name, node in sorted(nodes.items()):
        state, why = state_of(node)
        stroke, fill, _ = _PALETTE[state]
        label = name.rsplit(".", 1)[-1][:24]
        prefix = name.rsplit(".", 1)[0][:26] if "." in name else ""
        marks = sum(node["counts"].values())
        origin = origins.get(name, {}).get("origin", "")
        badge = ""
        if origin == "untracked":
            badge = (f'<text class="badge" x="{node["x"] + NODE_W - 6}" '
                     f'y="{node["y"] + 13}" text-anchor="end">untracked</text>')
        elif origin == "vendored":
            badge = (f'<text class="badge" x="{node["x"] + NODE_W - 6}" '
                     f'y="{node["y"] + 13}" text-anchor="end">vendored</text>')
        node_svg.append(
            f'<g class="node" data-name="{_e(name)}" data-state="{state}" '
            f'tabindex="0" role="button" aria-label="{_e(name)}: {_e(why)}">'
            f'<rect x="{node["x"]}" y="{node["y"]}" width="{NODE_W}" '
            f'height="{NODE_H}" rx="7" fill="{fill}" stroke="{stroke}"/>'
            f'<text class="mod" x="{node["x"] + 9}" y="{node["y"] + 17}">'
            f'{_e(prefix)}</text>'
            f'<text class="name" x="{node["x"] + 9}" y="{node["y"] + 31}">'
            f'{_e(label)}</text>'
            f'{badge}'
            + (f'<text class="marks" x="{node["x"] + NODE_W - 6}" '
               f'y="{node["y"] + 31}" text-anchor="end">{marks}</text>'
               if marks else "")
            + "</g>")

    svg = f"""<svg id="graph" viewBox="0 0 {graph['width']} {graph['height']}"
     width="{graph['width']}" height="{graph['height']}"
     xmlns="http://www.w3.org/2000/svg" role="img"
     aria-label="module dependency map">
  <defs>
    <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7"
            markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z"/></marker>
    <marker id="stub" viewBox="0 0 8 8" refX="5" refY="4" markerWidth="8"
            markerHeight="8" orient="auto"><path d="M1,0 L1,8 M4,0 L4,8"
            stroke-width="1.6" fill="none"/></marker>
  </defs>
  <g class="edges">{''.join(edge_svg)}</g>
  <g class="nodes">{''.join(node_svg)}</g>
</svg>"""

    payload = {}
    for name, node in nodes.items():
        state, why = state_of(node)
        row = frontier_by_module.get(name, {})
        payload[name] = {
            "state": state,
            "why": why,
            "loc": node["loc"],
            "depth": node["depth"],
            "counts": node["counts"],
            "origin": origins.get(name, {}).get("origin", "unknown"),
            "origin_why": origins.get(name, {}).get("why", ""),
            "uses": sorted(project.imports.get(name, ())),
            "used_by": sorted(project.imported_by.get(name, ())),
            "external": sorted(project.external.get(name, ()))[:12],
            "signals": {k: [[t, ln] for t, ln in v]
                        for k, v in row.get("signals", {}).items()},
            "snippets": snippets_by_module.get(name, []),
            "deadends": deadends_by_module.get(name, []),
        }
    return svg, json.dumps(payload).replace("<", "\\u003c").replace(">", "\\u003e")


LEGEND = "".join(
    f'<span class="key"><i style="background:{fill};border-color:{stroke}"></i>'
    f'{html.escape(state)} &mdash; {html.escape(desc)}</span>'
    for state, (stroke, fill, desc) in _PALETTE.items())
