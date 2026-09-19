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

from collections import Counter
import html
import json

from .layout import BAR_H, NODE_H, NODE_W, state_of

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


def _meta_line(size, owner, w, badge, continues):
    """The size-and-owner row, fitted to what the right-hand marks leave."""
    # Chromium's widths: the 8.5px spaced badge is 7px a character, the
    # continuation arrow about 20px; the 11px row is 6.2px a character.
    reserve = (7 * len(badge) + 8 if badge else 0) + (22 if continues else 0)
    chars = int((w - 18 - reserve) / 6.2)
    text = size if badge else size + "  \u00b7  " + owner
    if badge and len(text) > chars:
        text = size.split()[0]            # "12,345", not "12,345 li…"
    return _fit(text, chars)


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


# Worst-first. When the modules at one end of a ribbon are split evenly between
# conditions, the tie goes to the more alarming one: a ribbon that hides trouble
# behind an equally-common healthy state is worse than one that overstates it.
_SEVERITY = ("broken", "deadend", "unfinished", "maybe", "tested", "live",
             "confirmed")


# A state missing here would fall through to arbitrary dict order, making a
# ribbon's colour nondeterministic on a tie. Guarded like _PALETTE above.
assert set(_SEVERITY) == set(_PALETTE), (set(_SEVERITY) ^ set(_PALETTE))


def _dominant(state_by_module, names):
    """The condition most of these modules are in, worst-first on a tie."""
    counts = Counter(state_by_module.get(n) for n in names)
    counts.pop(None, None)
    if not counts:
        return None
    top = max(counts.values())
    for state in _SEVERITY:
        if counts.get(state) == top:
            return state
    return next(iter(counts))


# How many evidence lines a connection shows before it starts counting. The
# median edge on the corpus needs 3; a test file calling its subject 99 times
# needs a limit. 8 covers 85% of edges outright.
EVIDENCE_LIMIT = 8


def cap_evidence(lines, limit=EVIDENCE_LIMIT):
    """The first few evidence lines, and how many were held back.

    Truncating in silence is the behaviour this tool exists to argue against,
    so the count travels with the lines instead of the remainder vanishing.
    """
    return {"lines": lines[:limit], "more": max(0, len(lines) - limit)}


def folder_ribbons(project, folders, state_by_module):
    """One ribbon per ordered folder pair that carries imports.

    The overview used to draw an edge per import. Every one of them crossed the
    whole chart and had to be followed by eye, so they were cut. A ribbon is the
    aggregate: the 1104 imports of the 977-module corpus become 41 ribbons,
    which is a number a reader can actually follow.

    Imports WITHIN one folder are not ribbons -- they would start and end in the
    same box and say nothing at this scale. A module's own connections are in
    its trace, one click away.
    """
    where = {name: node["folder"]
             for name, node in (folders.get("nodes") or {}).items()}
    pairs = {}
    for source, targets in (project.imports or {}).items():
        src_folder = where.get(source)
        if src_folder is None:
            continue
        for target in targets:
            dst_folder = where.get(target)
            if dst_folder is None or dst_folder == src_folder:
                continue
            pairs.setdefault((src_folder, dst_folder), []).append((source, target))

    ribbons = []
    for (src_folder, dst_folder), links in pairs.items():
        ribbons.append({
            "src": src_folder, "dst": dst_folder, "count": len(links),
            "src_state": _dominant(state_by_module, (a for a, _b in links)),
            "dst_state": _dominant(state_by_module, (b for _a, b in links)),
        })
    return ribbons


def _border_point(box, toward_x, toward_y):
    """Where a line from this box's centre toward a point leaves the box.

    Ribbons attach to the border rather than the centre, so a ribbon reads as
    leaving a folder instead of appearing from under its cards.
    """
    cx, cy = box["x"] + box["w"] / 2.0, box["y"] + box["h"] / 2.0
    dx, dy = toward_x - cx, toward_y - cy
    if not dx and not dy:
        return cx, cy
    half_w, half_h = box["w"] / 2.0 + 3, box["h"] / 2.0 + 3
    scale = min(half_w / abs(dx) if dx else float("inf"),
                half_h / abs(dy) if dy else float("inf"))
    return cx + dx * scale, cy + dy * scale


