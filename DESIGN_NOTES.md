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

### A detector is only as good as its false-positive rate, and that has to be measured

Three of this tool's own detectors were wrong most of the time, and none of them was
detectably wrong from reading the code. Each was found by running the tool over two real
trees and checking every finding against the source.

| Detector | Was | Is | What was wrong |
|---|---|---|---|
| tagged comments | 641 on the corpus | 75 | `tag in text.upper()` — TEMP hides in "attempts", BUG in "debug", NOTE in "DESIGN_NOTES" |
| promised return | 165 | 7 | the word "returns" anywhere in a docstring, including prose about what something else returns |
| commented-out code | 22 | 15 | trailing annotations counted as disabled code |
| unused import | 11 (this repo) | 0 | four were already marked `# noqa: F401` |
| unreachable | 369 `maybe` | 57 | modules a named tool loads were not treated as places execution begins |
| `pass` stubs | 511 on the two corpora | 3 | an empty body is how Python spells "deliberately empty"; 1 of 100 judged was abandoned work. Kept only where the body itself carries a TODO/FIXME, plus an exemption for a class whose every method is empty |
| `NotImplementedError` | 281 | 3 | most abstract methods carry no decorator — they say "subclasses must implement" in prose, which no decorator list can see. Two instruments were measured: an `abc.ABC` exemption moved 3 of 281 findings, a prose exemption moved 182 and left precision where it was. The same in-body marker gate was the one that worked |
| unused import | 787 | 666 | a name used only in a `# type:` comment is used; the scanner could not see the comment form of an annotation |
| undocumented public definition | 3,698 | 78, at weight 0 | 0 true positives in 128 judged. Not an abandonment signal at all: reclassified, gated on a module that documents its others, and given no weight so it cannot rank |

`except: pass` was measured too and nothing was changed. The audit's hypothesis was that a bare
`except:` with an empty body, no `else`/`finally` and no explanatory comment would be defensible.
That subset is 20 findings across the two trees; all 20 were opened and every one was a deliberate
best-effort suppression -- a `close()` in a `__del__`, a debug write, a `flush()` on the way out.
The evidence for narrowing the rule is not there, and neither is the evidence for the rule.

The findings feed the score that ranks where the work stopped, so a detector that is 90%
noise does not merely add rows — it reorders the list somebody reads first.

**The rule that follows:** a finding the reader can dismiss in five seconds teaches them to
dismiss the next one, and the next one might be true. Precision is not a nicety here; it is
the whole basis on which anything else in the report gets believed.

**And measure it on real trees.** Every one of these passed its unit tests. The corpus —
975 modules of somebody's real, half-finished work — is what showed the rate.

### Colour has to mean something specific

Green and red imply a judgement, so the legend must say exactly what each colour is derived from,
or it is decoration that invites a wrong conclusion. What ships:

| State | What the colour is derived from |
|---|---|
| **confirmed** | reached from a start point and exercised by a test |
| **tested** | a test exercises it, but it still carries signals |
| **live** | reached from a start point, nothing unfinished in it |
| **unfinished** | reached, and carrying signals of unfinished work |
| **deadend** | execution reaches here and stops inside it |
| **maybe** | no static path reaches it -- an inference, not a verdict |
| **broken** | this file could not be read |

This table is checked against `svgmap._PALETTE` -- it described a four-colour vocabulary
(green/amber/red/grey) for some time after the map had moved to these seven, which is exactly the
drift the check now prevents.

`maybe` must stay visually distinct from `broken`. "Nothing reaches it" is a lower bound -- Python
dispatches through registries, decorators and getattr -- and colouring it like a file that will not
parse would state as fact the one thing this tool explicitly cannot know.

The key sits above the chart and stays there: it is pinned under the title bar for as long as the
map is on screen, because a chart twelve thousand pixels tall makes a legend at the top a legend
for the first screen only. Clicking a colour in it steps every other module back.

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

