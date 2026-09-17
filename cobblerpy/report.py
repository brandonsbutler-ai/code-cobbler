"""The map: one self-contained HTML file describing an unfamiliar codebase.

Written to be read top to bottom by someone who has just inherited the code and
has an afternoon. The order is the order the questions arrive: how big is this,
where does it start, what depends on what, where did they stop, and what does
the history say they were doing.

No external assets, so it opens from a file:// URL, survives being e-mailed,
and never sends a line of somebody's private source anywhere.

PROVEN and INFERRED are kept visually distinct throughout. Structure is parsed
from the syntax and is close to exact; reachability and dead code are lower
bounds because Python dispatches in ways a parser cannot follow. A tool that
blurs those two makes its confident half untrustworthy.
"""

import datetime
import html
import json
import os

from . import snippets as snip
from . import svgmap
from .deadends import by_module as deadends_by_module, find as find_deadends
from .layout import compute as compute_layout

_CSS = """
:root{--bg:#f7f7f6;--fg:#1a1a18;--mut:#6b6b66;--line:#dcdcd6;--card:#fff;
      --accent:#1f4d8f;--warn:#8a5a00;--warnbg:#fdf4e3;--ok:#2d6a4f;
      --hot:#a13d2d;--hotbg:#fbecea}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){
      --bg:#16161a;--fg:#e8e8e4;--mut:#9a9a94;--line:#32323a;--card:#1e1e24;
      --accent:#7aa8e8;--warn:#e0b060;--warnbg:#2a2318;--ok:#7fc8a4;
      --hot:#e08878;--hotbg:#2c1e1c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);padding:24px 16px;
     font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:22px;margin:0 0 2px}
h2{font-size:16px;margin:30px 0 4px;color:var(--accent)}
h2 .n{color:var(--mut);font-weight:400;font-size:13px}
.sub{color:var(--mut);font-size:13px}
.lede{color:var(--mut);font-size:13px;margin:2px 0 12px;max-width:74ch}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0 4px}
.truncated{background:var(--hotbg);border:1px solid var(--hot);
  border-radius:8px;padding:11px 13px;margin:14px 0 4px;font-size:13px}
.truncated b{color:var(--hot)}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;
      padding:9px 13px;min-width:96px}
.stat b{display:block;font-size:20px;line-height:1.2}
.stat span{color:var(--mut);font-size:11.5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px;
      padding:13px 15px;margin-bottom:12px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line);
      vertical-align:top}
th{font-size:11.5px;color:var(--mut);text-transform:uppercase;
   letter-spacing:.04em;white-space:nowrap}
tr:last-child td{border-bottom:none}
.tablewrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);
      border-radius:9px;margin-bottom:12px}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.num{text-align:right;font-variant-numeric:tabular-nums}
.tag{display:inline-block;border-radius:20px;padding:1px 9px;font-size:11.5px;
     border:1px solid var(--line);margin:1px 3px 1px 0;white-space:nowrap}
.tag.hot{background:var(--hotbg);color:var(--hot);border-color:transparent}
.chain.more{color:var(--mut);font-style:italic}
.attempt{margin-bottom:18px}
.attempt-head{margin-bottom:7px}
tr.lead td{background:color-mix(in srgb,var(--ok) 9%,transparent)}
.tag.ok{background:var(--ok);color:#fff;border-color:transparent}
.cont{padding:5px 0;border-bottom:1px dotted var(--line)}
.cont:last-child{border-bottom:none}
.cont .mono{margin-right:7px}
.tag.warn{background:var(--warnbg);color:var(--warn);border-color:transparent}
.proven{color:var(--ok);font-weight:600}
.inferred{color:var(--warn);font-weight:600}
.note{background:var(--warnbg);border:1px solid var(--line);border-radius:8px;
      padding:10px 13px;margin:10px 0;font-size:12.5px;max-width:82ch}
.bar{height:7px;border-radius:4px;background:var(--accent);display:inline-block;
     vertical-align:middle}
input[type=search]{width:100%;max-width:340px;padding:7px 10px;font-size:13px;
     border:1px solid var(--line);border-radius:7px;background:var(--card);
     color:var(--fg);margin-bottom:10px}
details{margin:5px 0}
summary{cursor:pointer;font-size:13px}
.layer{display:flex;gap:8px;align-items:flex-start;margin-bottom:6px}
.layer b{min-width:74px;font-size:12px;color:var(--mut);padding-top:2px}
.chain{font-size:12.5px;color:var(--mut)}
.empty{color:var(--mut);padding:8px 2px}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 10px;font-size:12px;
        color:var(--mut)}
.key{display:flex;align-items:center;gap:5px}
.key i{width:11px;height:11px;border-radius:3px;border:1px solid;display:inline-block}
.mapwrap{overflow:auto;background:var(--card);border:1px solid var(--line);
         border-radius:9px;padding:6px;margin-bottom:16px;max-height:76vh}
#graph{display:block}
#graph .edge{fill:none;stroke:var(--line);stroke-width:1.3}
#graph .edge.back{stroke-dasharray:4 3}
#graph .edge.deadend{stroke:var(--hot);stroke-width:1.8}
#graph marker path{fill:var(--mut);stroke:var(--mut)}
#graph .node{cursor:pointer}
#graph .node rect{stroke-width:1.5;transition:filter .1s}
#graph .node:hover rect,#graph .node:focus rect{filter:brightness(.94);stroke-width:2.4}
#graph .node:focus{outline:none}
#graph text{font:11px ui-monospace,SFMono-Regular,Menlo,monospace;fill:var(--fg)}
#graph .mod{font-size:9.5px;fill:var(--mut)}
#graph .name{font-size:11.5px;font-weight:600}
#graph .marks{font-size:10px;fill:var(--mut)}
#graph .badge{font-size:8.5px;fill:var(--mut);letter-spacing:.03em}
#graph .node.dim rect{opacity:.28}
#graph .node.dim text{opacity:.3}
#dbody{padding:14px 16px;max-height:70vh;overflow:auto}
#dbody h4{margin:14px 0 5px;font-size:12.5px;color:var(--accent)}
#dbody .facts{font-size:12.5px;color:var(--mut);margin-bottom:8px}
#dbody pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;
           padding:8px 10px;margin:5px 0;font-size:11.5px;overflow-x:auto}
#dbody .ln{color:var(--mut);user-select:none}
#dbody .hit{background:var(--warnbg);display:block}
#dbody .gap{color:var(--mut);font-size:11px;padding:2px 0}
#dbody .sig{font-size:12px;margin:2px 0}
#dbody .de{background:var(--hotbg);border-radius:6px;padding:8px 10px;margin:6px 0;
           font-size:12.5px}
@media(max-width:640px){body{padding:16px 12px}}
"""

