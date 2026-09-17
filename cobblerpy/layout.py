"""Turn the module graph into coordinates, without a graph library.

A layered (Sugiyama-style) layout in three stages, of which the first is
already done elsewhere:

    1. LAYER ASSIGNMENT -- Project.layers() gives depth from the entry points,
       which is exactly this. Modules no entry point reaches get their own
       trailing layer rather than being dropped, because "nothing reaches this"
       is one of the findings a reader most needs to see.

    2. ORDERING within each layer, to cut edge crossings. The barycentre
       heuristic: repeatedly place each node at the average position of its
       neighbours in the adjacent layer and re-sort. A handful of sweeps gets
       most of the benefit; the optimum is NP-hard and nobody looking at the
       picture can tell the difference.

    3. COORDINATES -- x from layer, y from order, with edges routed as cubic
       curves so two edges between the same pair of layers stay distinguishable.

Graphviz would do this better. It would also be a dependency, and the whole
point of this tool is that it reads somebody's private source without dragging
anything onto the machine.
"""

from collections import defaultdict

# Geometry, in SVG user units.
# A card holds three lines, not four: the filename, where it lives, and its
# size and owner together. A 210x74 card was mostly empty -- an eleven
# character filename in a box sized for thirty-one -- and eight of them plus a
# 108px gutter came to 2,492px, which is a horizontal scrollbar on any screen.
#
# At 152x54 with a 30px gutter, eight fit in 1,482px and the scrollbar goes.
# Long names truncate rather than widen every card in the chart to suit the
# worst one: the full path is in the panel a click away.
# Card-shaped: narrower than it is tall-ish, rather than a wide strip.
#
# Width is what decides whether a chart fits, because it is the dimension that
# does not scroll. Taking another 20% off the width and giving a little of it
# back as height buys two more columns per row at the same page width -- which
# is the difference between a 25-module project fitting in three rows and a
# 1,000-module one being readable at all.
NODE_W = 138
NODE_H = 66
X_GAP = 24          # horizontal space between layers
Y_GAP = 30           # vertical space between nodes in a layer
MARGIN = 28


def _order_layers(layers, edges, sweeps=6):
    """Barycentre ordering, sweeping forwards and backwards."""
    order = {d: list(names) for d, names in layers.items()}
    depths = sorted(order)
    if len(depths) < 2:
        return order

    succ = defaultdict(list)
    pred = defaultdict(list)
    for a, b in edges:
        succ[a].append(b)
        pred[b].append(a)

    for sweep in range(sweeps):
        forward = sweep % 2 == 0
        seq = depths[1:] if forward else depths[-2::-1]
        for depth in seq:
            neighbours = pred if forward else succ
            ref_depth = depth - 1 if forward else depth + 1
            ref = {name: i for i, name in enumerate(order.get(ref_depth, []))}
            if not ref:
                continue

            def bary(name, _ref=ref, _nb=neighbours):
                positions = [_ref[n] for n in _nb.get(name, ()) if n in _ref]
                # A node with no neighbour in the reference layer keeps its
                # place rather than being dragged to the top, which would
                # scatter unrelated modules through the picture every sweep.
                return sum(positions) / len(positions) if positions else 1e9

            order[depth] = sorted(order[depth], key=lambda n: (bary(n), n))
    return order


