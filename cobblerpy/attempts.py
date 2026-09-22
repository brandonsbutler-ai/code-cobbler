"""Competing attempts: the same job, started over more than once.

The situation this exists for is a developer leaving mid-stream, the next one
failing to reconstruct the reasoning, and starting again -- four times over,
each attempt abandoned at a different point and written to a different taste.

Everything else in this package looks at one module, or at one module and its
successor. Nothing said "these four files are the same job, attempted four
times", which is the most useful sentence available to a reader in that
position.

WHAT MAKES TWO FILES THE SAME JOB

Shared DEFINITION NAMES, and nothing softer. If two modules both define
`parse_claim`, `validate_claim` and `submit_claim`, they are attempts at the
same thing regardless of who wrote them or how. Generic names are excluded,
because every module defines `main` and `run`.

Style is deliberately not a signal, and not because it would be hard. The whole
premise is that each attempt was written to a different developer's preference,
so anything keyed on naming conventions, formatting or docstring habits reads
four AUTHORS as four qualities of work. Every signal here is structural.

HOW FAR ALONG AN ATTEMPT IS

The fraction of its definitions that have real bodies, adjusted by whether
anything reaches it and whether tests exercise it. Docstrings are not counted:
one developer documents everything and the next documents nothing, and scoring
that would rank the tidiest author rather than the furthest-advanced work.

The number is a proportion of what that file itself started, not a percentage
of some imagined finished feature. It answers "how much of this attempt is
filled in", which is the question that decides whether continuing beats
starting over.

WHICH GROUP IS WORTH THE AFTERNOON

Groups are ranked by the WORK AT STAKE in them: the lines of the furthest-along
attempt, taken down by how much of it is written and by how much of it a
sibling attempt already holds. That is what a reader gets back for resuming it.

How many times the job was restarted is a tie-break and nothing more. It says
how often somebody gave up, which is a fact about the team, not about how much
code is sitting in the group waiting to be picked up.

A COPY IS NOT A RESTART

Most of what this finds on a working tree is not a restart at all. Measured
2026-09-22 on a 979-module tree, 22 groups: in 13 of them one single sibling
defines EVERY specific name the named module defines. Reading them says what
they are -- five 353-line PDF builders differing by a version number in the
filename, modules copied into a backup folder beside the live ones, re-dated
variants of one script. "Somebody restarted this four times" is a false
sentence about a backup folder, not merely a low-ranked one, so those groups
carry `verdict: "copy"`, get their own sentence naming the file that holds
everything, and sort below every real restart.

A COPY IS TAKEN OUT OF ITS GROUP, NOT USED TO CONDEMN IT

The verdict is about a PAIR of files, so it is asked between every pair in a
group and never only between the named file and its siblings. Fixed
2026-09-22, the day the verdict landed: it was read off the named file alone,
so four genuine competing attempts plus a copy of the winning one under
`backups/` reported no restart at all -- the attempts, the percentages and
the resume line were not demoted, they were gone, and one copy row stood in
for the lot. Backing up a LOSING attempt was harmless; backing up the WINNER
swallowed the group. A backup folder must not be able to switch off the one
finding this package is named for.

So a group's members are partitioned into sets that are the same file as each
other. One set means the whole group is a copy. More than one means a real
restart with a duplicate in it: the restart is reported with one member per
set -- the live file, not the one under the kept-aside directory -- and each
set of more than one file is reported as its own copy, under the copy
heading. The restart is then ranked on what is actually left in it.

Measured 2026-09-22 on a 979-module tree. The matcher finds the same 22
groups either way. 12 of them are copies end to end and 7 hold no duplicate
at all; the remaining 3 hold both, and splitting them reports 25 groups -- 10
restarts and 15 copies, where naming the whole group off its best file gave 9
and 13. The restart recovered is the clearest reading of the bug on real
data: a machine-identity helper had a generated twin of itself beside it, and
that twin was being used to call a licence client a copy as well -- two files
whose texts are 8% alike. The three copies split out are 87.7%, 99.7% and 100%
the same text as the file they were split from, so nothing was moved that
should have stayed.

The cut came from the distribution, not from a round number. Across the 22
groups the share of the named module's definitions that a sibling also holds
came out 12, 13, 22, 29, 50, 53, 60, 60, 80, and then 100 thirteen times: a
clean gap between 80% and 100% with nothing in it. So the cut sits at
everything. It is stated as the two files defining the SAME SET rather than as
a percentage of a union, because every group above the gap had a single
sibling holding the lot -- which gives the reader one file to check the
verdict against instead of four -- and because one-way containment alone let a
file through whose sibling merely had it plus two more.

A filename signal was measured against the same 22 groups and does NOT do the
work: the same basename in another directory fired on 5 groups where the
definitions fired on 13, and a backup/archive directory ON ITS OWN fired on a
group with only 22% overlap -- a genuinely distinct script that had been
archived. It is used only as the conjunction in `_copied_aside`, which adds
exactly one group the definitions miss (a live file whose backup copy is one
class behind) and fired on no genuine restart.
"""