_JS = """
const DATA = __PAYLOAD__;

function esc(s){
  return (s ?? '').toString().replace(/[&<>]/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}

function snippet(sn){
  if(!sn || !sn.regions.length) return '<p class="facts">No source stored for this module.</p>';
  let out = '', prev = null;
  for(const r of sn.regions){
    // Gaps between regions are shown, never closed up: a snippet that looks
    // continuous but is not would mislead anyone reading line numbers.
    if(prev !== null && r.start > prev + 1)
      out += '<div class="gap">... lines ' + (prev+1) + '-' + (r.start-1) + ' not shown ...</div>';
    out += '<pre>';
    r.lines.forEach((line, i) => {
      const n = r.start + i;
      const hit = sn.marks[String(n)];
      const cls = hit ? ' class="hit"' : '';
      const note = hit ? '   <span class="ln">&lt;-- ' + esc(hit.join('; ')) + '</span>' : '';
      out += '<span' + cls + '><span class="ln">' + String(n).padStart(4) + '</span>  '
           + esc(line) + note + '</span>\n';
    });
    out += '</pre>';
    prev = r.start + r.lines.length - 1;
  }
  return out;
}

function openModule(name){
  const d = DATA[name];
  if(!d) return;
  document.getElementById('dtitle').textContent = name;
  let h = '<p class="facts">' + esc(d.state) + ' &mdash; ' + esc(d.why)
        + ' &middot; ' + d.loc + ' lines &middot; depth ' + d.depth
        + ' &middot; origin: ' + esc(d.origin) + '</p>';

  if(d.deadends && d.deadends.length){
    h += '<h4>The flow stops here</h4>';
    for(const de of d.deadends){
      h += '<div class="de"><strong>' + esc(de.qualname) + '</strong> (line '
         + de.lineno + ') &mdash; ' + esc(de.kind);
      if(de.direction) h += '<br>direction: ' + esc(de.direction);
      if(de.callers && de.callers.length){
        h += '<br>reached from: ' + de.callers.slice(0,4)
             .map(c => esc(c.module) + ':' + c.lineno).join(', ');
      }
      h += '<br><span class="ln">' + esc(de.caveat) + '</span></div>';
    }
  }

  const kinds = Object.keys(d.signals || {});
  if(kinds.length){
    h += '<h4>Signals</h4>';
    for(const k of kinds.sort()){
      h += '<div class="sig"><strong>' + esc(k.replace(/_/g,' ')) + '</strong> &times; '
         + d.signals[k].length + '</div>';
    }
  }

  h += '<h4>Connections</h4><p class="facts">';
  h += 'uses: ' + (d.uses.length ? d.uses.map(esc).join(', ') : 'nothing internal');
  h += '<br>used by: ' + (d.used_by.length ? d.used_by.map(esc).join(', ') : 'nothing');
  if(d.external.length) h += '<br>external: ' + d.external.map(esc).join(', ');
  h += '</p>';

  h += '<h4>Source</h4>' + snippet(d.snippets);
  document.getElementById('dbody').innerHTML = h;
  document.getElementById('detail').showModal();
}

document.querySelectorAll('#graph .node').forEach(g => {
  const name = g.dataset.name;
  g.addEventListener('click', () => openModule(name));
  g.addEventListener('keydown', e => {
    if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); openModule(name); }
  });
  // Hovering a module dims everything it has nothing to do with, which is the
  // fastest way to see what one thing actually touches.
  g.addEventListener('mouseenter', () => {
    const d = DATA[name]; if(!d) return;
    const near = new Set([name, ...d.uses, ...d.used_by]);
    document.querySelectorAll('#graph .node').forEach(o =>
      o.classList.toggle('dim', !near.has(o.dataset.name)));
  });
  g.addEventListener('mouseleave', () =>
    document.querySelectorAll('#graph .node').forEach(o => o.classList.remove('dim')));
});
document.getElementById('dclose')?.addEventListener('click',
  () => document.getElementById('detail').close());

const q = document.getElementById('q');
if (q) q.addEventListener('input', () => {
  const n = q.value.toLowerCase();
  document.querySelectorAll('tbody tr[data-search]').forEach(tr => {
    tr.hidden = n && !tr.dataset.search.includes(n);
  });
});
"""