def compute(project, frontier_by_module=None):
    """Positions for every module, plus routed edges.

    Returns a dict with `nodes`, `edges`, `width` and `height`, ready to render.
    """
    frontier_by_module = frontier_by_module or {}
    layers = dict(project.layers())

    placed = {n for names in layers.values() for n in names}
    unreached = sorted(set(project.by_dotted) - placed)
    if unreached:
        # Appended as further rows, these read as DEEPER LEVELS of the same
        # tree -- rows of grey that look like the flow petering out, and then
        # colour "picking back up" four rows down. Nothing above reaches any
        # of them. They are a separate pool, and the gap below says so.
        layers[max(layers, default=-1) + 1] = unreached

    edges = [(a, b) for a, targets in project.imports.items() for b in targets
             if a in placed or a in unreached]
    order = _order_layers(layers, edges)

    nodes = {}
    # TOP DOWN, like an org chart: depth is a ROW, and the modules at that
    # depth spread across it. Left-to-right put depth on the x axis, which is
    # the conventional dependency-graph reading but not the one people already
    # know -- everybody can read an org chart without being told how, and the
    # thing being shown here is the same shape: what sits under what.
    # A row WRAPS past this many cards.
    #
    # Turning the chart top-down turned the long dimension from height into
    # width, and on a 109-module project the unreached row alone is 64 cards:
    # the first version produced a chart 20,300 pixels wide. Height scrolls
    # naturally and width does not, so a wide row becomes several stacked
    # lines at the same depth.
    # Ten per row rather than eight: the narrower card pays for two more
    # columns inside the same page width, so a long row becomes fewer lines.
    # Eight per row. The chart column is two thirds of the page now, so a
    # ten-wide chart overflowed it and brought the horizontal scrollbar back.
    ROW_MAX = 8

    widest = min(max((len(v) for v in order.values()), default=1), ROW_MAX)
    row_index, placed_rows = {}, 0
    detached_from = (max(order) if unreached and len(order) > 1 else None)
    for depth in sorted(order):
        if depth == detached_from:
            placed_rows += 1          # a blank row, so the pool reads as apart
        row_index[depth] = placed_rows
        placed_rows += max(1, -(-len(order[depth]) // ROW_MAX))   # ceil

    for depth in sorted(order):
        names = order[depth]
        for i, name in enumerate(names):
            line, column = divmod(i, ROW_MAX)
            # Centre each LINE horizontally, so a short line sits under the
            # middle of the one above rather than hard against the margin.
            in_line = min(len(names) - line * ROW_MAX, ROW_MAX)
            offset = (widest - in_line) * (NODE_W + X_GAP) / 2
            row = frontier_by_module.get(name, {})
            nodes[name] = {
                "name": name,
                "x": MARGIN + offset + column * (NODE_W + X_GAP),
                "y": MARGIN + (row_index[depth] + line) * (NODE_H + Y_GAP),
                "depth": depth,
                "score": row.get("score", 0),
                "counts": row.get("counts", {}),
                "loc": row.get("loc", 0),
                "unreached": name in unreached,
                "orphan": name in project.orphans,
                "entry": depth == 0,
            }

    routed = []
    for a, b in edges:
        if a not in nodes or b not in nodes:
            continue
        src, dst = nodes[a], nodes[b]
        back = dst["depth"] <= src["depth"]
        # Elbow connectors, not curves: down out of the parent, across, and
        # down into the child. That right-angled shape is what makes a diagram
        # read as a hierarchy -- a bezier between two boxes reads as a network,
        # which is the thing this is trying not to look like.
        x1, y1 = src["x"] + NODE_W / 2, src["y"] + NODE_H
        x2, y2 = dst["x"] + NODE_W / 2, dst["y"]
        if back:
            # A cycle or a jump upwards. Leave from the top and arrive at the
            # bottom, so it visibly goes against the grain of the chart.
            y1 = src["y"]
            y2 = dst["y"] + NODE_H
        mid = (y1 + y2) / 2
        routed.append({
            "from": a, "to": b,
            "path": (f"M{x1:.0f},{y1:.0f} L{x1:.0f},{mid:.0f} "
                     f"L{x2:.0f},{mid:.0f} L{x2:.0f},{y2:.0f}"),
            "back": back,
        })

    width = MARGIN * 2 + widest * (NODE_W + X_GAP) - X_GAP
    height = MARGIN * 2 + placed_rows * (NODE_H + Y_GAP) - Y_GAP
    return {"nodes": nodes, "edges": routed,
            "width": max(width, 320), "height": max(height, 200),
            # Where the connected part of the chart ends. Everything at or
            # below this y is reached by nothing above it.
            "detached_y": (MARGIN + (row_index[detached_from] - 1)
                           * (NODE_H + Y_GAP) + NODE_H / 2
                           if detached_from is not None else None),
            "detached_count": len(unreached)}


def state_of(node, tested=False, deadend=False):
    """The colour band a node belongs in, and why.

    Six bands, and the order below is the order of certainty. What a reader
    wants from this chart is the PATH THAT WORKS -- which files are reached,
    exercised and finished -- and that was not sayable before: every reachable
    module was one of three shades of "has signals or does not", with nothing
    distinguishing code a test actually runs from code nobody has ever called.

        confirmed  reached from an entry point AND exercised by a test AND
                   carrying no unfinished-work signals
        tested     a test exercises it, but it is not finished
        live       reached, finished, but no test touches it
        unfinished reached, and carrying signals
        deadend    execution reaches it and stops inside
        maybe      no static path reaches it -- an INFERENCE, never a verdict

    Grey stays distinct from red for the reason it always did: "no static path
    reaches this" is a statement about a language that dispatches through
    registries and getattr, not a statement that the code is dead.
    """
    if deadend:
        return "deadend", "execution reaches here and stops inside it"
    if node["orphan"]:
        return "maybe", "nothing imports it and nothing starts from it"
    if node["unreached"]:
        return "maybe", "no static path from an entry point (inference, not proof)"
    if node["counts"].get("syntax_error"):
        return "broken", "this file does not parse"
    real = {k: v for k, v in node["counts"].items()
            if k not in ("no_docstring", "unreached")}
    if tested and not real:
        return "confirmed", "reached from a start point and exercised by a test"
    if tested:
        return "tested", "a test exercises it, but it still carries signals"
    if not real:
        return "live", "reached from a start point, nothing unfinished in it"
    if node["score"] >= 8:
        return "unfinished", "several signals of unfinished work"
    return "unfinished", "carries signals of unfinished work"


def _legacy_state_of(node):
    """The colour band a node belongs in, and why.

    Kept in one place because the legend has to say exactly what each colour is
    derived from. Grey in particular must stay distinct from red: "no static
    path reaches this" is an INFERENCE about a language that dispatches through
    registries and getattr, while the others are facts read from the syntax.
    """
    if node["orphan"]:
        return "orphan", "nothing imports it and nothing starts from it"
    if node["unreached"]:
        return "unreached", "no static path from an entry point (inference, not proof)"
    if node["counts"].get("syntax_error"):
        return "broken", "this file does not parse"
    if node["score"] >= 8:
        return "hot", "several signals of unfinished work"
    if node["score"] > 0:
        return "warm", "carries signals of unfinished work"
    return "clean", "reachable, no unfinished-work signals found"


# -- the folder overview ---------------------------------------------------
#
# The other layout on this page answers "how far is this from a start point",
# which is the right question once you know what you are looking at and the
# wrong one first. Opening on it gave the reader 975 identical rectangles
# wrapped eight to a row down twelve thousand pixels, with no landmark
# anywhere: the folder each card lived in was printed on the card, in 11px
# grey, 975 times, instead of being drawn.
#
# This one answers the two questions somebody actually opens a map with --
# what is in here, and where did the work go. Folders are boxes, so things
# that belong together are together and nothing has to be traced. Cards are
# as wide as their module is long, so the eye lands on the big work before
# reading a single filename. Relationships are not drawn at all; clicking a
# card gives that module's own trace, which is where an edge is worth
# following.

CHART_W = 1200       # fits the chart column at 1920 without sideways scroll
BOX_PAD = 11
BOX_LABEL_H = 25
BOX_GAP = 14
CARD_GAP = 10

# Two card sizes, not a continuum: a card either has room for its three lines
# of text or it does not, and a smoothly shrinking one spends the range in
# between on text nobody can read.
CARD_H = 66
CARD_H_SMALL = 34
CARD_W_MIN = 116
CARD_W_MAX = 300
SMALL_LOC = 120      # below this, filename only

# Fixed anchors rather than this project's own min and max, so a card of a
# given width means the same number of lines in every project cobblerpy maps.
# One 25,000-line outlier would otherwise squash everything else flat.
WIDTH_AT_MIN_LOC = 40
WIDTH_AT_MAX_LOC = 2400


def card_width(loc):
    """Card width for a module of `loc` lines, on a log scale.

    Log, not linear: the 25,556-line file in the corpus is 280 times the
    median one, and a linear scale would draw the median as a sliver. Log
    keeps a 2,000-line module visibly bigger than a 200-line one, which is
    the comparison a reader actually makes.
    """
    import math
    loc = max(0, int(loc or 0))
    if loc <= WIDTH_AT_MIN_LOC:
        return CARD_W_MIN
    if loc >= WIDTH_AT_MAX_LOC:
        return CARD_W_MAX
    low, high = math.log10(WIDTH_AT_MIN_LOC), math.log10(WIDTH_AT_MAX_LOC)
    share = (math.log10(loc) - low) / (high - low)
    return int(round(CARD_W_MIN + share * (CARD_W_MAX - CARD_W_MIN)))


def _pack(modules, inner_width):
    """Cards into rows, left to right, wrapping. Returns (rows, height).

    Sorted biggest first, so a row holds cards of similar size and its height
    is not set by one outlier sitting next to a dozen small ones.
    """
    placed, row, row_w, y, rows = [], [], 0, 0, []
    for module in sorted(modules, key=lambda m: (-(m.loc or 0), m.relpath)):
        width = card_width(module.loc)
        if row and row_w + CARD_GAP + width > inner_width:
            rows.append((row, y))
            y += max(h for _m, _w, h in row) + CARD_GAP
            row, row_w = [], 0
        height = CARD_H_SMALL if (module.loc or 0) < SMALL_LOC else CARD_H
        row.append((module, width, height))
        row_w += width + (CARD_GAP if row_w else 0)
    if row:
        rows.append((row, y))
        y += max(h for _m, _w, h in row)
    for row, top in rows:
        x = 0
        for module, width, height in row:
            placed.append((module, x, top, width, height))
            x += width + CARD_GAP
    return placed, y


def compute_folders(project, modules_by_key=None, chart_width=CHART_W):
    """Modules grouped into their own folders, sized by how long they are.

    Boxes are laid out on shelves: a folder is as wide as it needs to be up to
    the chart width, and small folders share a row rather than each taking a
    full-width band of their own. Sixteen of the corpus's thirty folders hold
    three modules or fewer.
    """
    import os
    groups = defaultdict(list)
    for module in project.modules:
        folder = os.path.dirname(module.relpath).replace("\\", "/") or ""
        groups[folder or "the project root"].append(module)

    inner_max = chart_width - 2 * BOX_PAD
    boxes = []
    for folder, modules in groups.items():
        widest = max(card_width(m.loc) for m in modules)
        total = sum(card_width(m.loc) + CARD_GAP for m in modules) - CARD_GAP
        label_w = int(len(folder) * 7.4 + 96 + 24)
        inner = min(inner_max, max(widest, total, label_w))
        placed, inner_h = _pack(modules, inner)
        boxes.append({
            "folder": folder,
            "modules": len(modules),
            "loc": sum(m.loc or 0 for m in modules),
            "cards": placed,
            "w": inner + 2 * BOX_PAD,
            "h": inner_h + BOX_LABEL_H + 2 * BOX_PAD,
        })
    # Most code first. "Where did the year go" is answered by the order.
    boxes.sort(key=lambda b: (-b["loc"], b["folder"]))

    nodes, x, y, shelf_h = {}, 0, 0, 0
    for box in boxes:
        if x and x + box["w"] > chart_width:
            x, y = 0, y + shelf_h + BOX_GAP
            shelf_h = 0
        box["x"], box["y"] = x, y
        for module, cx, cy, cw, ch in box["cards"]:
            key = module.dotted or module.relpath
            nodes[key] = {
                "x": x + BOX_PAD + cx,
                "y": y + BOX_LABEL_H + BOX_PAD + cy,
                "w": cw, "h": ch, "loc": module.loc or 0,
                "small": ch == CARD_H_SMALL, "folder": box["folder"],
            }
        shelf_h = max(shelf_h, box["h"])
        x += box["w"] + BOX_GAP
    height = y + shelf_h

    for box in boxes:
        del box["cards"]
    return {"boxes": boxes, "nodes": nodes,
            "width": chart_width, "height": height}
