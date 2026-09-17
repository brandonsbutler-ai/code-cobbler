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

def state_for(node, name, project, deadends_by_module):
    """The state one module is in, for callers outside this module.

    The report needs the same answer the chart draws, and recomputing it from
    a different set of rules is how a legend and a picture come to disagree.
    """
    def _is_test(key):
        parts = str(key).lower().replace("-", "_").split(".")
        return any(p in ("tests", "test", "conftest") or p.startswith("test_")
                   or p.endswith("_test") for p in parts)
    tested = any(name in project.imports.get(k, ())
                 for k in project.by_dotted if _is_test(k))
    return state_of(node, tested=tested,
                    deadend=bool(deadends_by_module.get(name)))


def _fit(text, chars, keep_end=0):
    """Truncate to fit a card. The full value is on hover and in the panel.

    Sizing every card to the longest name in the project makes the whole chart
    as wide as its worst case -- one 31-character filename would widen 975
    boxes.

    `keep_end` cuts from the MIDDLE and keeps that many trailing characters,
    which matters for filenames: chopping the end turned `_e2e_phase_a_fix.py`
    and `_e2e_phase_b_rollback.py` into the same label. Measured on a
    975-module project, end-truncation at this width made 150 names
    indistinguishable from another; middle-truncation made 79, for no extra
    width.
    """
    text = str(text)
    if len(text) <= chars:
        return text
    if keep_end and chars > keep_end + 2:
        return text[:chars - keep_end - 1] + "\u2026" + text[-keep_end:]
    return text[:chars - 1] + "\u2026"


# Six bands. Colour carries meaning here, so the legend names what each one is
# DERIVED from rather than what it looks like.
_PALETTE = {
    "confirmed":  ("#33d6c8", "#0d2624",
                   "reached from a start point and exercised by a test"),
    "tested":     ("#58a6ff", "#0e1b2b",
                   "a test exercises it, but it still carries signals"),
    "live":       ("#7ee787", "#102117",
                   "reached from a start point, nothing unfinished in it"),
    "unfinished": ("#d8a657", "#241c10",
                   "reached, and carrying signals of unfinished work"),
    "deadend":    ("#ff6ec7", "#2a1220",
                   "execution reaches here and stops inside it"),
    "maybe":      ("#bc8cff", "#1b1526",
                   "no static path reaches it -- an inference, not a verdict"),
    "broken":     ("#f4796b", "#2a1614", "this file does not parse"),
}

# Every state the layout can return has an entry above, and nothing else does.
# Leftovers from the previous vocabulary sat here and were rendered into the
# legend, so the map explained nine colours while drawing six -- two of them
# saying the same thing in different words.
assert not (set(_PALETTE) - {"confirmed", "tested", "live", "unfinished",
                             "deadend", "maybe", "broken"}), _PALETTE


def _e(v):
    return html.escape("" if v is None else str(v))


