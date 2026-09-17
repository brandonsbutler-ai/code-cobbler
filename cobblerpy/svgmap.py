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

def _fit(text, chars):
    """Truncate to fit a card. The full value is in the panel, one click away.

    Sizing every card to the longest name in the project makes the whole chart
    as wide as its worst case -- one 31-character filename would widen 109
    boxes.
    """
    text = str(text)
    return text if len(text) <= chars else text[:chars - 1] + "\u2026"


_PALETTE = {
    "clean":     ("#2d6a4f", "#e7f2ec", "reachable, no unfinished-work signals"),
    "warm":      ("#8a5a00", "#fdf4e3", "carries signals of unfinished work"),
    "hot":       ("#a13d2d", "#fbecea", "several signals of unfinished work"),
    "broken":    ("#7b2d26", "#f7dedb", "this file does not parse"),
    "orphan":    ("#5a4b8a", "#efecf8", "nothing imports it, nothing starts from it"),
    "unreached": ("#6b6b66", "#ececea", "no static path from an entry point (inference)"),
    # Execution reaches this module and stops inside it. Not the same fact as
    # "carries signals of unfinished work", so not the same colour: this is
    # the boundary where somebody put the pen down.
    "deadend": ("#b0247f", "#fbe9f4",
                "execution reaches here and stops"),
}


def _e(v):
    return html.escape("" if v is None else str(v))


def render(graph, project, frontier_by_module, snippets_by_module,
           deadends_by_module, origins, history=None, modules_by_key=None,
           attempts=None):
    """SVG plus the JSON payload the panel reads. Returns (svg, payload).

    A node carries four facts -- filename, where it lives, how big it is and
    who last touched it -- on a card whose FILL MATCHES THE PANEL BEHIND IT.
    Filled boxes made the graph a wall of colour with the state fighting the
    text for attention; a cut-out with a glowing edge lets the edge carry the
    state and leaves the middle legible.
    """
    nodes, edges = graph["nodes"], graph["edges"]
    history = history or {}
    modules_by_key = modules_by_key or {}
    hist_files = history.get("files", {}) if isinstance(history, dict) else {}
    attempts = attempts or []

    edge_svg = []
    for edge in edges:
        stub = bool(deadends_by_module.get(edge["to"]))
        cls = "edge deadend" if stub else ("edge back" if edge["back"] else "edge")
        edge_svg.append(
            f'<path class="{cls}" d="{edge["path"]}" '
            f'data-from="{_e(edge["from"])}" data-to="{_e(edge["to"])}" '
            f'marker-end="url(#{"stub" if stub else "arrow"})"/>')

    # Where the flow stops, and where it probably carries on.
    #
    # An import edge says "this calls that". These say something different and
    # are drawn differently for it: the module went as far as it went, and
    # ANOTHER file is doing the same work -- matched on shared definition
    # names, not on style. It is the one link on this map that is a
    # hypothesis, so it is dashed, and the reason travels with it.
    continuation_svg = []
    for group in attempts:
        winner = group.get("resume_at")
        target = nodes.get(winner)
        if not target:
            continue
        for attempt in group.get("attempts", []):
            source = nodes.get(attempt["module"])
            if source is None or attempt["module"] == winner:
                continue
            x1 = source["x"] + NODE_W / 2
            y1 = source["y"] + NODE_H / 2
            x2 = target["x"] + NODE_W / 2
            y2 = target["y"] + NODE_H / 2
            # A curve here would read as a network edge. The map is a
            # hierarchy now, so this follows the same elbow idiom as the
            # import edges and is told apart by being dashed, not by shape.
            bend = (y1 + y2) / 2
            continuation_svg.append(
                f'<path class="continues" d="M{x1:.0f},{y1:.0f} '
                f'L{x1:.0f},{bend:.0f} L{x2:.0f},{bend:.0f} '
                f'L{x2:.0f},{y2:.0f}" '
                f'data-from="{_e(attempt["module"])}" data-to="{_e(winner)}" '
                f'marker-end="url(#continues)"><title>'
                f'{_e(attempt["relpath"])} and {_e(group["resume_relpath"])} '
                f'define the same things '
                f'({_e(", ".join(group["shared"][:4]))}); '
                f'{_e(group["resume_relpath"])} is further along'
                f'</title></path>')

    node_svg = []
    for name, node in sorted(nodes.items()):
        state, why = state_of(node)
        # A dead end is not the same as "carries signals". Execution reaches
        # this module and stops inside it, which is a different fact and gets
        # its own colour.
        if deadends_by_module.get(name):
            state = "deadend"
            why = "execution reaches here and stops"
        stroke, fill, _ = _PALETTE[state]
        label = name.rsplit(".", 1)[-1][:24]
        prefix = name.rsplit(".", 1)[0][:26] if "." in name else ""
        marks = sum(node["counts"].values())
        origin = origins.get(name, {}).get("origin", "")
        badge = ""
        if origin in ("untracked", "vendored"):
            badge = (f'<text class="badge" x="{node["x"] + NODE_W - 9}" '
                     f'y="{node["y"] + NODE_H - 8}" text-anchor="end">'
                     f'{origin}</text>')
        module = modules_by_key.get(name)
        relpath = getattr(module, "relpath", name.replace(".", "/") + ".py")
        filename = relpath.rsplit("/", 1)[-1]
        location = relpath.rsplit("/", 1)[0] + "/" if "/" in relpath else "./"
        loc = getattr(module, "loc", 0)
        size = f"{loc:,} lines" if loc else "-"
        record = hist_files.get(relpath, {})
        authors = record.get("authors") or []
        owner = authors[0] if authors else "no history"
        x, y = node["x"], node["y"]
        node_svg.append(
            f'<g class="node" data-name="{_e(name)}" data-state="{state}" '
            f'tabindex="0" role="button" aria-label="{_e(name)}: {_e(why)}">'
            # The fill is the panel's own background, so the card reads as a
            # cut-out and the glowing edge is what carries the state.
            f'<rect class="card" x="{x}" y="{y}" width="{NODE_W}" '
            f'height="{NODE_H}" rx="8" stroke="{stroke}"/>'
            f'<text class="fname" x="{x + 9}" y="{y + 22}">'
            f'{_e(_fit(filename, 16))}</text>'
            f'<text class="meta" x="{x + 9}" y="{y + 40}">'
            f'{_e(_fit(location, 19))}</text>'
            # Size and owner share a line: two facts, one row, and the card
            # loses a quarter of its height.
            f'<text class="meta owner" x="{x + 9}" y="{y + 56}">'
            f'{_e(_fit(size + "  \u00b7  " + owner, 19))}</text>'
            f'{badge}'
            + (f'<text class="marks" x="{x + NODE_W - 9}" '
               f'y="{y + 20}" text-anchor="end">{marks}</text>'
               if marks else "")
            + "</g>")

    edge_svg.extend(continuation_svg)

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
    <marker id="continues" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="9" markerHeight="9" orient="auto"><path
            d="M0,1 L9,5 L0,9" fill="none" stroke-width="1.4"/></marker>
  </defs>
  <g class="edges">{''.join(edge_svg)}</g>
  <g class="nodes">{''.join(node_svg)}</g>
