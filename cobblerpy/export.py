"""Hand the map to a tool somebody already owns.

Two formats, for two different people.

MERMAID is a few lines of text that GitHub, VS Code and most wikis render
natively, so the map can live in the repository it describes.

DRAWIO is XML that diagrams.net opens and edits, and draw.io exports to Visio
from there -- a far better route than emitting .vsdx directly, which is an OPC
zip of several interdependent XML parts and is miserable to generate correctly.
This is the one a client can annotate and put in a deck.

Both carry the same colour meanings as the HTML map, and both are written with
xml.etree and string building alone.
"""

import xml.etree.ElementTree as ET

from .layout import NODE_H, NODE_W, state_of

# state -> (fill, stroke) in the flat form both tools want
# Print-friendly equivalents of the six states the map draws. Exports land in
# Visio and diagrams.net, where a dark editor palette is the wrong medium, so
# these are light fills with the same MEANING rather than the same colour.
#
# Every state the layout can return must have an entry: a missing one is a
# KeyError at export time, which is how `maybe` announced itself.
_COLOURS = {
    "confirmed":  ("#DDF4F1", "#1F6F68"),
    "tested":     ("#E3EEFB", "#1F4D8F"),
    "live":       ("#E7F2EC", "#2D6A4F"),
    "unfinished": ("#FDF4E3", "#8A5A00"),
    "deadend":    ("#FBE9F4", "#B0247F"),
    "broken":     ("#F7DEDB", "#7B2D26"),
    "maybe":      ("#EFECF8", "#5A4B8A"),
}

_MERMAID_CLASSES = "\n".join(
    f"    classDef {state} fill:{fill},stroke:{stroke},stroke-width:1.5px;"
    for state, (fill, stroke) in _COLOURS.items())


def _safe_id(name):
    """Mermaid node ids cannot carry dots or dashes."""
    return "n_" + "".join(c if c.isalnum() else "_" for c in name)


def to_mermaid(graph, project, deadends_by_module=None, max_nodes=120):
    """A Mermaid flowchart. Returns the diagram source."""
    deadends_by_module = deadends_by_module or {}
    nodes = graph["nodes"]

    if len(nodes) > max_nodes:
        # Past a certain size a Mermaid diagram is unreadable anywhere it
        # renders, so say so rather than emit something nobody can use.
        keep = sorted(nodes, key=lambda n: -(nodes[n]["score"] or 0))[:max_nodes]
        chosen = set(keep)
        note = (f"%% showing the {max_nodes} modules with the most signals, of "
                f"{len(nodes)} total -- the full picture is in the HTML map\n")
    else:
        chosen = set(nodes)
        note = ""

    lines = [note + "flowchart LR"]
    by_depth = {}
    for name in sorted(chosen):
        by_depth.setdefault(nodes[name]["depth"], []).append(name)

    for depth in sorted(by_depth):
        label = "entry points" if depth == 0 else f"depth {depth}"
        lines.append(f'    subgraph d{depth}["{label}"]')
        lines.append("    direction TB")
        for name in by_depth[depth]:
            short = name.rsplit(".", 1)[-1]
            lines.append(f'        {_safe_id(name)}["{short}"]')
        lines.append("    end")

    for edge in graph["edges"]:
        if edge["from"] not in chosen or edge["to"] not in chosen:
            continue
        arrow = "-.->" if deadends_by_module.get(edge["to"]) else "-->"
        lines.append(f"    {_safe_id(edge['from'])} {arrow} {_safe_id(edge['to'])}")

    for name in sorted(chosen):
        state, _why = state_of(nodes[name])
        lines.append(f"    class {_safe_id(name)} {state};")

    lines.append(_MERMAID_CLASSES)
    return "\n".join(lines) + "\n"


def to_drawio(graph, project, deadends_by_module=None, title="codebase map"):
    """A .drawio (mxGraphModel) document. Returns the XML string."""
    deadends_by_module = deadends_by_module or {}
    nodes = graph["nodes"]

    mxfile = ET.Element("mxfile", host="cobblerpy")
    diagram = ET.SubElement(mxfile, "diagram", name=title)
    model = ET.SubElement(diagram, "mxGraphModel",
                          dx="1100", dy="800", grid="1", gridSize="10",
                          page="1", pageWidth=str(graph["width"]),
                          pageHeight=str(graph["height"]))
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")

    ids = {}
    for i, (name, node) in enumerate(sorted(nodes.items()), start=2):
        state, why = state_of(node)
        fill, stroke = _COLOURS.get(state, ("#ECECEA", "#6B6B66"))
        ids[name] = str(i)
        marks = sum(node["counts"].values())
        label = name if not marks else f"{name}&#10;({marks} signals)"
        cell = ET.SubElement(
            root, "mxCell", id=str(i), value=label, parent="1", vertex="1",
            style=(f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};"
                   f"strokeColor={stroke};fontSize=11;align=left;"
                   f"spacingLeft=8;verticalAlign=middle"))
        ET.SubElement(cell, "mxGeometry", x=str(int(node["x"])),
                      y=str(int(node["y"])), width=str(NODE_W),
                      height=str(NODE_H), **{"as": "geometry"})

    edge_id = len(nodes) + 10
    for edge in graph["edges"]:
        source, target = ids.get(edge["from"]), ids.get(edge["to"])
        if not source or not target:
            continue
        stub = bool(deadends_by_module.get(edge["to"]))
        style = ("edgeStyle=orthogonalEdgeStyle;rounded=1;html=1;"
                 + ("strokeColor=#A13D2D;dashed=1;endArrow=none;"
                    if stub else "strokeColor=#BBBBB5;"))
        cell = ET.SubElement(root, "mxCell", id=str(edge_id), parent="1",
                             edge="1", source=source, target=target, style=style)
        ET.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        edge_id += 1

    ET.indent(mxfile, space="  ")
    return ET.tostring(mxfile, encoding="unicode", xml_declaration=True)