def _e(v):
    return html.escape("" if v is None else str(v))


def _stat(value, label):
    return f'<div class="stat"><b>{_e(value)}</b><span>{_e(label)}</span></div>'


def write_map(project, frontier, history, path, title=None, summary_totals=None,
              origins=None, modules_by_key=None, forks=None, attempts=None):
    """Write the HTML map. Returns `path`.

    `forks` is diversion.find()'s output. The caller usually has it already --
    the CLI prints it -- so it is passed in rather than recomputed; when it is
    not given and there is history to work from, it is computed here, because a
    map that silently omits a section depending on how it was called is worse
    than one that takes a moment longer.
    """
    origins = origins or {}
    modules_by_key = modules_by_key or {(m.dotted or m.relpath): m
                                        for m in project.modules}
    if forks is None and history and history.get("available"):
        from .diversion import find as _find_forks
        forks = _find_forks(project, modules_by_key, history, frontier)
    forks = forks or []
    if attempts is None:
        from .attempts import find as _find_attempts
        attempts = _find_attempts(project, modules_by_key, origins)
    attempts = attempts or []
    frontier_by_module = {r["module"]: r for r in frontier}

    # The graph, the source behind each node, and the points where the flow
    # stops. Snippets are per-module regions rather than whole files: embedding
    # a whole codebase would bury the thing the reader clicked for.
    graph = compute_layout(project, frontier_by_module)
    snippets_by_module = {}
    for key, module in modules_by_key.items():
        signals = frontier_by_module.get(key, {}).get("signals", {})
        regions = snip.for_module(module, signals)
        if regions:
            snippets_by_module[key] = {
                "regions": regions,
                "marks": {str(k): v for k, v in snip.marked_lines(signals).items()},
                "relpath": module.relpath,
            }
    dead = deadends_by_module(find_deadends(project, modules_by_key, origins))
    svg, payload = svgmap.render(graph, project, frontier_by_module,
                                 snippets_by_module, dead, origins)
    title = title or f"Codebase map -- {os.path.basename(project.root)}"
    mods = project.modules
    total_loc = sum(m.loc for m in mods)
    parse_errors = [m for m in mods if m.error]
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # -- overview
    stats = "".join([
        _stat(len(mods), "modules"),
        _stat(f"{total_loc:,}", "lines"),
        _stat(len(project.entry_points), "entry points"),
        _stat(len(project.orphans), "orphans"),
        _stat(len(project.cycles), "import cycles"),
        _stat(len(parse_errors), "will not parse"),
    ] + ([_stat(project.skipped, "NOT READ")] if project.skipped else []))

    # A truncated survey makes every "nothing imports this" finding unsound:
    # the importer may be one of the files that was never read. The terminal
    # said so in one line at the top; this file did not say so at all, and this
    # file is the one that gets sent to somebody else.
    truncation = ""
    if project.skipped:
        truncation = (
            f'<div class="truncated"><b>This survey is incomplete.</b> '
            f'{project.skipped:,} Python file'
            f'{"s were" if project.skipped != 1 else " was"} not read, because '
            f'the file limit was reached. Every finding below that depends on '
            f'what imports what &mdash; orphans, reachability, the frontier '
            f'ranking &mdash; is unsound while that is true, since the importer '
            f'may be one of the files that was skipped. Re-run with '
            f'<code>--max-files</code> above {project.skipped + len(mods):,} '
            f'before reading any of it.</div>')

    # -- entry points
    if project.entry_points:
        rows = "".join(
            f'<tr><td class="mono">{_e(n)}</td><td>'
            + "".join(f'<span class="tag">{_e(r)}</span>' for r in why)
            + "</td></tr>" for n, why in project.entry_points)
        entry_html = (f'<div class="tablewrap"><table><thead><tr><th>Module</th>'
                      f"<th>Why it looks like a start point</th></tr></thead>"
                      f"<tbody>{rows}</tbody></table></div>")
    else:
        entry_html = ('<div class="card empty">No entry point found. Either this '
                      'is a library meant to be imported, or the program that '
                      'drove it is not in this directory.</div>')

    # -- layers
    layers = project.layers()
    unplaced = sorted(set(project.by_dotted) - set().union(*layers.values())
                      if layers else set(project.by_dotted))
    # Each group carries its size. Without it the groups read as one long run
    # of names -- on a 109-module project the unreached group alone is 54 of
    # them, and its size is the single most useful number in the section: it
    # says how much of the codebase no import path reaches.
    layer_rows = []
    total_placed = sum(len(v) for v in layers.values()) + len(unplaced)
    for depth in sorted(layers):
        names = layers[depth]
        label = "entry points" if depth == 0 else f"depth {depth}"
        chips = "".join(f'<span class="tag">{_e(n)}</span>' for n in sorted(names))
        layer_rows.append(
            f'<div class="layer"><b>{label} <span class="n">{len(names)}</span></b>'
            f"<div>{chips}</div></div>")
    if unplaced:
        chips = "".join(f'<span class="tag warn">{_e(n)}</span>' for n in unplaced)
        share = f" &middot; {len(unplaced) * 100 // max(total_placed, 1)}% of the project"
        layer_rows.append(
            f'<div class="layer"><b>unreached <span class="n">{len(unplaced)}'
            f"{share}</span></b><div>{chips}</div></div>")
    layers_html = f'<div class="card">{"".join(layer_rows) or "<span class=empty>No import structure found.</span>"}</div>'

    # -- dependency table
    fan = project.fan()
    dep_rows = []
    for name, out_n, in_n in fan:
        imports = sorted(project.imports.get(name, ()))
        ext = sorted(project.external.get(name, ()))[:8]
        dep_rows.append(
            f'<tr data-search="{_e((name + " " + " ".join(imports) + " " + " ".join(ext)).lower())}">'
            f'<td class="mono">{_e(name)}</td>'
            f'<td class="num">{in_n}</td><td class="num">{out_n}</td>'
            f'<td>{"".join(f"<span class=tag>{_e(i)}</span>" for i in imports) or "<span class=sub>-</span>"}</td>'
            f'<td>{"".join(f"<span class=tag>{_e(i)}</span>" for i in ext) or "<span class=sub>-</span>"}</td></tr>')
    dep_html = (f'<div class="tablewrap"><table><thead><tr><th>Module</th>'
                f"<th>Used&nbsp;by</th><th>Uses</th><th>Internal imports</th>"
                f"<th>External</th></tr></thead><tbody>{''.join(dep_rows)}"
                f"</tbody></table></div>")

    # -- cycles
    if project.cycles:
        items = "".join(f'<li class="mono">{_e(" -> ".join(c))}</li>'
                        for c in project.cycles[:20])
        cycles_html = f'<div class="card"><ul>{items}</ul></div>'
    else:
        cycles_html = '<div class="card empty">No import cycles.</div>'

    # -- frontier
    hot = [r for r in frontier if r["score"] > 0]
    top = max((r["score"] for r in hot), default=1) or 1
    f_rows = []
    for r in hot:
        width = max(3, round(100 * r["score"] / top))
        chips = "".join(
            f'<span class="tag {"hot" if k in ("syntax_error","not_implemented","stub_pass","stub_ellipsis","orphan") else "warn"}">'
            f"{_e(k.replace('_', ' '))} {v}</span>"
            for k, v in sorted(r["counts"].items(), key=lambda kv: -kv[1]))
        # The chip shows the true count; the evidence list is capped at six per
        # kind so one noisy module cannot fill the page. Capping silently is
        # the problem: a reader who opens the evidence to check a chip saying
        # "commented code 7" counts six lines and has no way to know whether
        # the count is wrong or the list is short. Say which.
        _EVIDENCE_SHOWN = 6
        detail = []
        for kind, hits in sorted(r["signals"].items()):
            for text, lineno in hits[:_EVIDENCE_SHOWN]:
                where = f":{lineno}" if lineno else ""
                detail.append(f'<div class="chain mono">{_e(kind)}{_e(where)} &mdash; {_e(text)}</div>')
            hidden = len(hits) - _EVIDENCE_SHOWN
            if hidden > 0:
                detail.append(
                    f'<div class="chain more">&mdash; and {hidden} more '
                    f'{_e(kind.replace("_", " "))}, not listed here. The count '
                    f"on the chip is the whole file.</div>")
        body = ("<details><summary>evidence</summary>" + "".join(detail) + "</details>"
                if detail else "")
        f_rows.append(
            f'<tr data-search="{_e((r["module"] + " " + " ".join(r["counts"])).lower())}">'
            f'<td class="mono">{_e(r["module"])}</td>'
            f'<td class="num">{r["loc"]}</td>'
            f'<td class="num">{r["score"]}<br><span class="bar" style="width:{width}px"></span></td>'
            f"<td>{chips}{body}</td></tr>")
    frontier_html = (f'<div class="tablewrap"><table><thead><tr><th>Module</th>'
                     f"<th>LOC</th><th>Score</th><th>Signals</th></tr></thead>"
                     f"<tbody>{''.join(f_rows) or '<tr><td class=empty colspan=4>No unfinished-work signals found.</td></tr>'}"
                     f"</tbody></table></div>")

    # -- the same job, started over
    #
    # Placed above everything else because on the codebase this was built for
    # it is the finding that changes the next hour: reading one attempt beats
    # rewriting a fifth. The percentage is a proportion of what that file
    # itself started, not of an imagined finished feature, and the facts that
    # produced it are printed beside it so the reader can disagree.
    if attempts:
        blocks = []
        for group in attempts[:8]:
            rows = []
            for a in group["attempts"]:
                lead = a["module"] == group["resume_at"]
                rows.append(
                    f'<tr class="{ "lead" if lead else "" }">'
                    f'<td class="num">{a["percent"]}%</td>'
                    f'<td class="mono">{_e(a["relpath"])}'
                    f'{" <span class=\"tag ok\">resume here</span>" if lead else ""}'
                    f'</td><td>'
                    + "".join(f'<div class="ln">{_e(f)}</div>' for f in a["facts"])
                    + "</td></tr>")
            extra = "".join(
                f'<div class="ln">{_e(mod)} has '
                f'{_e(", ".join(names)) if names else "the only tests for this job"}'
                f"</div>"
                for mod, names in group["elsewhere"].items())
            blocks.append(
                f'<div class="attempt"><div class="attempt-head">'
                f'<b>{len(group["attempts"])} attempts at one job</b>'
                f'<span class="ln"> &middot; sharing '
                f'{_e(", ".join(group["shared"][:6]))}</span></div>'
                f'<div class="tablewrap"><table><tbody>{"".join(rows)}</tbody>'
                f"</table></div>{extra}</div>")
        attempts_html = "".join(blocks)
        if len(attempts) > 8:
            attempts_html += (f'<p class="lede">{len(attempts) - 8} more groups '
                              f"not shown.</p>")
    else:
        attempts_html = ('<p class="empty">No two modules define enough of the '
                         'same things to look like restarts of one job.</p>')

    # -- where the effort went instead
    #
    # Next to the frontier on purpose: the frontier says where work STOPPED,
    # and this says where it may have carried on. Read together they are the
    # question somebody inheriting the code actually has -- not "what is
    # unfinished" but "what was being attempted, and where do I pick it up".
    #
    # Presented as a hypothesis throughout, because it is one. The reasons are
    # on every row so a wrong pairing can be dismissed in seconds.
    if forks:
        fork_rows = []
        for f in forks[:12]:
            conts = "".join(
                f'<div class="cont"><span class="mono">{_e(c["module"])}</span>'
                f'{"".join(f"<span class=\"tag\">{_e(sig)}</span>" for sig in c["signals"])}'
                f'<div class="ln">{_e(c["why"])}</div></div>'
                for c in f["continued_as"])
            fork_rows.append(
                f'<tr><td class="mono">{_e(f["stopped"])}'
                f'<div class="ln">last touched {_e(f["last_touched"])} &middot; '
                f'{f["commits"]} commit{"s" if f["commits"] != 1 else ""}</div>'
                f'<div class="ln">&ldquo;{_e(str(f["last_subject"])[:70])}&rdquo;</div></td>'
                f"<td>{conts}</td></tr>")
        more = (f'<p class="lede">{len(forks) - 12} more not shown.</p>'
                if len(forks) > 12 else "")
        forks_html = (
            '<div class="tablewrap"><table><thead><tr>'
            '<th>Effort stopped here</th><th>and may have continued here</th>'
            f"</tr></thead><tbody>{''.join(fork_rows)}</tbody></table></div>{more}")
    else:
        forks_html = ('<p class="empty">No fork candidates. Either the history is '
                      'too short to tell, or nothing that went quiet has a '
                      'similar effort that carried on.</p>')

    # -- history
    if history.get("available"):
        tl = history["timeline"]
        tl_rows = "".join(
            f'<tr><td class="mono">{_e(month)}</td><td class="num">{len(paths)}</td>'
            f'<td>{"".join(f"<span class=tag>{_e(os.path.basename(p))}</span>" for p in paths[:14])}</td></tr>'
            for month, paths in tl.items())
        cc_rows = "".join(
            f'<tr><td class="num">{n}&times;</td><td class="mono">{_e(a)}</td>'
            f'<td class="mono">{_e(b)}</td></tr>'
            for a, b, n in history["co_change"][:20])
        st_rows = "".join(
            f'<tr><td class="mono">{_e(r["path"])}</td><td class="num">{r["commits"]}</td>'
            f'<td>{_e(r["first_seen"])}</td><td>{_e(r["last_touched"])}</td>'
            f'<td class="num">{r["days_quiet"]}</td><td>{_e(r["last_subject"][:70])}</td></tr>'
            for r in history["stalled"][:20])
        history_html = f"""
<p class="lede">Authors in this history: {", ".join(_e(a) for a in history["authors"])}.</p>
<h3 style="font-size:13.5px;margin:14px 0 4px">When each file first appeared</h3>
<div class="tablewrap"><table><thead><tr><th>Month</th><th>New files</th>
<th>Which</th></tr></thead><tbody>{tl_rows}</tbody></table></div>
<h3 style="font-size:13.5px;margin:14px 0 4px">Files that change together</h3>
<p class="lede">Repeated co-editing marks the code's real seams, which the
directory layout often hides.</p>
<div class="tablewrap"><table><thead><tr><th>Commits</th><th>File</th>
<th>Changed with</th></tr></thead><tbody>{cc_rows or '<tr><td class=empty colspan=3>No file pair changed together more than once.</td></tr>'}</tbody></table></div>
<h3 style="font-size:13.5px;margin:14px 0 4px">Started and left alone</h3>
<p class="lede">Few commits and nothing since. Finished code gets revisited;
abandoned code does not get started again.</p>
<div class="tablewrap"><table><thead><tr><th>File</th><th>Commits</th>
<th>First seen</th><th>Last touched</th><th>Days quiet</th><th>Last commit said</th>
</tr></thead><tbody>{st_rows or '<tr><td class=empty colspan=6>Nothing stalled.</td></tr>'}</tbody></table></div>
"""
    else:
        history_html = (f'<div class="card empty">No version history available &mdash; '
                        f'{_e(history.get("reason", "unknown"))}. The rest of this '
                        f"map is unaffected; only the chronology is missing.</div>")

    # -- unreferenced
    unref = project.unreferenced
    u_rows = "".join(
        f'<tr data-search="{_e((u["module"] + " " + u["name"]).lower())}">'
        f'<td class="mono">{_e(u["module"])}</td><td class="mono">{_e(u["name"])}</td>'
        f'<td>{_e(u["kind"])}</td><td class="num">{u["lineno"]}</td>'
        f'<td class="sub">{_e(u["caveat"] or "")}</td></tr>' for u in unref[:400])

    errors_html = ""
    if parse_errors:
        rows = "".join(f'<tr><td class="mono">{_e(m.relpath)}</td>'
                       f"<td>{_e(m.error)}</td></tr>" for m in parse_errors)
        errors_html = (f'<h2>Files that will not parse <span class="n">'
                       f'({len(parse_errors)})</span></h2>'
                       f'<p class="lede">A file that cannot be parsed was either written '
                       f"for a different Python version, or left mid-edit. Either way it "
                       f"is a finding, not a gap.</p>"
                       f'<div class="tablewrap"><table><thead><tr><th>File</th>'
                       f"<th>Problem</th></tr></thead><tbody>{rows}</tbody></table></div>")

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{_e(title)}</title><style>{_CSS}</style></head><body><div class="wrap">
<h1>{_e(title)}</h1>
<div class="sub">{_e(project.root)} &middot; mapped {stamp} by cobblerpy</div>
<div class="stats">{stats}</div>
{truncation}