import os
import re
from collections import defaultdict

# Definition names too common to mean anything. Sharing `main` is not evidence.
_GENERIC = {
    "main", "run", "start", "stop", "setup", "teardown", "init", "__init__",
    "get", "set", "load", "save", "read", "write", "open", "close", "parse",
    "format", "handle", "process", "execute", "call", "send", "receive",
    "create", "update", "delete", "list", "find", "search", "build", "make",
    "to_dict", "as_dict", "from_dict", "serialize", "deserialize", "validate",
    "check", "test", "helper", "wrapper", "factory", "__repr__", "__str__",
    "__enter__", "__exit__", "configure", "connect", "disconnect", "reset",
}

# How many specific definition names two modules must share to be called
# attempts at one job. Two is too loose -- a pair of files can share
# `parse_claim` and `submit_claim` by both being claim-shaped without being
# the same effort. Three has held on every tree this was run against.
MIN_SHARED = 3

# Bodies that are not filled in.
_EMPTY = {"pass", "ellipsis", "raise"}

# Directory names that say "this is a copy kept aside", in the generic English
# a project uses for it. Deliberately words and not paths: a private tree's own
# folder names have no business in a package anybody can install, and a rule
# built from them would only ever be right about one repository.
#
# On its own this is NOT evidence. Measured 2026-09-22 on a 979-module tree it
# fired on a group whose named file shares only 22% of its definitions with its
# sibling -- a real, distinct script somebody had archived. It is only ever
# used with a matching filename, in `_copied_aside`.
_KEPT_ASIDE = {
    "backup", "backups", "bak", "archive", "archived", "archives", "old",
    "orig", "original", "copy", "copies", "previous", "prev", "deprecated",
    "legacy", "obsolete", "attic", "saved", "snapshot", "snapshots",
}

_WORD = re.compile(r"[^a-z0-9]+")


def _kept_aside_dir(relpath):
    """The directory component that says this file is a copy kept aside."""
    for part in os.path.dirname(str(relpath).replace("\\", "/")).split("/"):
        for word in _WORD.split(part.lower()):
            if word in _KEPT_ASIDE:
                return part
    return None


def _copied_aside(best_relpath, other_relpath):
    """True when these two are one file in two places, by their PATHS alone.

    The same file name in another directory is not two attempts at a job, and
    when one of the two directories is called `backups` or `archive` it is
    somebody's copy. Both halves are needed: a tree with
    `backends/postgres/store.py` and `backends/mysql/store.py` shares the
    basename and is two real implementations, and an archived script that
    shares no name with the live one is a real earlier attempt.
    """
    a, b = str(best_relpath).replace("\\", "/"), str(other_relpath).replace("\\", "/")
    if os.path.basename(a).lower() != os.path.basename(b).lower():
        return None
    if os.path.dirname(a) == os.path.dirname(b):
        return None
    return _kept_aside_dir(a) or _kept_aside_dir(b)


def _specific_definitions(module):
    """Top-level definition names worth comparing between modules."""
    out = set()
    for definition in module.definitions:
        if definition.parent:
            continue                      # a method's name repeats everywhere
        name = definition.name
        if name.lower() in _GENERIC or name.startswith("_"):
            continue
        if len(name) < 4:
            continue
        out.add(name)
    return out