Measured effect: the 975-module project 438 to 132, a second private project 72 to 6, this
repository 3 to 3. The survivors are the first ones that read like forks rather than like
popularity -- described rather than named, because that code is not ours to publish:

    a report builder         ->  a second report builder    shares pdf, section, subsection
    a screenshot capture     ->  an end-to-end walkthrough  shares api, capture, wait
    a host-inventory module  ->  a config module            same package, 4 unused imports

**Still not validated against a known positive.** Every change so far has been tuned against
absence -- making wrong answers go away -- and that is not the same as being shown to find a
fork somebody agrees is a fork. Until a codebase with a known, admitted change of direction is
run through it, the output stays labelled a hypothesis.

**No synthetic fixture reproduces a fork.** The gates need vocabulary overlap, co-change history
and matching effects together, which a repository small enough to build inside a unit test does
not generate. A unit test that drove `find()` over such a fixture asserted against an empty list
and passed with the filter deleted; it was removed rather than kept green. The evidence for both
gates is the measurement above.


---

## Who this is for (the author, 2026-09-17)

Recorded because it changes what "done" means for several features, and because the tool had
been drifting towards being a general codebase-describer rather than something aimed at anyone
in particular.

> "The CobblerPy is going to be used by a company that uses python heavily for development.
> Lets imagine that they are a software shop that is building for customers in the healthcare
> industry, they have a pretty high turnover rate for their developers, and that was the call to
> arms for this product, they were dissapointed in how many streams of code they had to just
> abandon because their developers kept leaving when they were mid stream, and the new developers
> werent able to 'pick up where the last guy left off' so they had a ton of abandoned code, all
> of it written with a different developers preference, and when the new developer came in, they
> would try to pick it up, but would end up just starting over, so now, with 4 full fresh
> restarts of half completed code built up, they need a solution that will be able to better
> illustrate the previous trains of thought and could recommend a path forward. This both
> prevents a full restart, and gives the new developer confidence that he is on the right path,
> along with cutting down on the time to completion/ROI on picking up the code rather than
> re-writing."

Four things follow from it, in order of how badly the tool currently falls short.

**1. Competing attempts are not detected at all.** The stated situation is four restarts of ONE
effort. Everything in the tool today looks at one module at a time, or at one module and its
successor. Nothing says "these four files are the same job, attempted four times". That is the
single most valuable thing it could say to this customer and it cannot say it.

**2. There is no recommendation.** The frontier is a ranked list of facts. Ranked facts are not a
path forward, and the brief asks for one explicitly. A recommendation here does not mean guessing
motive -- that stays out. It means: of the attempts at this job, THIS one is furthest along, here
is what it still lacks, and here is what the others have that it does not.

**3. Confidence is the actual product.** "Gives the new developer confidence that he is on the
right path." A rewrite is rarely a technical decision; it is what a developer does when they
cannot tell whether continuing is safe. Every finding therefore has to carry its evidence in a
form the reader can check in seconds, because a recommendation nobody can verify buys nothing --
they will rewrite anyway and be right to.

**4. Style varies by author, so style cannot be a signal.** "All of it written with a different
developers preference." Anything keyed on naming conventions, formatting or docstring habits will
read four authors as four qualities of work. Signals must be structural.

### Not for this customer

Motive still stays out. "Why did they stop" is not recoverable and a guess at it would undermine
the one thing the brief actually asks for, which is grounds for trusting the rest.

## Which restart group is worth the afternoon (2026-09-21)

The salvage list ranks by lines, which is the right answer to "where do I spend an afternoon".
It did not answer "which of these is worth the afternoon", and neither did the restart groups:
they were sorted by `-len(attempts)`, so five half-finished stabs at a 40-line helper outranked
two serious stabs at a 1,200-line subsystem. A restart count says how often somebody gave up.
It says nothing about how much code is sitting in the group.

