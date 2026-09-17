# CobblerPy design notes

Recorded decisions and deferred work. Not documentation -- documentation says what the tool does;
this says what it should do next and why.

Entries attributed to Brandon are the author's, quoted as written and dated. They are kept
verbatim because several of them overturned a design that had already been built: the
attribution is there so a later reader can tell a requirement from an assumption.

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


---

## Where set-aside code "would have fitted" (Brandon, 2026-09-16)

> "separate the workflow from the thoughts and ideas that never made it into the proper flow,
> but still understand where it might fit in ultimately"

The separating half is **built and works** (`clusters.py`). On a real project it told two
copied-in libraries, 10,992 lines, apart from the 12,206-line application, using only three
questions: does anything start here, does the project reach it, does the project mention itself
anywhere near it.

The **attachment** half -- "where would this have fitted" -- was attempted and mostly failed.
Recording the attempt so it is not repeated blind.

### What was tried

Find names the live code calls that nothing live defines or imports, then see whether the
set-aside code defines them. On paper this is near-proof of intended attachment: the live code
reaches for something that does not exist, and the orphan provides it.

### What actually happened

221 unresolved names, of which 4 were defined in the set-aside code: `execute` (38 uses),
`close` (23), `flush` (5), `run` (3). Every one is a generic method name -- `execute` at 38 uses
is a database cursor, not `pidb.database.execute`. **Four candidates, four false positives.**

Vocabulary overlap fared similarly: the top shared words were `get`, `init`, `parse`, `create`,
`run`. Generic verbs dominate any two Python codebases.

**One real signal emerged**: `ingest` (5 uses live, 11 definitions set aside), which does suggest
`thetabase.security.ingest` was meant to serve `backend.app.services.finding_ingestor`. One hit
out of roughly twenty candidates is not a feature.

### What would be needed to make it work

- **Distinctiveness weighting.** A name shared with the standard library or any common ORM
  carries no information. Requires a baseline frequency corpus, which is a dependency-free but
  non-trivial asset to build and ship.
- **Multi-signal agreement.** Name match AND compatible arity AND domain-vocabulary overlap AND
  matching external effects. Any one alone is noise.
- **Module-level rather than name-level.** Compare a whole orphan module's profile against each
  live module's, and report the best match as a hypothesis with its evidence -- not a claim.

### The honest boundary

This is probably where deterministic analysis ends. "Where would this have fitted" is a question
about INTENT, and intent left with the developer. A model reading the two modules side by side
would do better than any heuristic here -- and the right input for it is the structured model
this tool already produces, not raw source.

So: ship the separation, which is provable. Offer attachment as a clearly-labelled hypothesis,
generated by narration, never presented as a finding.


---

## Dead-ends: where the flow trails off (Brandon, 2026-09-16)

> "pop up the code snippets where the trailing off occurs and it doesnt connect to the next
> process or action ... maybe even some hints as to why they quit, or what wasnt working at that
> point that caused them to change direction"

This is the map's most valuable interaction, and it is more specific than "click a node".

A DEAD-END is a place execution can reach where nothing happens. Four shapes, all computable
from the call graph plus the body classification already extracted:

| Shape | What it means |
|---|---|
| a called function whose body is `pass`, `...` or `NotImplementedError` | the caller was written, the callee never was |
| a call to a name nothing in the project defines | the flow reaches for something that does not exist |
| an `except: pass` on a path the flow uses | the error case was deferred and never returned to |
| an import that is never used in a module that otherwise works | the intent to connect something, unconsummated |

The first is the strongest. Somebody working top-down writes the caller, stubs the callee, and
runs out of road at that boundary -- so **the stub's name and its caller together say what they
were reaching for when they stopped.** That is as close to a train of thought as static analysis
gets, and unlike intent inference it is a FACT about the code rather than a guess.

### Rendering

Dead-ends are drawn on the edge, not the node: the call arrow terminates in a stub marker rather
than an arrowhead, so the eye lands on the break rather than the box. Clicking it opens both
sides at once -- the calling lines and the empty body -- because neither half explains the
situation alone.

### The honest limit

A stub is not always abandonment: abstract base classes, protocol definitions and deliberate
no-op hooks are all legitimately empty, and a plugin interface is *supposed* to raise
NotImplementedError. The marker says "execution reaches here and stops", which is true; whether
that is a problem is the reader's call, and the caveat travels with it.

**Motive stays out of the output entirely.** Corrected by Brandon, 2026-09-16, and the correction
improves the design:

> "when you hit a stub, the why is not important, its important to know where it stands, that is
> all, maybe even have a descriptor that shows the direction it was heading, and likely components
> that effort was diverted to (you didnt need the remediation as much as you needed the report of
> why remediation was required)"

So a stub gets three things, none of them a motive:

**POSITION** -- what calls it, what it sits between, how far it is from an entry point. Already
computed.

**DIRECTION** -- what it was shaped to do, read off the name, the signature, and what its finished
siblings in the same module do. A stubbed `remediate(finding)` beside a working `report(finding)`
and `assess(finding)` says where it was heading without anybody guessing.

**DIVERSION** -- what absorbed the effort afterwards. This is the interesting one, and it is a
FACT from the history rather than an inference: the work did not stop, it moved, and the commits
say where. Brandon's own example: the remediation engine was stubbed because what was actually
needed was the report explaining why remediation was required. The effort went sideways, and that
is visible.


---

## Diversion detection: attempt one, and why it produced nothing (2026-09-16)