def _is_test_module(key):
    parts = str(key).lower().replace("-", "_").split(".")
    return any(p in ("tests", "test", "conftest") or p.startswith("test_")
               or p.endswith("_test") for p in parts)


def completeness(module, reachable, tested):
    """(score, facts) for how far along one attempt is.

    `score` is 0.0-1.0 and `facts` says how it was reached, because a number
    nobody can check is a number nobody will act on.
    """
    tops = [d for d in module.definitions if not d.parent and d.kind != "class"]
    filled = [d for d in tops if d.body_kind not in _EMPTY]
    stubs = [d for d in tops if d.body_kind in _EMPTY]

    if not tops:
        body_ratio = 0.0
    else:
        body_ratio = len(filled) / len(tops)

    score = body_ratio * 0.7
    facts = [f"{len(filled)} of {len(tops)} definitions have bodies"]
    if stubs:
        facts.append("still stubbed: " + ", ".join(sorted(d.name for d in stubs)))

    if reachable:
        score += 0.2
        facts.append("reachable from an entry point")
    else:
        facts.append("nothing reaches it")

    if tested:
        score += 0.1
        facts.append(f"{tested} test{'s' if tested != 1 else ''} refer to it")
    else:
        facts.append("no tests refer to it")

    # Unfinished-work markers pull it back, but never below what the bodies
    # alone earned: a TODO in otherwise complete code is a note, not a hole.
    todos = len(getattr(module, "todos", []) or [])
    if todos:
        score = max(body_ratio * 0.7, score - min(todos * 0.03, 0.12))
        facts.append(f"{todos} TODO marker{'s' if todos != 1 else ''}")

    return round(min(score, 1.0), 3), facts


def _lineage(ranked):
    """The attempts in the order the work actually grew, with what each added.

    Ordered by what each attempt DEFINES rather than by when it was committed.
    Dates say when a file was last touched, which on a squashed or rebased
    history is one timestamp for everything; the definition sets survive that,
    and on the shape this exists for they nest -- each developer kept the core
    and reached for one more thing. Read in this order the group stops being
    four rows and becomes a sequence somebody can follow.

    Ties break on completeness, so a pair that defines exactly the same names
    still comes out in a stable, defensible order.
    """
    order = sorted(ranked, key=lambda a: (len(a["defines"]), a["score"]))
    out, seen = [], set()
    for attempt in order:
        defines = set(attempt["defines"])
        out.append({
            "module": attempt["module"],
            "relpath": attempt["relpath"],
            "percent": attempt["percent"],
            "added": sorted(defines - seen),
            "dropped": sorted(seen - defines),
            "carried": sorted(defines & seen),
        })
        seen |= defines
    return out


def _common_gaps(ranked, stub_names):
    """Definitions that EVERY attempt left unfinished.

    The most useful sentence a survey of four restarts can produce, and a list
    cannot say it: the reader has to intersect four separate "still stubbed"
    lines themselves to notice that four developers all stopped in the same
    place. Where they all stopped is where the actual problem is -- it is
    rarely the code, and a fifth attempt will stop there too.
    """
    sets = [stub_names.get(a["module"], set()) for a in ranked]
    if not sets or any(s is None for s in sets):
        return []
    common = set.intersection(*sets) if sets else set()
    return sorted(common)


def _same_file_sets(ranked):
    """Partition a group's attempts into sets that are THE SAME FILE.

    Two attempts are the same file when they define the same SET of specific
    names, or when `_copied_aside` recognises one name in two places with a
    kept-aside directory on one side -- exactly the two tests the verdict has
    always used, applied between every pair in the group instead of only
    between the named file and its siblings.

    Sameness is treated as transitive and the sets come back as connected
    components: five re-dated copies of one script are five files that are
    each other's copy, and reporting them as four separate pairs would say
    the same thing four times.

    Each set keeps the order `ranked` was in, so the furthest-along member of
    a set is its first element.
    """
    parent = {a["module"]: a["module"] for a in ranked}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for i, a in enumerate(ranked):
        for b in ranked[i + 1:]:
            if not (set(a["defines"]) == set(b["defines"])
                    or _copied_aside(a["relpath"], b["relpath"])):
                continue
            ra, rb = root(a["module"]), root(b["module"])
            if ra != rb:
                parent[rb] = ra

    sets = defaultdict(list)
    for attempt in ranked:
        sets[root(attempt["module"])].append(attempt)
    return [sets[key] for key in
            sorted(sets, key=lambda k: [a["module"] for a in ranked].index(k))]