What a reader does with a group is open its furthest-along attempt and carry on, so the group is
worth what that one file hands back:

    at stake = lines(resume_at) x (resume_percent / 100) x (1 - already elsewhere)

`already elsewhere` is the share of the resume module's own specific definition names -- the same
set the matching uses -- that at least one OTHER attempt in the group also defines. Scoped to the
group, because the question is what is lost by leaving this group alone. It is not the `elsewhere`
map on the group, which points the other way: that lists what the siblings hold and the resume
module does not.

Whether the sibling's copy of a definition has a real body is deliberately not asked. Measured on
a 979-module, 362k-line tree with 22 restart groups: requiring a filled body changed nothing in
the top five, and only lifted four whole-file backup copies off zero. It buys no ordering anyone
would act on and costs the one sentence a reader can check against the files.

Measured on that tree, old order against new:

| | resume module | lines | complete | elsewhere | attempts | at stake |
|---|---|---|---|---|---|---|
| old 1 | a deploy script | 867 | 90% | 13% | 7 | ~679 |
| old 2 | a combined e2e script | 108 | 70% | 100% | 5 | ~0 |
| old 3 | a PDF builder | 353 | 90% | 100% | 5 | ~0 |
| old 4 | a backup copy of a matrix module | 2,767 | 100% | 100% | 4 | ~0 |
| old 5 | a backup copy of a scanner | 2,261 | 100% | 100% | 4 | ~0 |
| new 1 | a license server | 1,375 | 100% | 12% | 2 | ~1,215 |
| new 2 | a deploy script | 867 | 90% | 13% | 7 | ~679 |
| new 3 | an e2e walkthrough | 1,599 | 90% | 53% | 4 | ~677 |
| new 4 | an archived e2e run | 564 | 90% | 22% | 2 | ~395 |
| new 5 | a 3-phase validation run | 453 | 90% | 29% | 4 | ~291 |

Four of the old top five return nothing. Every definition of the file the tool names in them also
sits in a sibling attempt: two are modules copied into a `backups/` folder, one is five re-dated
copies of a single PDF builder, one a family of re-dated variants of one script. The group with the most work in it -- a 1,375-line
license server that two people wrote twice -- came 21st of 22 under the old order, because it had
only been restarted once.

Thirteen of the 22 groups on that tree score zero for the same reason. That is a finding in its
own right and the ranking now shows it: most of what "the same job, started over" detects on a
working tree is somebody's backup copy, not a restart.

The ranking prints its three inputs on every row and never a bare score, and the lines left to
finish ride beside them without being part of the order, so a small nearly-done group still reads
as a quick win from wherever it sorts.

## A copy is not a restart (2026-09-22)

Sorting those thirteen groups to the bottom was not enough. They were still in a list headed "the
same job, started over", and a reader told that about a `backups/` folder has been told something
that did not happen. A wrong verdict is not fixed by ranking it low.

**Where the cut sits, from the distribution.** Across the 22 groups, the share of the named
module's definitions that a sibling attempt also holds:

    12  13  22  29  50  53  60  60  80   ·   100 ×13

Nothing between 80 and 100. The cut goes in the gap, which puts it at *everything*, and the same
place the conservative rule would have put it. It is stated as **the two files defining the same
set**, not as a share of what all the siblings hold together, for two reasons. The verdict prints
as "every definition in it is also in *one named file*", so the reader has one file to open rather
than four. And one-way containment over-fires: it called a 401-line report builder a copy of a
365-line one that had the same three-name scaffold plus two tables besides -- read side by side
they are 45% the same text and two different documents.

**The filename signal was measured and did not do the work.** The same basename in another
directory fired on 5 of the 22 groups where the definitions fired on 13. A backup/archive
directory *on its own* was worse than useless: it fired on an archived script sharing only 22% of
its definitions with its sibling -- a real, distinct earlier attempt. So the two are used as a
conjunction, same file name *and* one of them under a directory whose name is a generic word for
kept-aside work. On that tree it added exactly one group the definitions missed -- a live module
whose copy under `backups/` is one class behind -- and fired on none of the nine real restarts.
The word list is generic English (`backup`, `archive`, `old`, `legacy`, …) and never a particular
tree's folder names; a rule built from those would be right about one repository.