</svg>"""

    # What each card has to be able to answer when it is clicked: either
    # "execution reaches here and stops, here is the line", or "this stopped
    # and that one carries on, here is what they share". A card that opens a
    # panel of facts without leading with the reason makes the reader do the
    # joining, and the reason is the only part they cannot reconstruct.
    verdicts = {}
    for group in attempts:
        winner = group.get("resume_at")
        for attempt in group.get("attempts", []):
            key = attempt["module"]
            if key == winner:
                verdicts[key] = {
                    "kind": "resume",
                    "headline": "this is where it goes",
                    "detail": (f"furthest along of {len(group['attempts'])} "
                               f"attempts at this job -- "
                               f"{attempt['percent']}% of what it started"),
                    "shared": group.get("shared", [])[:6],
                    "facts": attempt.get("facts", []),
                    "other": group.get("resume_relpath", ""),
                }
            else:
                verdicts[key] = {
                    "kind": "superseded",
                    "headline": "another attempt got further",
                    "detail": (f"{attempt['percent']}% of what it started; "
                               f"{group.get('resume_relpath', '')} is at "
                               f"{group.get('resume_percent', 0)}%"),
                    "shared": group.get("shared", [])[:6],
                    "facts": attempt.get("facts", []),
                    "other": group.get("resume_relpath", ""),
                }

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
            "verdict": verdicts.get(name),
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


# The dashed link has to be in the legend too. A line on a diagram that
# nothing explains is worse than no line: the reader either ignores it or
# invents a meaning for it, and this one is the only INFERENCE on the map.
_LINK_KEY = (
    '<span class="key"><svg width="26" height="10" aria-hidden="true">'
    '<path d="M1,5 L20,5" stroke="#ff6ec7" stroke-width="1.6" fill="none" '
    'stroke-dasharray="5 4"/><path d="M19,2 L25,5 L19,8" stroke="#ff6ec7" '
    'stroke-width="1.4" fill="none"/></svg>'
    'probable continuation &mdash; this stopped, and that one is doing the '
    'same work (inferred from shared definition names)</span>')

LEGEND = _LINK_KEY + "".join(
    f'<span class="key"><i style="background:{fill};border-color:{stroke}"></i>'
    f'{html.escape(state)} &mdash; {html.escape(desc)}</span>'
    for state, (stroke, fill, desc) in _PALETTE.items())