def _live_one(members):
    """Which of several copies of one file stands for it in the restart group.

    The live file, not the copy: a set is already ordered furthest-along
    first and then by size, and that alone named a 2,767-line copy under a
    backup directory over the 3,527-line file it came from on the tree
    measured 2026-09-22. A path that says "kept aside" says which one the
    reader is meant to carry on in, so it is asked first; with nothing to
    separate them the set's own order decides, and two runs over one tree
    still agree.
    """
    for attempt in members:
        if _kept_aside_dir(attempt["relpath"]) is None:
            return attempt
    return members[0]


def _describe(ranked, modules_by_key):
    """One reported group, built from the attempts it actually holds.

    Takes a ranked list rather than a set of module keys because a
    matched group can be reported as MORE THAN ONE group: a copy kept
    beside a real restart is split off and described on its own, and
    the restart is described again without it. Every number here --
    what the group shares, what is at stake, what sits elsewhere -- is
    therefore computed from `ranked` and never from the wider group.
    """
    # What the group shares, recomputed from its final membership: names
    # defined by at least two of them. Intersecting pairwise sets as the
    # group grew emptied it -- A and B can share three names, B and C
    # another three, and the intersection of all three be nothing, which
    # printed a group with no evidence attached to it at all.
    seen = defaultdict(int)
    for attempt in ranked:
        for name in attempt["defines"]:
            seen[name] += 1
    shared_names = sorted(n for n, count in seen.items() if count > 1)
    best = ranked[0]
    stub_names = {}
    for attempt in ranked:
        module = modules_by_key[attempt["module"]]
        stub_names[attempt["module"]] = {
            d.name for d in module.definitions
            if not d.parent and d.kind != "class" and d.body_kind in _EMPTY}
    others = ranked[1:]

    elsewhere = {}
    for other in others:
        extra = set(other["defines"]) - set(best["defines"])
        if extra:
            elsewhere[other["module"]] = sorted(extra)
        if other["tests"] and not best["tests"]:
            elsewhere.setdefault(other["module"], [])

    # WHAT IS AT STAKE IN THE GROUP
    #
    # The reader's move with a group is to open its furthest-along attempt
    # and carry on, so the group is worth what that one file hands back:
    # its size, taken down by how much of it is actually written, and taken
    # down again by how much of it can be had from a sibling attempt
    # anyway.
    #
    # `elsewhere_percent` is the share of the RESUME module's own specific
    # definition names -- the same set the matching uses: top level, not
    # generic, four characters or more -- that at least one OTHER attempt
    # in this group also defines. Scoped to the group and not to the whole
    # project, because the question is what is lost if this group is left
    # alone, and the sibling attempts are the only other place the group's
    # own work sits. Note this is not the `elsewhere` map above, which
    # points the other way: that lists what the SIBLINGS hold and the
    # resume module does not.
    #
    # Whether the sibling's copy of a definition has a real body is
    # deliberately not asked. Measured 2026-09-21 on a 979-module, 362k
    # line tree with 22 restart groups: requiring a filled body changed
    # nothing in the top five and only lifted four groups of near-identical
    # copies off zero. It buys no ordering a reader would act on, and it
    # costs the one sentence they can check against the files themselves.
    best_defines = set(best["defines"])
    siblings = set()
    for other in others:
        siblings |= set(other["defines"])
    elsewhere_share = (len(best_defines & siblings) / len(best_defines)
                       if best_defines else 0.0)
    resume_lines = getattr(modules_by_key[best["module"]], "loc", 0) or 0
    done = best["percent"] / 100.0
    at_stake = resume_lines * done * (1.0 - elsewhere_share)

    # IS THIS A COPY RATHER THAN A RESTART
    #
    # Looked for in ONE sibling, not across all of them together: the
    # verdict is printed as "every definition is also in <file>", and a
    # reader has to be able to open that one file and see it. Measured
    # 2026-09-22 on a 979-module tree, all 13 groups whose definitions are
    # covered by the siblings TAKEN TOGETHER also had a single sibling
    # holding the lot, so nothing is lost by asking for the stronger thing.
    #
    # The containment runs BOTH ways -- the two files define the same set,
    # neither one more. One-way containment fired on a pair of PDF builders
    # sharing a three-name scaffold where the other file had two report
    # tables besides; read side by side they are 45% the same text and two
    # different documents, and "a copy" is the wrong word for them.
    #
    # Every pair this does call a copy was then read against the files
    # themselves on that tree, by how much of the two texts is the same:
    # ten are byte-identical, eleven of the twelve are 99.9% or more, and
    # the least alike is 72% -- three enrichment scripts, one per database.
    # The nine groups left as restarts run from 77% down to 2%. Text
    # similarity is NOT used as a signal here, because a survey has to work
    # where the source has not been kept; it was used to check this rule.
    copy_of = next((o for o in others if best_defines == set(o["defines"])),
                   None)
    copy_why = "every definition in it is also in" if copy_of else ""
    if copy_of is None:
        # The filename conjunction, which the definitions miss when the
        # copy is a little behind the original. On that same tree this
        # added exactly one group -- a live module whose copy under a
        # backup directory is one class older -- and fired on none of the
        # nine real restarts.
        for other in others:
            aside = _copied_aside(best["relpath"], other["relpath"])
            if aside:
                copy_of = other
                # Written so it reads the same whichever of the two is
                # the one under the kept-aside directory: the tie-break
                # names the bigger file, which is usually but not always
                # the live one.
                copy_why = (f"the same file name in two places, one of "
                            f"them under {aside}/ -- the other is")
                break

    # WHAT IS LEFT TO WRITE IN THE FURTHEST ATTEMPT
    #
    # Counted from the definitions that HAVE NO BODY, and from nothing
    # else. It used to be `lines x (1 - completeness)`, and completeness
    # also carries whether anything reaches the file and whether tests
    # mention it -- so a file where every definition was written, nothing
    # imported it and no test named it was reported as "~281 lines left to
    # finish it" when there was not a single unwritten line in it. That is
    # a wrong number, not a vague one.
    #
    # Measured 2026-09-22 across all 67 attempts in the 22 groups on a
    # 979-module tree: every one of them has a body on every definition,
    # so this is 0 everywhere and `effort_sentence` prints nothing at all.
    # That is the honest reading of a finished tree, and better than the
    # old line, which put a number on 10 of the 22 and called the other 12
    # "nothing left unwritten by this measure".
    tops = [d for d in modules_by_key[best["module"]].definitions
            if not d.parent and d.kind != "class"]
    unwritten = [d for d in tops if d.body_kind in _EMPTY]
    unwritten_share = len(unwritten) / len(tops) if tops else 0.0

    return {
        "shared": shared_names,
        "attempts": ranked,
        "lineage": _lineage(ranked),
        "common_gaps": _common_gaps(ranked, stub_names),
        "resume_at": best["module"],
        "resume_relpath": best["relpath"],
        "resume_percent": best["percent"],
        # The numbers the ranking is made of, named so a reader gets
        # them without the formula. The line figures are estimates off a
        # line count and a completeness proportion, which is why they are
        # printed with a `~` beside them.
        "resume_lines": resume_lines,
        "elsewhere_percent": int(round(elsewhere_share * 100)),
        "at_stake_lines": int(round(at_stake)),
        # A copy carries the verdict, the file that holds everything and
        # the reason, so no renderer has to work any of it out and every
        # one of them says the same thing. `copy_of` is None on a restart.
        "verdict": "copy" if copy_of else "restart",
        "copy_of": copy_of["module"] if copy_of else None,
        "copy_of_relpath": copy_of["relpath"] if copy_of else None,
        "copy_reason": copy_why,
        # Effort to finish, which is NOT the ranking: the lines of the
        # furthest attempt that sit in definitions with no body yet.
        # A nearly-done group has to be recognisable as a quick win on the
        # row where it sorts, or ranking by size hides the cheap wins the
        # way ranking by count hid the big ones.
        "remaining_lines": int(round(resume_lines * unwritten_share)),
        "unwritten_definitions": len(unwritten),
        "resume_facts": best["facts"],
        "lacks": [f for f in best["facts"] if f.startswith("still stubbed")],
        "elsewhere": elsewhere,
        "caveat": ("attempts are matched on shared definition names, not on "
                   "style -- two files can share names and be unrelated, and "
                   "the names are listed so you can see which"),
    }