**Checked against the files, not the rule.** Every pair called a copy was then read by how much of
the two texts is the same: ten are byte-identical, eleven of twelve are 99.9% or more, and the
least alike is 72% -- three enrichment scripts, one per database. The nine left as restarts run
from 77% down to 2%. Text similarity is not used as a signal, because a survey has to work where
the source has not been kept; it was used to check the rule.

Copies are still returned, still carried in `--json`, still marked on the map. They go under their
own heading with their own sentence, and out of the ranked restart list.

## A backup of the winner switched the whole finding off (2026-09-22, same day)

The copy verdict above shipped in the morning and was wrong in its scope by the afternoon. It was
read off the group's **named file alone**: if one sibling defined everything the named file
defined, the entire group was reclassified.

Reproduced on a four-attempt fixture built to the shape of the worked example in the README --
`claims_v3`, `claims_ingest`, `intake_new`, `intake`, sharing four definitions at 76%, 28%, 18%
and 0%. The tool reported `THE SAME JOB, STARTED OVER`, the four percentages, `~36 lines at
stake` and `resume at app/claims_v3.py`. Then a `backups/2026-08/claims_v3.py` was added -- a
copy of the file the tool had just named as the place to resume. The entire restart disappeared:
not demoted, not footnoted, replaced by one row reading `app/claims_v3.py and 4 others ... this
is a copy, not a restart`. Backing up a *losing* attempt was harmless. Backing up the *winner*
swallowed the group. Every inherited codebase has a backup folder in it, so the product's
headline feature was switchable off by the thing it exists to be pointed at.

**The verdict is about a pair, so it is asked between every pair.** A group's members are
partitioned into sets that are the same file as each other, by the same two tests as before --
identical definition sets, or the file-name-plus-kept-aside-directory conjunction -- treated as
transitive, so five re-dated copies of one script come out as one set rather than four
overlapping pairs. One set means the group is a copy end to end. More than one set means a real
restart with a duplicate in it: the restart is reported with one member per set and re-ranked on
what is left, and each set holding more than one file is reported as its own copy under the copy
heading. The representative is the live file rather than the one under the kept-aside directory,
which is the same correction the line-count tie-break made for the same reason.

**Measured on the same 979-module tree.** The matcher still finds 22 groups. 12 are copies end to
end, 7 hold no duplicate at all, 3 hold both. Reported: 25 groups, 10 restarts and 15 copies,
against 9 and 13 before. The restart recovered is the bug in miniature on real data -- a
machine-identity helper has a generated twin of itself beside it, and that twin was being used to
call a licence client a copy as well: two files whose texts are **8%** alike. The three copies
split out of restart groups were checked against the files the same way the original cut was:
they are **87.7%, 99.7% and 100%** the same text as the file they were split from. Nothing moved
that should have stayed.

One module can now appear twice -- once as an attempt in a restart, once in the copy pair its
backup makes. On the map the card is written once, and the restart takes it: "another attempt got
further" is what the reader acts on, and "there is a copy of this under `backups/`" is something
they can see in the file tree. The copy keeps its own card, and both keep their row in the
summary.

## Output paths are checked before the survey, not after it (2026-09-22)

All four of `--map`, `--json`, `--mermaid` and `--drawio` raised `FileNotFoundError`,
`PermissionError` or `IsADirectoryError` out of the writer when the destination folder did not
exist, was unwritable, or was a folder -- **after** the whole survey had run, so the survey was
thrown away with it. `cobble` did the same when the project's parent is read-only, which a
mounted share often is, and from a desktop icon that failure is not visible at all.