def _ribbon_svg(ribbons, folders):
    """(paths, gradient defs) for the folder ribbons.

    Each ribbon gets its OWN gradient in userSpaceOnUse units, for the reason
    the trace veins do: a shared objectBoundingBox gradient is dropped outright
    on a path whose bounding box has zero width or height, which is every
    ribbon between two boxes on the same shelf or in the same column.

    The paint is an INLINE STYLE. A presentation attribute loses to any
    stylesheet declaration, and this svg carries class="chart".
    """
    import math
    boxes = {box["folder"]: box for box in (folders or {}).get("boxes", [])}
    paths, defs = [], []
    ordered = sorted(ribbons, key=lambda r: (-r["count"], r["src"], r["dst"]))
    for index, ribbon in enumerate(ordered):
        src, dst = boxes.get(ribbon["src"]), boxes.get(ribbon["dst"])
        if src is None or dst is None:
            continue
        if ribbon["src_state"] is None or ribbon["dst_state"] is None:
            continue
        sx, sy = _border_point(src, dst["x"] + dst["w"] / 2.0,
                               dst["y"] + dst["h"] / 2.0)
        ex, ey = _border_point(dst, src["x"] + src["w"] / 2.0,
                               src["y"] + src["h"] / 2.0)
        # Bowed perpendicular to its own direction, so A->B and B->A separate
        # instead of lying on top of each other as one ambiguous line.
        dx, dy = ex - sx, ey - sy
        span = math.hypot(dx, dy) or 1.0
        bow = min(64.0, span * 0.16)
        mx = (sx + ex) / 2.0 - dy / span * bow
        my = (sy + ey) / 2.0 + dx / span * bow
        # Weight says how much passes through it, log-scaled: a 300-import
        # dependency is heavier than a 3-import one but not a hundred times so.
        weight = min(6.0, 1.3 + math.log2(ribbon["count"] + 1) * 0.9)
        gid = (f"ribbon_{ribbon['src_state']}_{ribbon['dst_state']}_{index}")
        defs.append(
            f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" '
            f'x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}">'
            f'<stop offset="0" stop-color="{_PALETTE[ribbon["src_state"]][0]}"/>'
            f'<stop offset="1" stop-color="{_PALETTE[ribbon["dst_state"]][0]}"/>'
            f"</linearGradient>")
        paths.append(
            f'<path class="ribbon" style="stroke:url(#{gid})" '
            f'stroke-width="{weight:.1f}" '
            f'data-src="{_e(ribbon["src"])}" data-dst="{_e(ribbon["dst"])}" '
            f'data-count="{ribbon["count"]}" '
            f'd="M{sx:.1f},{sy:.1f} Q{mx:.1f},{my:.1f} {ex:.1f},{ey:.1f}" '
            f'marker-end="url(#rarrow)"><title>{_e(ribbon["src"])} imports '
            f'{_e(ribbon["dst"])} &#183; {ribbon["count"]} '
            f'time{"s" if ribbon["count"] != 1 else ""}</title></path>')
    return paths, defs


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
    # Kept so the ribbons can be coloured by the conditions at their ends
    # without computing state_of a second time and risking a divergent answer.
    state_by_module = {}
    for name, node in sorted(nodes.items()):
        state, why = state_of(node, tested=name in tested,
                              deadend=bool(deadends_by_module.get(name)))
        state_by_module[name] = state
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
        carries_on = continues_from.get(name)
        spot = placed.get(name)
        x = spot["x"] if spot else node["x"]
        y = spot["y"] if spot else node["y"]
        w = spot["w"] if spot else NODE_W
        h = spot["h"] if spot else NODE_H
        share = spot["share"] if spot else 0.0
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
            "share": share,
        }
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
            f'<text class="fname" x="{x + 9}" y="{y + 21}">'
            f'{_e(_fit(filename, int((w - 18) / 7.4), keep_end=7))}</text>'
            f'<text class="meta" x="{x + 9}" y="{y + 38}">'
            f'{_e(_fit(location, int((w - 18) / 6.2)))}</text>'
            # Size and owner share a line: two facts, one row, and the card
            # loses a quarter of its height. A badge or a continuation mark
            # sits at the right-hand end of this same row, so the text stops
            # short of it -- measured in Chromium, "untracked" was drawn over
            # "624 lines · no his…" on 18 of 55 cards. With a badge the line
            # is the size alone; the owner is still in the hover.
            f'<text class="meta owner" x="{x + 9}" y="{y + 53}">'
            f'{_e(_meta_line(size, owner, w, badge_origin, carries_on))}</text>'
            # How long the module is, as a length rather than as a shape.
            # Every bar starts at the same x on every card, so a column of
            # them reads like a chart. Sizing each card individually encoded
            # the same fact, lined up with nothing, and read as a badly built
            # brick wall.
            f'<rect class="barbed" x="{x + 9}" y="{y + h - 12}" '
            f'width="{w - 18}" height="{BAR_H}" rx="1.5"/>'
            + (f'<rect class="bar" x="{x + 9}" y="{y + h - 12}" '
               f'width="{max(2, round((w - 18) * share)):.0f}" '
               f'height="{BAR_H}" rx="1.5" fill="{stroke}"/>' if share else "")
            + (f'<text class="badge" x="{x + w - 9 - (22 if carries_on else 0)}" '
               f'y="{y + h - 18}" text-anchor="end">{badge_origin}</text>'
               if badge_origin else "")
            + (f'<text class="marks" x="{x + w - 9}" '
               f'y="{y + 19}" text-anchor="end">{marks}</text>'
               if marks else "")
            + (f'<text class="continues-mark" x="{x + w - 10}" '
               f'y="{y + h - 18}" text-anchor="end">&#8594;&#8230;</text>'
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

    ribbon_svg, ribbon_defs = [], []
    if folders:
        # Still no MODULE-level edges here: every one of them crossed the whole
        # chart and had to be followed by eye, which is the thing the reader
        # said was hard. A module's own connections are one click away in its
        # trace, laid out short enough to read.
        #
        # What the overview draws instead is the AGGREGATE -- one ribbon per
        # ordered folder pair. The corpus's 1104 imports become 41 ribbons,
        # drawn behind the boxes so they can never obscure a card.
        edge_svg = []
        continuation_svg = []
        width, height = folders["width"], folders["height"]
        ribbon_svg, ribbon_defs = _ribbon_svg(
            folder_ribbons(project, folders, state_by_module), folders)
    else:
        width, height = graph["width"], graph["height"]

    svg = f"""<svg id="graph" class="chart" viewBox="0 0 {width} {height}"
     width="{width}" height="{height}"
     xmlns="http://www.w3.org/2000/svg" role="img"
     aria-label="module dependency map">
  <defs>
    <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7"
            markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z"/></marker>
    <!-- A marker is scaled by the stroke it ends, and a ribbon's stroke is
         its weight: #arrow on a 4px ribbon was 28px across. -->
    <marker id="rarrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="2.6"
            markerHeight="2.6" orient="auto"><path d="M0,0 L8,4 L0,8 z"/></marker>
    <marker id="stub" viewBox="0 0 8 8" refX="5" refY="4" markerWidth="8"
            markerHeight="8" orient="auto"><path d="M1,0 L1,8 M4,0 L4,8"
            stroke-width="1.6" fill="none"/></marker>
    <marker id="continues" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="9" markerHeight="9" orient="auto"><path
            d="M0,1 L9,5 L0,9" fill="none" stroke-width="1.4"/></marker>
    {''.join(ribbon_defs)}
  </defs>
  <g class="folders">{''.join(box_svg)}</g>
  <g class="ribbons">{''.join(ribbon_svg)}</g>
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
            # The lines that show HOW this module reaches each one it uses.
            # Outgoing edges only: the evidence for "used by X" is in X's own
            # source, so it is carried once, on X, rather than twice.
            "links": {target: cap_evidence(project.evidence_for(name, target))
                      for target in sorted(project.imports.get(name, ()))},
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
#
# A swatch is the EDGE colour, which is what a card carries -- the palette's
# fill is near-black and drew a row of black squares in the light theme. The
# continuation is a mark on the map, not a state a card is in, so its swatch
# is the mark itself: had it the dead end's box, the two read as one thing.
_LINK_CHIP = (
    '<span class="chip static">'
    '<i style="background:transparent;border-color:transparent;color:#ff6ec7;'
    'font-weight:700;line-height:11px;text-align:center">'
    '&#8594;</i>continuation'
    '<span class="def">&mdash; a card marked &#8594;&#8230; stopped, and '
    'another file is doing the same work; click it for the name (inferred '
    'from shared definition names)</span></span>')

KEYBAR = _LINK_CHIP + "".join(
    f'<button type="button" class="chip" data-state="{state}" '
    f'aria-pressed="false">'
    f'<i style="background:{stroke};border-color:{stroke}"></i>'
    f'{html.escape(state)}'
    f'<span class="def">&mdash; {html.escape(desc)}</span></button>'
    for state, (stroke, _fill, desc) in _PALETTE.items())
