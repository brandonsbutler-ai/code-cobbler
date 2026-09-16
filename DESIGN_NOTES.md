# CobblerPy design notes

Recorded decisions and deferred work. Not documentation -- documentation says what the tool does;
this says what it should do next and why.

---

## Visual map: Visio / draw.io / clickable code (Brandon, 2026-09-16)

> "would we be able to put together something like a visio or a drawio flow graphic that could
> show green and red for function and trouble? It would be amazing to have a graphical
> representation that you could click on and pull up code snippets."

Yes. Three outputs, because they serve three different readers, and none of them needs a
dependency.

### 1. Interactive SVG inside the existing HTML map -- the flagship

This is the one that delivers "click a box, see the code". The report is already a
self-contained HTML file; the graph becomes inline SVG in the same file, and clicking a node
opens a panel showing the actual source lines plus the evidence behind its colour.

No server, no external assets, nothing uploaded -- which matters because the input is usually
somebody's private source.

**The hard part is layout, and we are most of the way there already.** A layered (Sugiyama)
layout has three stages, and stage one is done:

- *layer assignment* -- `Project.layers()` already computes depth from the entry points, which is
  exactly this;
- *ordering within a layer* -- barycentre heuristic, two or three sweeps, to cut edge crossings;
- *coordinates* -- x from layer, y from order, edges as polylines with a bend at the layer
  boundary.

At the sizes this tool sees (tens to low hundreds of modules) that is a couple of hundred lines
of standard library and it renders instantly. Graphviz would do it better and is not worth the
dependency.

### 2. `--drawio` export -- for people who want to edit it

A `.drawio` file is XML (`mxGraphModel`), writable with `xml.etree` alone. Opens in
diagrams.net desktop or web, fully editable, and draw.io exports to Visio from there -- which is
a far better route than trying to emit `.vsdx` directly, since that is an OPC zip of several
interdependent XML parts and is miserable to generate correctly.

This is the deliverable for a client who wants to annotate the map in a tool they already own and
put it in a deck.

### 3. `--mermaid` export -- for the README and the pull request

A Mermaid `flowchart` is a few lines of text, renders natively on GitHub, in VS Code and in most
wikis. Costs almost nothing to emit and means the map can live in a repository's own README.

---

### Colour has to mean something specific

Green and red imply a judgement, so the legend must say exactly what each colour is derived from,
or it is decoration that invites a wrong conclusion. Proposed:

| Colour | Meaning | Derived from |
|---|---|---|
| **green** | reachable from an entry point, no unfinished-work signals | proven structure |
| **amber** | reachable, but carries signals (TODO, stub, unused import) | proven signals, inferred significance |
| **red** | high signal score, will not parse, or nothing imports it | proven facts, ranked |
| **grey** | no static path reaches it | **inference, not proof** |

Grey must stay visually distinct from red. "Unreachable" is a lower bound -- Python dispatches
through registries, decorators and getattr -- and colouring it the same as "broken" would state
as fact the one thing this tool explicitly cannot know.

**Red does not mean bad code.** It means signals of unfinished work, which may be entirely
innocent: an abstract base class is full of `NotImplementedError`, a plugin module is imported by
nobody. The legend says so, and clicking through shows the evidence so the reader can dismiss it
in seconds.

### Order of work

1. SVG layout + inline interactive map (the demo that sells it)
2. `--mermaid` (nearly free once the graph model exists)
3. `--drawio` (half a day, and it is what a client can edit)

`.vsdx` direct: not planned. Route through draw.io.