<div class="note"><span class="proven">Proven</span> &mdash; module structure,
imports, definitions, entry points and unfinished-work signals are read
directly from the syntax.
<span class="inferred">Inferred</span> &mdash; reachability and "nothing uses
this" are LOWER BOUNDS. Python reaches code through dispatch tables, getattr,
decorators, plugin registries and framework callbacks, none of which a parser
can follow. Treat every such finding as "no static path was found", never as
"dead".</div>

<h2>The map</h2>
<p class="lede">Left to right is distance from a start point. Click any module for its source,
its signals and what it connects to. A doubled bar instead of an arrowhead marks a call that
reaches a body with nothing in it.</p>
<div class="legend">{svgmap.LEGEND}</div>
<div class="mapwrap">{svg}</div>

<input type="search" id="q" placeholder="Filter the tables below...">

<h2>Where it starts</h2>
<p class="lede">Execution begins here. The reason is given because the evidence
varies in strength: a <code>__main__</code> guard is near-certain, a suggestive
filename is not.</p>
{entry_html}

<h2>How far each module sits from a start point</h2>
<p class="lede">Shortest import path from an entry point. Roughly, how close a
module is to the top of the program &mdash; and a reasonable reading order.</p>
{layers_html}

<h2>What depends on what <span class="n">({len(mods)} modules)</span></h2>
{dep_html}

