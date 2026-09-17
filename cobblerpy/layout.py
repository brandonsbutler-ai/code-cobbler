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
    ROW_MAX = 10

    widest = min(max((len(v) for v in order.values()), default=1), ROW_MAX)
    row_index, placed_rows = {}, 0
    for depth in sorted(order):
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
            "width": max(width, 320), "height": max(height, 200)}


def state_of(node):
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