Every output path is now checked before a single file is read, and a bad one is one sentence
naming the flag and the reason, with exit 2: `cobblerpy: --json out/s.json: cannot write here
(its folder does not exist)`. Every bad path is named, not just the first, because a person
fixing two mistyped paths one run at a time pays for the refusal twice. The check is the same
one the sibling extraction tool uses, and the sentence is in the voice this package already uses
when the shelf cannot be written.

## No drive letter, and no silent shelf (2026-09-22)

The shelf root was looked for in `CODECOBBLER_HOME`, then in a hardcoded drive letter, ahead of
the documented fallback under the home directory -- under a comment insisting it was not a
hardcoded path. It was. On Windows a mapped drive on that letter is usually a corporate network
share, so a machine that happened to have one silently wrote somebody's shelf and register onto
it, and nothing in the output said where they had gone. The environment variable is the
documented mechanism and is now the only one; the machine-specific value belongs in the launcher
shim the installer writes. A run that puts the shelf anywhere but the default now says where it
put it, and says nothing when it is where it always is.

## file:// URLs are built, not concatenated (2026-09-22)

Four places pasted the scheme onto the front of a path. On Windows that gives
`file://<drive>:\...`, which parses with the **whole path as the hostname** and an empty path, so
every `cobble` run on that platform left a dead link on the shelf and opened nothing. On any
platform a project path holding `#`, `?` or `%` broke the same way -- everything from the `#` on
became a fragment -- and a space went through unencoded. `pathlib.Path.as_uri` percent-encodes
all of it and writes the `file:///C:/...` form Windows reads. A register row written by an older
version can hold a relative path, which `as_uri` refuses, so such a path is handed back as it
came: a row that cannot be linked beats a shelf that will not render.

## The resume pick could be the smaller copy (2026-09-22)

Completeness is a proportion of what each file itself started, so two copies of one module score
identically however far apart their sizes are, and the module *name* settled it. Measured on the
same tree: 15 of the 22 groups have a tie at the top and in 7 of them the name picked the smaller
file. In one, a 2,767-line copy under a backup directory was named "resume here" over the
3,527-line live file it was copied from -- so the advice was to carry on in the copy, and the
work-at-stake figure measured the wrong file. Line count now breaks the tie before the name, and
the name stays as the last resort so two runs over one tree still agree.

## Completeness saturates, and the effort line inherited it (2026-09-22)

Measured across all 67 attempts in those 22 groups: **every one of them has a body on every
definition.** The 0.7 body term is a constant on a tree like this, so the whole spread of the
score comes from the remaining 0.3 -- reachability and tests -- and the group-level number takes
three values in total, 70%, 90% and 100%, with 21 of 22 at 90 or above.

Everything else already computed was measured for spread over the same 22 groups before anything
was changed: TODO and FIXME markers (4 in the entire 979-module tree, 0 in any of the 67
attempts), the group's `lacks` and `common_gaps` (empty for all 22, since nothing is stubbed),
unreferenced definitions (0 for 14 of the 22), the abandonment score (0 for 7 of 22, and mostly
a count of smells in the biggest files), and history (undefined for 4 of the 22, which are
untracked). **Nothing separated them**, and none of those measures what the line claims to
measure anyway, so no re-weighting was invented. A weighting that passes only its own fixtures is
worse than the saturated one it replaced.

What the measurement did find is that the number was wrong, not merely useless. `lines left to
finish` was `lines × (1 − completeness)`, and completeness carries reachability and tests. A
2,809-line file with a body on every definition, which nothing imported and no test named, was
reported as having **~281 lines left to finish it**. So the field now counts what its name says --
the lines in definitions with no body -- and nothing else, and the sentence names the count so it
can be checked. On this tree that is 0 everywhere, and where it is 0 the line is dropped instead
of printing "nothing left unwritten by this measure" on 12 rows out of 22. What the measure does
and does not cover is stated once above the groups rather than on every row.