<h2>Import cycles <span class="n">({len(project.cycles)})</span></h2>
<p class="lede">Cycles usually mark a design that drifted rather than one that
was planned, and they are where refactoring hurts most.</p>
{cycles_html}

<h2>Where the work stopped <span class="n">({len(hot)} modules with signals)</span></h2>
<p class="lede">Ranked by weighted signal count. Read top-down, this is the order
in which someone inheriting this code should look at it. Each signal is a fact
about the source; whether it means the work is unfinished is your call, and the
evidence is attached so you can make it quickly.</p>
{frontier_html}

<h2>The same job, started over <span class="n">({len(attempts)})</span></h2>
<p class="lede">Modules that define enough of the same things to be attempts at
one piece of work rather than separate pieces. The percentage is how much of
what THAT FILE started is filled in &mdash; not a share of some finished
feature &mdash; and the facts behind it are beside it. Matching is on shared
definition names and never on style, because each attempt was written to a
different developer's taste and scoring that would rank the tidiest author
rather than the furthest-advanced work.</p>
{attempts_html}

<h2>Where the effort went instead <span class="n">({len(forks)})</span></h2>
<p class="lede"><strong>A hypothesis, not a finding.</strong> Work rarely stops;
it forks. A module goes quiet while a similar one carries on, and the pairing is
the most useful thing the history can say about a dead patch. A candidate has to
be doing the same KIND of work by another route &mdash; at least two of shared
vocabulary, same package, historical co-change and matching outside effects must
agree &mdash; because timing alone returns whichever file changes in every
commit. Two modules can resemble each other and have nothing to do with each
other; the reasons are on every row so you can dismiss a wrong one in seconds.</p>
{forks_html}

{errors_html}

<h2>What the history says</h2>
{history_html}

<h2>Defined but never referenced here <span class="n">({len(unref)})</span></h2>
<p class="lede">No other code in this tree mentions these names. That is an
observation, not a verdict &mdash; the caveat column says why each one might
still be live.</p>
<div class="tablewrap"><table><thead><tr><th>Module</th><th>Name</th><th>Kind</th>
<th>Line</th><th>Might still be live because</th></tr></thead>
<tbody>{u_rows or '<tr><td class=empty colspan=5>Everything defined here is referenced somewhere.</td></tr>'}</tbody></table></div>

</div><script>{_JS.replace("__PAYLOAD__", payload)}</script></body></html>"""

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path