def render(graph, project, frontier_by_module, snippets_by_module,
           deadends_by_module, origins, history=None, modules_by_key=None,
           attempts=None, folders=None):
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

    # Which modules a TEST actually imports. This is the fact that turns
    # "reachable" into "reached and exercised", and it is the difference
    # between a path somebody can trust and one that merely parses.
    def _is_test(key):
        parts = str(key).lower().replace("-", "_").split(".")
        return any(p in ("tests", "test", "conftest") or p.startswith("test_")
                   or p.endswith("_test") for p in parts)

    tested = set()
    for key in project.by_dotted:
        if _is_test(key):
            tested |= set(project.imports.get(key, ()))

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

    # The overview draws folders, not flow. Positions and card sizes come
    # from the folder layout; `nodes` keeps supplying depth and the counts,
    # which are properties of the module and not of where it is drawn.
    placed = (folders or {}).get("nodes") or {}

    # The continuation survives the loss of its line, as a mark on the card
    # that stopped. A dashed path across a 1,240 x 13,000 chart could only be
    # followed by eye, which is the thing being taken out; a mark says "this
    # one carries on somewhere" in place, and the panel names the file.
    continues_from = {}
    for group in attempts:
        winner = group.get("resume_at")
        for attempt in group.get("attempts", []):
            if attempt["module"] != winner:
                continues_from[attempt["module"]] = {
                    "to": winner, "relpath": group.get("resume_relpath", ""),
                    "shared": list(group.get("shared", []))[:4]}

    node_svg = []
    cards = {}
    for name, node in sorted(nodes.items()):
        state, why = state_of(node, tested=name in tested,
                              deadend=bool(deadends_by_module.get(name)))
        stroke, fill, _ = _PALETTE[state]
        label = name.rsplit(".", 1)[-1][:24]
        prefix = name.rsplit(".", 1)[0][:26] if "." in name else ""
        marks = sum(node["counts"].values())
        origin = origins.get(name, {}).get("origin", "")
        badge_origin = origin if origin in ("untracked", "vendored") else ""
        module = modules_by_key.get(name)
        relpath = getattr(module, "relpath", name.replace(".", "/") + ".py")
        filename = relpath.rsplit("/", 1)[-1]
        location = relpath.rsplit("/", 1)[0] + "/" if "/" in relpath else "./"
        loc = getattr(module, "loc", 0)
        size = f"{loc:,} lines" if loc else "-"
        record = hist_files.get(relpath, {})
        authors = record.get("authors") or []
        owner = authors[0] if authors else "no history"
        # Kept as drawn, keyed by module. The payload is assembled in a
        # SECOND loop below, where these names still exist but hold the last
        # card drawn -- every module briefly carried the same card because of
        # exactly that.
        cards[name] = {
            "name": _fit(filename, 16, keep_end=7),
            "location": _fit(location, 19),
            "meta": _fit(size + "  \u00b7  " + owner, 19),
            "marks": marks,
            "title": f"{relpath} \u00b7 {size} \u00b7 {owner}",
            "stroke": stroke,
        }
        carries_on = continues_from.get(name)
        spot = placed.get(name)
        x = spot["x"] if spot else node["x"]
        y = spot["y"] if spot else node["y"]
        w = spot["w"] if spot else NODE_W
        h = spot["h"] if spot else NODE_H
        compact = bool(spot and spot["small"])
        node_svg.append(
            f'<g class="node" data-name="{_e(name)}" data-state="{state}" '
            + (f'data-continues="{_e(carries_on["to"])}" ' if carries_on else "")
            + f'tabindex="0" role="button" aria-label="{_e(name)}: {_e(why)}">'
            # The exact path on hover. A truncated label should never be the
            # last word on which file a card is -- on a 975-module project 79
            # of them are still not unique after middle-truncation.
            f'<title>{_e(relpath)} &#183; {_e(size)} &#183; {_e(owner)}</title>'
            # The fill is the panel's own background, so the card reads as a
            # cut-out and the glowing edge is what carries the state.
            f'<rect class="card" x="{x}" y="{y}" width="{w}" '
            f'height="{h}" rx="8" stroke="{stroke}"/>'
            # A short module gets a short card with its name on it and
            # nothing else. The folder is drawn around it now, and the line
            # count is the width -- so on a compact card both of those lines
            # were repeating what the shape already said.
            + (f'<text class="fname" x="{x + 9}" y="{y + 22}">'
               f'{_e(_fit(filename, int((w - 18) / 7.4), keep_end=7))}</text>'
               if compact else
               f'<text class="fname" x="{x + 9}" y="{y + 22}">'
               f'{_e(_fit(filename, int((w - 18) / 7.4), keep_end=7))}</text>'
               f'<text class="meta" x="{x + 9}" y="{y + 40}">'
               f'{_e(_fit(location, int((w - 18) / 6.2)))}</text>'
               # Size and owner share a line: two facts, one row, and the
               # card loses a quarter of its height.
               f'<text class="meta owner" x="{x + 9}" y="{y + 56}">'
               f'{_e(_fit(size + "  \u00b7  " + owner, int((w - 18) / 6.2)))}</text>')
            + (f'<text class="badge" x="{x + w - 9}" y="{y + h - 8}" '
               f'text-anchor="end">{badge_origin}</text>'
               if badge_origin and not compact else "")
            + (f'<text class="marks" x="{x + w - 9}" '
               f'y="{y + 20}" text-anchor="end">{marks}</text>'
               if marks else "")
            + (f'<text class="continues-mark" x="{x + w - 10}" '
               f'y="{y + h - 7}" text-anchor="end">&#8594;&#8230;</text>'
               f'<title>this one stops; {_e(carries_on["relpath"])} is doing '
               f'the same work ({_e(", ".join(carries_on["shared"]))})</title>'
               if carries_on else "")
            + "</g>")

    edge_svg.extend(continuation_svg)

    # A rule across the chart where the connected part ends, labelled. Without
    # it the pool below reads as deeper levels of the same tree.
    if not folders and graph.get("detached_y") is not None:
        y = graph["detached_y"]
        edge_svg.append(
            f'<line class="cut" x1="8" y1="{y:.0f}" '
            f'x2="{graph["width"] - 8}" y2="{y:.0f}"/>'
            f'<text class="cutlabel" x="14" y="{y - 7:.0f}">'
            f'nothing above reaches the {graph["detached_count"]} below</text>')

    # The folder boxes. Drawn first so every card sits on top of its own
    # box, and labelled with the two numbers that decide where to look: how
    # many modules are in there, and how many lines.
    box_svg = []
    for box in (folders or {}).get("boxes", []):
        box_svg.append(
            f'<g class="folder" data-folder="{_e(box["folder"])}">'
            f'<rect class="fbox" x="{box["x"]}" y="{box["y"]}" '
            f'width="{box["w"]}" height="{box["h"]}" rx="10"/>'
            f'<text class="fname-lbl" x="{box["x"] + 12}" '
            f'y="{box["y"] + 17}">{_e(box["folder"])}</text>'
            f'<text class="fmeta" x="{box["x"] + box["w"] - 12}" '
            f'y="{box["y"] + 17}" text-anchor="end">'
            f'{box["modules"]:,} file{"s" if box["modules"] != 1 else ""}'
            f' &#183; {box["loc"]:,} lines</text></g>')

    if folders:
        # No connections in the overview. Every one of them crossed the whole
        # chart and had to be followed by eye, which is the thing the reader
        # said was hard; a module's own connections are one click away in its
        # trace, laid out short enough to read.
        edge_svg = []
        continuation_svg = []
        width, height = folders["width"], folders["height"]
    else:
        width, height = graph["width"], graph["height"]

    svg = f"""<svg id="graph" viewBox="0 0 {width} {height}"
     width="{width}" height="{height}"
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
  <g class="folders">{''.join(box_svg)}</g>
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
                    "otherModule": winner,
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
                    "otherModule": winner,
                }

    payload = {}
    for name, node in nodes.items():
        state, why = state_of(node, tested=name in tested,
                              deadend=bool(deadends_by_module.get(name)))
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
            # Why this is not in the orphan list even though no import names
            # it. Without this the map states an exclusion it cannot justify.
            # Everything needed to redraw this card somewhere else, already
            # truncated HERE. The trace view draws the same cards from the
            # same strings; a second copy of _fit() in JavaScript is two
            # truncation rules that agree until the day one of them changes.
            "card": cards.get(name),
            "loaded_by": (getattr(project, "convention_reached", {})
                          .get(name).as_dict()
                          if (getattr(project, "convention_reached", {})
                              .get(name)) else None),
            "signals": {k: [[t, ln] for t, ln in v]
                        for k, v in row.get("signals", {}).items()},
            "snippets": snippets_by_module.get(name, []),
            "deadends": deadends_by_module.get(name, []),
        }
    return svg, json.dumps(payload).replace("<", "\\u003c").replace(">", "\\u003e")


# One key, two sizes. The same entries carry their sentence until the key is
# pinned under the title bar, at which point the sentences are taken off and
# what is left is the swatch and the word -- which is all "what was orange
# again?" needs. Writing the words twice, once in a strip of chips and once in
# a block of definitions underneath, said the same thing in the same place and
# made the reader check whether the two agreed.
#
# Built from _PALETTE. A key written next to a palette goes stale the first
# time a colour moves, and this project has already shipped one that did.
_LINK_CHIP = (
    '<span class="chip static">'
    '<i style="background:#2a1220;border-color:#ff6ec7;color:#ff6ec7">'
    '&#8594;</i>continuation'
    '<span class="def">&mdash; a card marked &#8594;&#8230; stopped, and '
    'another file is doing the same work; click it for the name (inferred '
    'from shared definition names)</span></span>')

KEYBAR = _LINK_CHIP + "".join(
    f'<button type="button" class="chip" data-state="{state}" '
    f'aria-pressed="false">'
    f'<i style="background:{fill};border-color:{stroke}"></i>'
    f'{html.escape(state)}'
    f'<span class="def">&mdash; {html.escape(desc)}</span></button>'
    for state, (stroke, fill, desc) in _PALETTE.items())
