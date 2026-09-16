"""The source a reader actually needs, and nothing else.

Embedding whole files would make the map enormous on any real codebase and bury
the thing the reader clicked for. So each module contributes only the regions
that matter: the lines a signal points at, with a few lines of context, plus
every definition header so the shape of the file is still legible.

Regions that overlap are merged, and the gaps between them are shown as an
explicit break rather than silently closed up -- a snippet that looks
continuous but is not would mislead someone reading line numbers.
"""

CONTEXT = 3


def _regions(module, signals, max_regions=40):
    """Line ranges worth showing, merged and in order."""
    wanted = set()
    for hits in signals.values():
        for _text, lineno in hits:
            if lineno:
                wanted.add(lineno)
    for d in module.definitions:
        wanted.add(d.lineno)

    if not wanted:
        return []
    spans = []
    for line in sorted(wanted)[:max_regions * 4]:
        lo = max(1, line - CONTEXT)
        hi = min(module.loc, line + CONTEXT)
        if spans and lo <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(spans[-1][1], hi))
        else:
            spans.append((lo, hi))
    return spans[:max_regions]


def for_module(module, signals):
    """[(start_line, [line, ...]), ...] for the regions worth showing.

    Returns [] for a file that could not be read, rather than inventing one.
    """
    if not module.source:
        return []
    lines = module.source.splitlines()
    out = []
    for lo, hi in _regions(module, signals):
        out.append({"start": lo, "lines": lines[lo - 1:hi]})
    return out


def marked_lines(signals):
    """Line number -> the signals that point at it, for highlighting."""
    marks = {}
    for kind, hits in signals.items():
        for text, lineno in hits:
            if lineno:
                marks.setdefault(lineno, []).append(f"{kind}: {text}")
    return marks


def doc_scaffold(module, project, definition):
    """A docstring stating what is mechanically true about a definition.

    Deliberately not an attempt at intent -- that needs a reader or a model.
    This states what a parser can prove: the signature, what the function
    calls, what reaches it, and what it touches outside the process. In an
    undocumented codebase that is most of what a newcomer is missing, and it is
    the right input for anyone or anything later writing the WHY.
    """
    key = module.dotted or module.relpath
    lines = []
    if definition.args:
        lines.append(f"Takes: {', '.join(definition.args)}")
    calls = sorted({c[0] for c in module.calls
                    if definition.lineno <= c[1] <= definition.end_lineno})
    if calls:
        lines.append(f"Calls: {', '.join(calls[:10])}"
                     + (" ..." if len(calls) > 10 else ""))
    touches = sorted(k for k, hits in module.effects.items()
                     if any(definition.lineno <= ln <= definition.end_lineno
                            for _n, ln in hits))
    if touches:
        lines.append(f"Touches: {', '.join(touches)}")
    used_by = sorted(project.imported_by.get(key, ()))
    if used_by:
        lines.append(f"Module used by: {', '.join(used_by[:6])}"
                     + (" ..." if len(used_by) > 6 else ""))
    if definition.returns:
        lines.append("Returns a value.")
    elif definition.body_kind == "code":
        lines.append("Returns nothing.")
    if not lines:
        return None
    lines.append("")
    lines.append("(Generated from the syntax by cobblerpy. It states what the "
                 "code does, not why -- that part still needs a human.)")
    return lines