def find(project, modules_by_key, origins=None):
    """Groups of modules that are competing attempts at one job.

    Returns a list of groups, ordered by the work at stake in each, and each
    ordered internally with the furthest-along attempt first, carrying what
    that attempt has, what it lacks, and what the others hold that it does not.
    """
    origins = origins or {}
    defs_by_key = {}
    for key, module in modules_by_key.items():
        if origins.get(key, {}).get("origin") == "vendored":
            continue                      # somebody else's code is not a restart
        if _is_test_module(key):
            continue                      # a test is not an attempt at the job
        names = _specific_definitions(module)
        if len(names) >= MIN_SHARED:
            defs_by_key[key] = names

    # Which modules do tests mention? Attributed to the module, not the test.
    tested = defaultdict(int)
    for key, module in modules_by_key.items():
        if not _is_test_module(key):
            continue
        mentioned = {t.split(".")[0] for t in getattr(module, "names_used", set())}
        for other in defs_by_key:
            tail = other.split(".")[-1]
            if tail in mentioned:
                tested[other] += sum(
                    1 for d in module.definitions if not d.parent)

    # Pair up, then merge pairs that share a member into one group: four
    # attempts at one job are six pairs, and reporting six pairs would bury
    # the one fact worth having.
    groups = []
    keys = sorted(defs_by_key)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            shared = defs_by_key[a] & defs_by_key[b]
            if len(shared) < MIN_SHARED:
                continue
            for group in groups:
                if a in group["modules"] or b in group["modules"]:
                    group["modules"].update((a, b))
                    break
            else:
                groups.append({"modules": {a, b}})

    reachable = set(getattr(project, "reachable", ()) or ())
    entry = {n for n, _ in project.entry_points}

    out = []
    for group in groups:
        members = sorted(group["modules"])
        if len(members) < 2:
            continue
        ranked = []
        for key in members:
            module = modules_by_key[key]
            score, facts = completeness(
                module, key in reachable or key in entry, tested.get(key, 0))
            ranked.append({
                "module": key,
                "relpath": module.relpath,
                "score": score,
                "percent": int(round(score * 100)),
                "facts": facts,
                "defines": sorted(defs_by_key[key]),
                "tests": tested.get(key, 0),
                "lines": getattr(module, "loc", 0) or 0,
            })
        # Furthest along first, and where two attempts are equally far along,
        # the BIGGER one. Completeness is a proportion of what each file
        # itself started, so two copies of one module score the same however
        # far apart their sizes are -- and the name used to settle it.
        # Measured 2026-09-22 on a 979-module tree: 15 of the 22 groups have a
        # tie at the top, and in 7 of them the name picked the smaller file.
        # In one, a 2,767-line copy under a backup directory was named "resume
        # here" over the 3,527-line live file it was copied from: the advice
        # was to carry on in the copy, and the work-at-stake figure then
        # measured the wrong file. The name stays as the last resort, so two
        # runs over one tree still agree.
        ranked.sort(key=lambda r: (-r["score"], -r["lines"], r["module"]))
        # THE SAME FILE IS NOT A RIVAL ATTEMPT
        #
        # Regression fixed 2026-09-22, the day the copy verdict landed. The
        # verdict was read off the group's NAMED file alone: if one sibling
        # held everything the named file held, the whole group was
        # reclassified. So four genuine competing attempts at one job, plus a
        # copy of the winning one under `backups/2026-08/`, printed no restart
        # at all -- the attempts, the percentages and the resume line were not
        # demoted, they were gone. Backing up a LOSING attempt was harmless;
        # backing up the WINNER swallowed the group. A backup folder must not
        # be able to switch off the finding this tool is named for.
        #
        # So the copy is taken OUT of the group and the group is re-ranked on
        # what is left, instead of four files being judged on one of them.
        # The members are partitioned into sets that are the same file as each
        # other; one set means the whole group is a copy, which is what all 13
        # copy groups on the 979-module tree measured 2026-09-22 are, so that
        # split is unchanged. More than one set means a real restart with a
        # duplicate in it: the restart is reported with one member per set,
        # and each set holding more than one file is reported as its own copy.
        same_file = _same_file_sets(ranked)
        if len(same_file) == 1:
            out.append(_describe(ranked, modules_by_key))
            continue
        standing = sorted((_live_one(members) for members in same_file),
                          key=lambda r: (-r["score"], -r["lines"], r["module"]))
        out.append(_describe(standing, modules_by_key))
        for members in same_file:
            if len(members) > 1:
                out.append(_describe(members, modules_by_key))
    # Ranked by the working code resuming it hands back, not by how many
    # times the job was restarted. The old key was -len(attempts), which put
    # five half-finished stabs at a 40-line helper above two serious stabs at
    # a 1,200-line subsystem: a restart count says how often somebody gave up,
    # never how much is in it. Measured 2026-09-21 on a 979-module tree: in
    # two of the old first three groups, every definition in the file the tool
    # named also sits in a sibling attempt -- one is five re-dated copies of a
    # single script -- so resuming either returns nothing new.
    #
    # The count stays, as the tie-break it always should have been, and the
    # module name settles the rest so two runs over one tree agree.
    #
    # Copies sort below every restart regardless of what is in them. They are
    # still returned -- `--json` carries them, the map still marks their cards
    # -- because a reader who wants to know their tree is full of backup
    # folders is better served by the finding than by its absence. What they
    # must never do is sit in the ranked restart list as though somebody had
    # started the job over.
    out.sort(key=lambda g: (g["verdict"] == "copy", -g["at_stake_lines"],
                            -len(g["attempts"]), g["resume_at"]))
    return out