The idea: for a file that went quiet, show what the commits afterwards went to. Work that stops
in one place usually reappears in another, and that redirection is the most useful thing the
history can say about a dead-end.

**Measured on a 13-commit repository, it returned the same three files for every quiet module.**
Those three were simply the ones that changed in almost every commit. Raw "what changed
afterwards" is dominated by hub files -- the test module, the CLI, the central service -- and
reports them regardless of what went quiet. That is not diversion, it is popularity.

It also reported pre-rename paths, because the prototype did not apply the rename map that
`history.py` already builds. Third time that trap has appeared; anything reading the log needs it.

### Brandon's refinement, 2026-09-16: it is a FORK, not a successor

> "diversion is a fork, you will see the effort continue, if the function is what they have
> persued, and suddenly they move to a similar but alternate effort in the PR's, that gives us a
> pretty good idea this is where the decided to go with a new path."

This is the discriminator the first attempt lacked. The signal is not "what changed next" -- that
is whatever file always changes. It is "effort on A stopped and effort on A-PRIME started", where
A-prime is doing the SAME KIND OF WORK by another route. Similarity is the whole signal; timing
alone is noise.

So the related-files restriction below is not a refinement of the feature, it IS the feature.
Candidates for A-prime, strongest first: a module sharing the abandoned one's domain vocabulary;
a historical co-change partner; a sibling in the same package; a module with the same external
effects. A fork should require at least two of those to agree.

### What would make it a real signal

- **Normalise against each file's own baseline rate.** The question is not "what changed after"
  but "what changed MORE than it usually does, after". A hub that always changes tells you
  nothing; a module that was quiet and then became busy tells you a lot.
- **Restrict to related files.** Same package, or a historical co-change partner, or overlapping
  domain vocabulary. "Remediation went quiet and reporting picked up" is a finding; "remediation
  went quiet and the test file kept changing" is noise.
- **Require a gap.** Diversion means the effort moved at roughly the moment the file stopped, not
  at any point in the subsequent year.

Until it does those three things it stays out of the product. A map that confidently points at
the busiest file in the repository as "where the effort went" would be worse than saying nothing,
because it looks like an answer.


---

## Diversion: shipped, conservatively, after three attempts (2026-09-16)

Each attempt failed in a different way and each failure named the next
discriminator. Recorded because the sequence is the actual design.

**Attempt one -- "what changed after".** Returned the same three files for every
quiet module: whichever ones changed in nearly every commit. Popularity, not diversion.

**Attempt two -- add similarity.** Vocabulary, package, co-change and shared effects, requiring
two to agree. Produced confident, meaningless pairs: `limits` -> `batch`, which is a dependency
relationship; two deploy scripts that resemble each other. The flaw: **finished code is also
quiet.** Every candidate was a healthy, complete module. Quiet does not mean abandoned.

**Attempt three -- require the quiet side to be unfinished.** Down from nine findings to three.
But all three pointed at the test module, because a test shares vocabulary with everything it
tests and co-changes with everything. And the one remaining non-test pair rested on
`package + effects`, which is true of half a codebase.

**Shipped version** adds two more gates: test modules can never be a destination, and at least
one signal must be SPECIFIC to the pair (vocabulary or co-change) rather than two generic ones
agreeing. Result on the two available codebases: one weak candidate and zero.

### The limitation that matters

**Neither codebase here contains a known fork**, so this has been tuned against absence rather
than validated against a positive. Zero on a project that was abandoned wholesale rather than
redirected is plausibly the right answer, but "produces almost nothing" is not the same as
"produces the right thing". It ships labelled as a hypothesis with its evidence attached, and it
would take a codebase with a documented change of direction to know whether it works.


---

## Diversion, third pass (2026-09-16): hubs, and who may be the abandoned side

Run against four real repositories, the shipped detector was still doing the thing the first
attempt did wholesale. On a 975-module project **one destination was proposed for 85% of all
stopped modules and a second for 80%**. The similarity gates cannot catch that: a large file
shares vocabulary with everything and touches every outside system, so it satisfies them all.

Only its SHARE of the findings exposes it. A destination named for more than 10% of the stopped
modules is not a fork of any one of them; the cap has a floor of two so that on a small project,
where one destination out of three findings is 33% by arithmetic, the rule stays quiet.

The abandoned side needed a gate too. Test modules were already barred as destinations and not
as sources, so on one project the top of the list was tests that had gone quiet -- which is what
a passing test does.

Measured effect: ledger 438 to 132, a second private project 72 to 6, this repository 3 to 3. The survivors are
the first ones that read like forks rather than like popularity:

    build_lab_test_report_pdf  ->  build_api_reference_pdf     shares pdf, section, subsection
    capture_screenshots        ->  e2e_screenshot_walkthrough  shares api, capture, wait
    windows_estate             ->  agent_config                same package, 4 unused imports

**Still not validated against a known positive.** Every change so far has been tuned against
absence -- making wrong answers go away -- and that is not the same as being shown to find a
fork somebody agrees is a fork. Until a codebase with a known, admitted change of direction is
run through it, the output stays labelled a hypothesis.

**No synthetic fixture reproduces a fork.** The gates need vocabulary overlap, co-change history
and matching effects together, which a repository small enough to build inside a unit test does
not generate. A unit test that drove `find()` over such a fixture asserted against an empty list
and passed with the filter deleted; it was removed rather than kept green. The evidence for both
gates is the measurement above.
