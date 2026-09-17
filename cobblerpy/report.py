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

from . import BRAND, __version__
from . import snippets as snip
from . import svgmap
from .deadends import by_module as deadends_by_module, find as find_deadends
from .layout import compute as compute_layout

_CSS = """
/* Dark by default, and the same palette as the desktop window.
   This file is the thing that gets sent to somebody, so it is the thing that
   has to look like the product. It used to default to light and go dark only
   if the VIEWER'S OPERATING SYSTEM said so, which meant the flagship artifact
   of a dark editor-native tool arrived looking like a different product on
   most machines. The colours are the ones a Python developer already reads:
   green furthest along, amber unfinished, coral will not parse, violet
   nothing imports it, blue the action. */
:root{--bg:#0f1116;--fg:#d6dae3;--mut:#8b93a3;--faint:#5e6675;
      --line:#262b36;--line2:#1f242e;--card:#161920;--sunk:#1b1f28;
      --accent:#58a6ff;--warn:#d8a657;--warnbg:#241c10;--ok:#7ee787;
      --hot:#f4796b;--hotbg:#2a1a16;--violet:#bc8cff;
      --barbg:#12151c;--barh:52px}
/* A light variant for anyone who explicitly asks for one -- printing, a
   projector, a reviewer who wants it on paper. Dark stays the default. */
@media(prefers-color-scheme:light){:root:not([data-theme=dark]){
      --bg:#f7f7f6;--fg:#1a1a18;--mut:#6b6b66;--faint:#9a9a94;
      --line:#dcdcd6;--line2:#ebebe7;--card:#fff;--sunk:#f6f6f4;
      --accent:#1f4d8f;--warn:#8a5a00;--warnbg:#fdf4e3;--ok:#2d6a4f;
      --hot:#a13d2d;--hotbg:#fbecea;--violet:#6b3fa0;
      --barbg:#fff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);padding:0 0 28px;
     font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
/* Geared for 1920. The old 1180px cap used 41% of a wide screen and left the
   graph in a letterbox; the tables are the widest thing here and they were
   the ones being squeezed. */
/* Chart left, detail right. The tables used to stack under the chart and the
   page ran to ten thousand lines; now the selection has somewhere to appear
   that the reader is already looking at. */
/* Two thirds chart, one third detail. The chart was shifted left and left at
   its own width, so the page got wider without the chart getting wider with
   it. */
.wrap{max-width:2100px;margin:0 auto;padding:22px 16px 0;
      display:flex;gap:24px;align-items:flex-start}
.left{flex:2 1 0;min-width:0}
#panel{flex:1 1 0;min-width:340px;max-width:660px;position:sticky;
       top:calc(var(--barh) + 14px);max-height:calc(100vh - var(--barh) - 28px);
       overflow:auto;background:var(--card);border:1px solid var(--line);
       border-radius:10px}
#panel .phead{position:sticky;top:0;background:var(--card);padding:13px 16px;
       border-bottom:1px solid var(--line);border-radius:10px 10px 0 0}
#panel .phead b{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
       font-size:13.5px;display:block}
#panel #pbody{padding:12px 14px 16px}
#panel h4{font-size:10.5px;letter-spacing:.9px;text-transform:uppercase;
       color:var(--mut);margin:14px 0 5px}
/* Tighter rows. A panel is read in a column, so the vertical rhythm that
   suits a full-width page wastes half of it. */
#panel td, #panel th{padding:4px 8px;line-height:1.35}
#panel .ln{line-height:1.4}
#panel p{margin:4px 0}
#panel .attempt{margin-bottom:11px}
#panel .lede{font-size:11.5px;margin:2px 0 7px}
#panel h4:first-child{margin-top:0}
#panel pre{background:var(--sunk);border:1px solid var(--line2);border-radius:6px;
       padding:9px 11px;overflow:auto;font-size:11.5px;line-height:1.5;margin:0 0 8px}
#panel table{font-size:11.5px}
#panel .tablewrap{margin-bottom:6px}
.tallies{display:flex;flex-wrap:wrap;gap:8px 14px;margin-bottom:4px}
.tally{display:flex;align-items:center;gap:6px;font-size:12px}
.tally b{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
.tally .dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.dot.s-confirmed{background:#33d6c8}
.dot.s-tested{background:#58a6ff}
.dot.s-live{background:#7ee787}
.dot.s-unfinished{background:var(--warn)}
.dot.s-deadend{background:#ff6ec7}
.dot.s-broken{background:var(--hot)}
.dot.s-maybe{background:var(--violet)}
#panel .ev{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
       font-size:11px;padding-left:12px}
#panel .ev.more{font-style:italic;color:var(--faint)}
#panel a.goto{color:var(--accent);cursor:pointer;text-decoration:underline;
       text-underline-offset:2px}
#panel a.goto:hover{color:var(--fg)}
#panel .de{background:var(--sunk);border-left:3px solid var(--hot);border-radius:6px;
       padding:8px 11px;margin-bottom:7px;font-size:12px}
@media(max-width:1180px){.wrap{display:block}
  #panel{position:static;max-height:none;margin-top:20px;width:100%}}
/* Title bar. The page used to open on a single heading that ran the job
   description and the folder together, which reads like the name of the
   product rather than the name of the job. The
   product is CodeCobbler; the tool that produced the file is cobblerpy; the
   folder is the subject. The bar keeps those three apart. */
.topbar{position:sticky;top:0;z-index:40;height:var(--barh);
     background:var(--barbg);border-bottom:1px solid var(--line)}
.topbar .inner{max-width:2100px;height:100%;margin:0 auto;padding:0 16px;
     display:flex;align-items:center;gap:13px}
.topbar .mark{width:27px;height:27px;border-radius:8px;flex:none;font-size:12.5px;
     background:linear-gradient(150deg,var(--accent),#2c62ab);color:#0b0d12;
     font-weight:800;letter-spacing:-.6px;
     display:flex;align-items:center;justify-content:center}
.topbar .word{font-size:17px;font-weight:650;letter-spacing:.2px;white-space:nowrap}
.topbar .word i{font-style:normal;color:var(--accent)}
.topbar .rule{width:1px;height:20px;flex:none;background:var(--line)}
.topbar .what{color:var(--mut);font-size:12.5px;white-space:nowrap}
.topbar .subj{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;
     min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.topbar .right{margin-left:auto;display:flex;align-items:center;gap:10px;flex:none;
     color:var(--faint);font-size:11.5px;white-space:nowrap}
.topbar .tool{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--mut);
     background:var(--sunk);border:1px solid var(--line);border-radius:999px;
     padding:3px 9px}
@media(max-width:820px){.topbar .what,.topbar .rule,.topbar .right{display:none}}
h1{font-size:22px;margin:0 0 2px}
h2{font-size:16px;margin:30px 0 4px;color:var(--accent)}
h2 .n{color:var(--mut);font-weight:400;font-size:13px}
.sub{color:var(--mut);font-size:13px}
.lede{color:var(--mut);font-size:13px;margin:2px 0 12px;max-width:74ch}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:16px 0 4px}
.excluded{background:var(--sunk);border:1px solid var(--line);
      border-left:3px solid var(--mut);border-radius:8px;padding:10px 13px;
      margin:10px 0 4px;font-size:12.5px;color:var(--mut);max-width:96ch}
.excluded b{color:var(--fg)}
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
/* The graph. Cards are cut-outs -- their fill is the panel's own background --
   and the state is carried by a glowing edge instead of a colour wash, which
   keeps four lines of text legible inside every one of them. */
#graph .card{fill:var(--card);stroke-width:1.5;
             filter:drop-shadow(0 0 4px currentColor)}
#graph .node{color:var(--accent)}
/* Six states, and the reader should be able to trace the working path by
   colour alone: cyan is confirmed, blue is under test, green is live. */
#graph .node[data-state="confirmed"]  .card{stroke:#33d6c8;stroke-width:2}
#graph .node[data-state="confirmed"]  {color:#33d6c8}
#graph .node[data-state="tested"]     .card{stroke:#58a6ff}
#graph .node[data-state="tested"]     {color:#58a6ff}
#graph .node[data-state="live"]       .card{stroke:#7ee787}
#graph .node[data-state="live"]       {color:#7ee787}
#graph .node[data-state="unfinished"] .card{stroke:var(--warn)}
#graph .node[data-state="unfinished"] {color:var(--warn)}
#graph .node[data-state="broken"]     .card{stroke:var(--hot)}
#graph .node[data-state="broken"]     {color:var(--hot)}
#graph .node[data-state="maybe"]      .card{stroke:var(--violet);opacity:.55}
#graph .node[data-state="maybe"]      {color:var(--violet)}
#graph .node[data-state="maybe"]:hover .card{opacity:1}
/* A dead end is its own state: execution reaches this module and stops
   inside it, which is not the same fact as "carries signals of unfinished
   work" and should not share its colour. */
#graph .node[data-state="deadend"]   .card{stroke:#ff6ec7;stroke-width:2}
#graph .node[data-state="deadend"]   {color:#ff6ec7}

/* The one hypothesis on the map: this module stopped, and THAT one is doing
   the same work. Dashed because it is inferred from shared definition names
   rather than read from an import, and every one carries its reason in a
   tooltip. */
#graph .continues{stroke:#ff6ec7;stroke-width:1.6;fill:none;
                  stroke-dasharray:7 5;opacity:.75}
#graph .continues:hover{opacity:1;stroke-width:2.4}
#graph marker#continues path{stroke:#ff6ec7}
#graph .node.selected .card{stroke-width:3;
                            filter:drop-shadow(0 0 11px currentColor)}
#graph .node:hover .card{stroke-width:2.5;
                         filter:drop-shadow(0 0 9px currentColor)}
/* Type size is NOT the lever for fitting more on screen. Shrinking the card
   text to 9.5px bought a narrower chart at the cost of making somebody lean
   in on a 1920 laptop, which is the wrong trade. The footprint came down by
   losing a row and most of the gutter instead, and the type went back up. */
#graph .fname{font:600 12.5px ui-monospace,SFMono-Regular,Menlo,monospace;
              fill:var(--fg)}
#graph .meta{font:11px ui-monospace,SFMono-Regular,Menlo,monospace;
             fill:var(--mut)}
#graph .node{cursor:pointer}
#graph .cut{stroke:var(--line);stroke-width:1.5;stroke-dasharray:3 6}
#graph .cutlabel{font:10.5px ui-monospace,SFMono-Regular,Menlo,monospace;
                 fill:var(--mut);letter-spacing:.6px}
/* Centred in its container. A chart narrower than the page used to sit hard
   against the left margin with a field of empty to its right, which reads as
   something failing to load rather than as a small project. */
.graphbox{display:flex;justify-content:center}
#graph .meta.owner{fill:var(--faint)}
#graph .marks{font:600 11px ui-monospace,SFMono-Regular,Menlo,monospace;
              fill:currentColor}
#graph .badge{font:9.5px ui-monospace,SFMono-Regular,Menlo,monospace;
              fill:var(--faint);letter-spacing:.5px}
/* The verdict at the top of the panel. Green when this is the file to carry
   on from, pink when the flow stops here, muted when another attempt got
   further -- the same three colours the chart uses, so the panel and the
   picture agree without the reader checking. */
.verdict{border-radius:8px;padding:11px 13px;margin:0 0 12px;
         border:1px solid var(--line)}
.verdict b{font-size:14px}
.verdict.resume{background:rgba(126,231,135,.09);border-color:var(--ok)}
.verdict.resume b{color:var(--ok)}
.verdict.superseded{background:var(--sunk)}
.verdict.superseded b{color:var(--mut)}
.verdict.deadend{background:rgba(255,110,199,.08);border-color:#ff6ec7}
.verdict.deadend b{color:#ff6ec7}
.attempt{margin-bottom:18px}
.wall{background:var(--hotbg);border:1px solid var(--hot);border-left:3px solid var(--hot);
      border-radius:8px;padding:10px 13px;margin:8px 0 12px}
.wall b{color:var(--hot)}
.lineage{margin:4px 0 14px}
.lincap{font-size:11px;letter-spacing:.9px;color:var(--mut);margin-bottom:6px}
.step{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;
      margin-bottom:2px}
.step .rail{color:var(--line)}
.step .node{color:var(--mut)}
.step .node.lead{color:var(--ok);font-weight:600}
.stepbody{margin:1px 0 7px 26px;font-size:11.5px}
.added{color:var(--accent)}
.dropped{color:var(--mut)}
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
.legend{display:flex;flex-wrap:wrap;gap:12px;margin:0 0 10px;font-size:12px;
        color:var(--mut)}
.key{display:flex;align-items:center;gap:5px}
.key i{width:11px;height:11px;border-radius:3px;border:1px solid;display:inline-block}
/* The key, always on screen. The chart is twelve thousand pixels tall on a
   real project, so a legend that sits above it answers "what was orange
   again?" for the first screen only. This one rides under the title bar --
   and only for as long as the map is on screen, because it is sticky inside
   .mapzone rather than inside the column. The sentences stay below it: they
   are read once, the colours are referred to continuously. */
.mapzone{position:relative}
.keybar{position:sticky;top:var(--barh);z-index:30;display:flex;flex-wrap:wrap;
        gap:6px;align-items:center;padding:8px 0 9px;background:var(--bg);
        border-bottom:1px solid var(--line2);margin-bottom:9px}
.keybar .chip{display:flex;align-items:center;gap:6px;cursor:pointer;
        font:12px/1 inherit;color:var(--mut);background:var(--card);
        border:1px solid var(--line);border-radius:999px;padding:5px 11px 5px 8px}
.keybar .chip i{width:11px;height:11px;border-radius:3px;border:1px solid;
        display:inline-block}
.keybar .chip:hover{color:var(--fg);border-color:var(--mut)}
.keybar .chip[aria-pressed="true"]{color:var(--fg);border-color:var(--fg);
        background:var(--sunk)}
.keybar .hint{color:var(--faint);font-size:11.5px;margin-left:4px}
/* Picked one colour out of the key: everything else steps back. Separate
   from .dim, which is the hover relation highlight -- the two are answering
   different questions and clearing one must not clear the other. */
#graph .node.off rect{opacity:.07}
#graph .node.off text{opacity:.09}
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
           + esc(line) + note + '</span>\\n';
    });
    out += '</pre>';
    prev = r.start + r.lines.length - 1;
  }
  return out;
}

function openModule(name){
  const d = DATA[name];
  if(!d) return;
  // The verdict leads. A card is clicked to answer one of two questions --
  // "is this where it goes" or "is this a dead end" -- and everything below
  // is the evidence for whichever one applies. Putting the facts first and
  // the verdict nowhere made the reader do the joining.
  let h = '';
  if(d.verdict){
    const v = d.verdict;
    h += '<div class="verdict ' + esc(v.kind) + '">'
       + '<b>' + esc(v.headline) + '</b>'
       + '<div class="ln">' + esc(v.detail) + '</div>'
       + (v.other
          ? '<div class="ln">the other side: <a class="goto" data-goto="'
            + esc(v.otherModule || '') + '">' + esc(v.other) + '</a></div>' : '')
       + (v.shared && v.shared.length
          ? '<div class="ln">shares ' + esc(v.shared.join(', ')) + '</div>' : '')
       + (v.facts && v.facts.length
          ? '<div class="ln">' + v.facts.map(esc).join('<br>') + '</div>' : '')
       + '</div>';
  } else if(d.deadends && d.deadends.length){
    h += '<div class="verdict deadend"><b>this is a dead end</b>'
       + '<div class="ln">execution reaches this module and stops inside it. '
       + 'The lines are below, with the callers that get here.</div></div>';
  }
  h += '<p class="facts">' + esc(d.state) + ' &mdash; ' + esc(d.why)
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
      const hits = d.signals[k] || [];
      h += '<div class="sig"><strong>' + esc(k.replace(/_/g,' ')) + '</strong> &times; '
         + hits.length + '</div>';
      // The EVIDENCE, not just the count. It used to sit in a table below the
      // chart; now that the chart is the page, this is the only place it can
      // be, and a count nobody can check is a count nobody will act on.
      const shown = hits.slice(0, 6);
      for(const hit of shown){
        const where = hit[1] ? ':' + hit[1] : '';
        h += '<div class="ln ev">' + esc(k) + esc(where) + ' &mdash; '
           + esc(hit[0]) + '</div>';
      }
      if(hits.length > shown.length)
        h += '<div class="ln ev more">and ' + (hits.length - shown.length)
           + ' more ' + esc(k.replace(/_/g,' ')) + ', not listed here. '
           + 'The count above is the whole file.</div>';
    }
  }

  // Relations are LINKS, not text. Tracing what a module is tied to -- or
  // what it was planned to be tied to -- is the reason somebody opens this
  // panel, and reading a name they then have to hunt for on the chart is the
  // opposite of tracing it.
  const link = n => DATA[n]
      ? '<a class="goto" data-goto="' + esc(n) + '">' + esc(n) + '</a>'
      : esc(n);
  h += '<h4>Connections</h4><p class="facts">';
  h += 'uses: ' + (d.uses.length ? d.uses.map(link).join(', ') : 'nothing internal');
  h += '<br>used by: ' + (d.used_by.length ? d.used_by.map(link).join(', ') : 'nothing');
  if(d.external.length) h += '<br>external: ' + d.external.map(esc).join(', ');
  h += '</p>';

  h += '<h4>Source</h4>' + snippet(d.snippets);
  // Into the panel beside the chart. The old handler called showModal() on a
  // dialog the page never contained, so clicking a module did nothing at all
  // -- silently, because getElementById returns null and the exception dies
  // in the handler.
  document.getElementById('ptitle').textContent = name;
  document.getElementById('phint').textContent = d.why || '';
  document.getElementById('pbody').innerHTML = h;
  document.querySelectorAll('#graph .node').forEach(
    o => o.classList.toggle('selected', o.dataset.name === name));
  document.getElementById('panel').scrollTop = 0;
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
// Nothing selected: the panel shows the project. Pressing Escape returns to
// it rather than leaving the reader on a module they have finished with.
function showProject(){
  document.getElementById('ptitle').textContent = 'project summary';
  document.getElementById('phint').textContent = 'click a module for its detail';
  document.getElementById('pbody').innerHTML =
    document.getElementById('summarySource').innerHTML;
  document.querySelectorAll('#graph .node').forEach(
    o => o.classList.remove('selected'));
}

// The key is not only a caption. "What was orange again?" is followed almost
// immediately by "so where are they", and on a chart this tall that is a
// scroll, not a glance. Clicking a colour steps everything else back.
//
// .off is deliberately not .dim: .dim is the hover relation highlight and is
// cleared on every mouseleave, so sharing the class would wipe the filter the
// first time the reader moved the pointer across the chart.
let only = null;
const KEYHINT = 'click a colour to show only those \u00b7 Esc clears';
function applyFilter(){
  document.querySelectorAll('#graph .node').forEach(
    o => o.classList.toggle('off', only !== null && o.dataset.state !== only));
  document.querySelectorAll('.keybar .chip').forEach(
    c => c.setAttribute('aria-pressed', String(c.dataset.state === only)));
  const hint = document.getElementById('keyhint');
  if(hint) hint.textContent = only === null
    ? KEYHINT
    : ('showing ' + only + ' only \u00b7 Esc clears');
}
document.querySelectorAll('.keybar .chip').forEach(c => {
  c.addEventListener('click', () => {
    only = (only === c.dataset.state) ? null : c.dataset.state;
    applyFilter();
  });
});

document.addEventListener('keydown', e => {
  if(e.key === 'Escape'){ only = null; applyFilter(); showProject(); }
});
showProject();          // the panel starts on the project

// Following a relation selects it and brings it into view, so a trace is a
// sequence of clicks rather than a search through the chart each time.
document.addEventListener('click', e => {
  const a = e.target.closest('.goto');
  if(!a) return;
  e.preventDefault();
  const name = a.dataset.goto;
  if(!name || !DATA[name]) return;
  openModule(name);
  const node = document.querySelector('#graph .node[data-name="'
    + CSS.escape(name) + '"]');
  if(node && node.scrollIntoView)
    node.scrollIntoView({block: 'center', inline: 'center'});
});

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
    # Resolved BEFORE the graph is drawn: the continuation links on the map
    # come from these groups, so computing them afterwards would draw a map
    # with the findings missing from it.
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
                                 snippets_by_module, dead, origins,
                                 history=history, modules_by_key=modules_by_key,
                                 attempts=attempts)
    # The product is CodeCobbler. The tool is cobblerpy -- that is the name
    # on the command, the package and the import, and it stays. The subject is
    # whatever folder was read. All three used to be mashed into one <h1>.
    subject = os.path.basename(project.root.rstrip(os.sep)) or project.root
    title = title or subject
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
    # What the survey walked past, and why.
    #
    # Excluding a virtualenv or a tool's cache directory is right. Saying
    # nothing about it is not: pointed at one real project this surveyed 975
    # modules and passed over 9,219 files, and the page said "975 modules"
    # with no hint that ten times that number had been declined. A reader
    # cannot tell a small project from a mostly-skipped one.
    excluded = getattr(project, "excluded", {}) or {}
    excluded_html = ""
    if excluded:
        total = sum(excluded.values())
        listed = ", ".join(f"{_e(name)} ({count:,})" for name, count in
                           sorted(excluded.items(), key=lambda kv: -kv[1])[:5])
        more = (f" and {len(excluded) - 5} other directories"
                if len(excluded) > 5 else "")
        excluded_html = (
            f'<div class="excluded"><b>{total:,} Python file'
            f'{"s were" if total != 1 else " was"} not read.</b> '
            f'They sit in directories a survey should not walk into: {listed}'
            f'{more}. That is deliberate &mdash; a virtualenv or a tool cache '
            f'is not the codebase you inherited &mdash; but the number is here '
            f'so you can tell a small project from a mostly-excluded one.</div>')

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

            # The wall: what every attempt left unfinished. First, because when
            # four people stopped in the same place that is the finding, and
            # the ranked rows below only say which of them got nearest to it.
            wall = ""
            if group.get("common_gaps"):
                names = ", ".join(group["common_gaps"])
                wall = (f'<div class="wall"><b>every attempt stopped at '
                        f'{_e(names)}</b><div class="ln">'
                        f'{len(group["attempts"])} attempts reached the same '
                        f'place and none got past it. Whatever is in the way is '
                        f'probably not the code, and a fifth attempt will stop '
                        f'here too.</div></div>')

            # The lineage: how the work grew. Ordered by what each attempt
            # DEFINES rather than by commit date, because a squashed or
            # rebased history is one timestamp for every file in it.
            lineage = ""
            if group.get("lineage"):
                steps = group["lineage"]
                rows_ = []
                for i, step in enumerate(steps):
                    lead = step["module"] == group["resume_at"]
                    glyph = "&#9492;&#9472;" if i == len(steps) - 1 else "&#9500;&#9472;"
                    detail = ""
                    if step["added"]:
                        detail += (f'<div class="added">+ '
                                   f'{_e(", ".join(step["added"]))}</div>')
                    if step["dropped"]:
                        detail += (f'<div class="dropped">&minus; '
                                   f'{_e(", ".join(step["dropped"]))}'
                                   f'  <span class="ln">(an earlier attempt had '
                                   f'this)</span></div>')
                    rows_.append(
                        f'<div class="step"><span class="rail">{glyph}</span> '
                        f'<span class="{"node lead" if lead else "node"}">'
                        f'{_e(step["relpath"])}</span> '
                        f'<span class="ln">{step["percent"]}%</span>'
                        f'<div class="stepbody">{detail}</div></div>')
                lineage = ('<div class="lineage"><div class="lincap">HOW IT GREW'
                           '</div>' + "".join(rows_) + "</div>")
            blocks.append(
                f'<div class="attempt"><div class="attempt-head">'
                f'<b>{len(group["attempts"])} attempts at one job</b>'
                f'<span class="ln"> &middot; sharing '
                f'{_e(", ".join(group["shared"][:6]))}</span></div>'
                f"{wall}{lineage}"
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

    # ONE panel, docked to the right of the chart.
    #
    # These tables used to stack below the map -- past ten thousand lines on a
    # 975-module project -- so finding the row for the module you just clicked
    # meant scrolling to hunt for it. The chart is the page now and the panel
    # sits beside it: click a module and the panel holds that module, or leave
    # nothing selected and it holds the project. Nothing is lost and nothing
    # is stacked underneath.
    # The panel's default view, and it has to stay SMALL.
    #
    # Its first version was every table the page used to stack: on a
    # 975-module project that is 1.2 MB of markup, including a dependency
    # table with a row per module, rendered into a 420px column. It looked
    # like a fault and it was the overhead this panel existed to remove.
    #
    # What stays is what a reader wants before they have clicked anything:
    # how the project divides by state, where the work stopped, and whether
    # the same job has been started more than once. What a single module
    # depends on is a property OF THAT MODULE, and it is one click away on the
    # card itself -- there is no reason to carry 975 of them at rest.
    _tally = {}
    for _name in project.by_dotted:
        _node = graph["nodes"].get(_name)
        if _node is None:
            continue
        _st, _ = svgmap.state_for(_node, _name, project, dead)
        _tally[_st] = _tally.get(_st, 0) + 1
    # The frontier at rest is a SHORTLIST. The full ranking is 900 rows on a
    # large project, and a reader who has not clicked anything yet wants to
    # know where to start, not to scroll an ordered list of everything.
    _top = hot[:12]
    _short_rows = "".join(
        f'<tr><td class="num">{r["score"]}</td>'
        f'<td class="mono">{_e(r["relpath"])}</td>'
        f'<td class="ln">{_e(", ".join(f"{k.replace(chr(95), chr(32))} {v}" for k, v in sorted(r["counts"].items(), key=lambda kv: -kv[1])[:3]))}</td></tr>'
        for r in _top)
    _frontier_short = (
        f'<div class="tablewrap"><table><tbody>{_short_rows}</tbody></table></div>'
        + (f'<p class="lede">{len(hot) - len(_top)} more, each on its own card.'
           f"</p>" if len(hot) > len(_top) else ""))

    _order = ["confirmed", "tested", "live", "unfinished", "deadend",
              "broken", "maybe"]
    _bars = "".join(
        f'<div class="tally"><span class="dot s-{_e(k)}"></span>'
        f'<b>{_tally.get(k, 0)}</b> <span class="ln">{_e(k)}</span></div>'
        for k in _order if _tally.get(k))
    summary_html = (
        f'<h4>what this project is made of</h4><div class="tallies">{_bars}</div>'
        f'<h4>the same job, started over</h4>{attempts_html}'
        f'<h4>where the work stopped</h4>{_frontier_short}'
        f'<h4>what the history says</h4>{history_html}')

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{_e(BRAND)} &mdash; {_e(subject)}</title>
<style>{_CSS}</style></head><body>
<header class="topbar"><div class="inner">
<span class="mark">CC</span><span class="word">Code<i>Cobbler</i></span>
<span class="rule"></span><span class="what">codebase map</span>
<span class="subj">{_e(subject)}</span>
<span class="right"><span class="tool">cobblerpy {_e(__version__)}</span>
<span>{_e(stamp)}</span></span>
</div></header>
<div class="wrap"><div class="left">
<h1>{_e(title)}</h1>
<div class="sub">{_e(project.root)} &middot; mapped {stamp} by cobblerpy</div>
<div class="stats">{stats}</div>
{truncation}
{excluded_html}

<div class="note"><span class="proven">Proven</span> &mdash; module structure,
imports, definitions, entry points and unfinished-work signals are read
directly from the syntax.
<span class="inferred">Inferred</span> &mdash; reachability and "nothing uses
this" are LOWER BOUNDS. Python reaches code through dispatch tables, getattr,
decorators, plugin registries and framework callbacks, none of which a parser
can follow. Treat every such finding as "no static path was found", never as
"dead".</div>

<h2>The map</h2>
<p class="lede">Read it top to bottom. Each row is one step further from a start
point, so the top of the chart is where execution begins and everything below it is
something that row depends on. Each card carries the filename, where it lives, how big
it is and who last touched it. Click one for its source, its signals and what it connects
to. A doubled bar instead of an arrowhead marks a call that reaches a body with nothing in
it, and a dashed pink line means this module stopped and another one is doing the same
work.</p>
<div class="mapzone">
<div class="keybar">{svgmap.KEYBAR}<span class="hint" id="keyhint">click a colour to
show only those &middot; Esc clears</span></div>
<div class="legend">{svgmap.LEGEND}</div>
<div class="mapwrap">{svg}</div>
</div>

<input type="search" id="q" placeholder="Filter the tables below...">

</div><!-- /left column -->

<aside id="panel" aria-live="polite">
  <div class="phead">
    <b id="ptitle">project summary</b>
    <span id="phint" class="ln">click a module for its detail</span>
  </div>
  <div id="pbody"></div>
</aside>
<!-- One copy. Rendering the summary into the panel AND keeping a hidden
     original put every table in the page twice, which on a 975-module project
     is megabytes of duplicate markup. The panel is filled from here on load. -->
<div id="summarySource" hidden>{summary_html}</div>

</div><script>{_JS.replace("__PAYLOAD__", payload)}</script></body></html>"""

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path