def restarts(groups):
    """The groups that really are the same job started over."""
    return [g for g in groups if g.get("verdict") != "copy"]


def copies(groups):
    """The groups that are one file in several places."""
    return [g for g in groups if g.get("verdict") == "copy"]


def stake_sentence(group):
    """The one line that says what resuming this group hands back.

    Every renderer prints this rather than a score: a bare number nobody can
    take apart is a number nobody will act on, and the three inputs are the
    parts a reader can check against the files themselves.
    """
    return (f"~{group['at_stake_lines']:,} lines at stake -- "
            f"{group['resume_lines']:,} in the furthest attempt, "
            f"{group['resume_percent']}% complete, "
            f"{group['elsewhere_percent']}% of its definitions exist "
            f"elsewhere")


def copy_sentence(group):
    """The verdict on a group that is one file in several places.

    A reader told "somebody restarted this four times" about a backup folder
    has been told something false, so this is a different sentence and not a
    lower score, and it names the one file they can open to check it.
    """
    if group.get("verdict") != "copy":
        return ""
    return (f"this is a copy, not a restart: {group['copy_reason']} "
            f"{group['copy_of_relpath']}")


# What the effort-to-finish estimate can and cannot see. Printed once above the
# groups rather than on every row, so the rows stay one sentence each.
EFFORT_SCOPE = ("lines left to finish counts only definitions with no body "
                "yet -- it says nothing about whether what is written works")


def effort_sentence(group):
    """How much of the furthest attempt is still to write, or "".

    Printed beside the stake, because it is deliberately not part of the
    ranking: a small group that is nearly done is a quick win, and it sorts
    low precisely because there is little of it.

    Empty when there is nothing unwritten, and every renderer drops the line.
    It used to say "nothing left unwritten by this measure" there, which on a
    finished tree is 12 rows of 22 carrying a sentence that discriminates
    nothing; and before 2026-09-22 the number itself was wrong, because it came
    off a completeness score that also folds in reachability and tests.
    """
    if not group["remaining_lines"]:
        return ""
    count = group.get("unwritten_definitions", 0)
    return (f"~{group['remaining_lines']:,} lines left to finish it -- "
            f"{count} definition{'s' if count != 1 else ''} with no body yet")
