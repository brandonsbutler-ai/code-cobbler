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
NODE_W = 188
NODE_H = 40
X_GAP = 108          # horizontal space between layers
Y_GAP = 18           # vertical space between nodes in a layer
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
    tallest = max((len(v) for v in order.values()), default=1)
    for depth in sorted(order):
        names = order[depth]
        # Centre each column vertically so the picture reads as a flow rather
        # than a ragged left-aligned list.
        offset = (tallest - len(names)) * (NODE_H + Y_GAP) / 2
        for i, name in enumerate(names):
            row = frontier_by_module.get(name, {})
            nodes[name] = {
                "name": name,
                "x": MARGIN + depth * (NODE_W + X_GAP),
                "y": MARGIN + offset + i * (NODE_H + Y_GAP),
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
        x1, y1 = src["x"] + NODE_W, src["y"] + NODE_H / 2
        x2, y2 = dst["x"], dst["y"] + NODE_H / 2
        if dst["depth"] <= src["depth"]:
            # A back edge (a cycle, or a jump to an earlier layer). Leave from
            # the same side it arrives on, so it reads as going backwards.
            x1 = src["x"]
            x2 = dst["x"] + NODE_W
        mid = (x1 + x2) / 2
        routed.append({
            "from": a, "to": b,
            "path": f"M{x1:.0f},{y1:.0f} C{mid:.0f},{y1:.0f} {mid:.0f},{y2:.0f} {x2:.0f},{y2:.0f}",
            "back": dst["depth"] <= src["depth"],
        })

    width = MARGIN * 2 + (max(order, default=0) + 1) * (NODE_W + X_GAP) - X_GAP
    height = MARGIN * 2 + tallest * (NODE_H + Y_GAP) - Y_GAP
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
