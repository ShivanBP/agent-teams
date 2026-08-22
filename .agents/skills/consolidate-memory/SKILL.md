---
name: consolidate-memory
description: Consolidate one persona's memory directory, and flag what has stopped being memory and belongs in repo instructions, a skill or an agent file. Use when asked to weed, consolidate, tidy or audit a persona's memory, when a MEMORY.md index has drifted from the files it lists, or after a long run of threads has left duplicate positions behind.
---

# Consolidate a persona's memory

One persona per run, though one wake may run several in sequence. Say which persona a run
covers at the start, commit it before the next one starts, and never edit two personas'
directories inside one run.

Memory lives at `memory/<persona>/`: a hot index `MEMORY.md` whose rows link to cold
`position_*.md` and `feedback_*.md` files. The index is read every wake; the cold files are
read only when a row makes them look relevant. That asymmetry is the whole job. A row that
misdescribes its file is worse than a missing row, because nobody opens the file to find out.

## What memory is for

Durable judgment: a position argued and why, what the operator accepted or rejected and why,
a correction that should change future behaviour, a thread left open. Not facts about the
world, not current state of the code, not anything fetchable. Those go stale and get
believed anyway, which is the failure this skill exists to prevent.

## Steps

1. Read `memory/<persona>/MEMORY.md`, then list the directory. Build three sets: rows with no
   file, files with no row, and cross-file links with no target. Report all three counts
   before changing anything.
2. Read every cold file. Yes, all of them. The judgments below need the contents, not the
   row summaries.
3. Fix the index. Every file gets exactly one row. Every row names the file's actual
   subject, the ruling if there is one, and the date. Kill rows pointing at nothing.
4. Collapse what is finished. A position marked CLOSED or superseded by a later one shrinks
   to a single row on the index and its file is deleted, with the superseding file naming
   what it replaced. Treat a file's own salvage claim as a claim, not evidence: verify the
   named successor contains every durable item before deleting. A position the operator ruled on
   keeps the ruling and loses the argument that led there.
5. Flag, do not fix:
   - stored facts that should have been fetched (versions, paths, prices, API shapes)
   - two files that contradict each other
   - a position whose ruling you cannot determine from the file
   - a sentence that has stopped being memory and belongs somewhere else, see Promotion
   Put these in the report for the owner. Resolving a contradiction is the owner's judgment,
   not yours.
6. Leave alone: anything recorded as a correction from the operator, anything marked
   do-not-reopen, and open threads that are still open. Age is not a reason to delete a
   correction.
7. Commit with `git -C <repo> commit -- memory/<persona>` and a message naming the persona
   and the counts. One commit per persona, never a sweep across several.
8. Report in the topic: rows fixed, files collapsed, files deleted, and the flag list. Name
   what you deleted, so the owner can object without reading the diff.

## Promotion

Memory accumulates things that are not memory: a durable rule, a repeatable procedure, a
standing instinct. This run already has every cold file open, so the marginal cost of asking
"does this still belong here" is one question per file.

Three gates, all of which must pass before a sentence is a candidate:

1. It survives the deletion of its thread. Delete the topic and the artifact that produced
   it: does the sentence still tell someone what to do?
2. It was acted on twice, in different threads, and not revised. Mentioned twice is not
   enough.
3. It names no specific artifact. A sentence that needs a path, a commit, a date or a
   one-time decision to make sense is a record, and records stay.

One ruling from the operator is a precedent, not a rule. It stays in memory until it recurs.

Route by who the sentence binds, not by what it is about:

| binds | destination | may this run write |
|---|---|---|
| every persona, every harness | fleet `AGENTS.md` | propose only |
| one persona's standing behaviour | that persona's agent file | propose only |
| a trigger plus ordered steps, fleet-wide | fleet `skills/` | yes, directly |
| one project, must always apply there | that project's `AGENTS.md` | propose only |
| one project, looked up when relevant | that project's `skills/` | propose only |
| nobody, it only explains why a decision went that way | stays in `memory/` | n/a |

The last row is the one that gets forgotten, and it is where most of memory correctly stays.

A proposal is a post, not an edit: the target path, the finished line as text, and which
gates it passed. Nothing in memory changes at proposal time, and this run writes nothing
outside `memory/<persona>/` except a new fleet skill. That bound holds for every project
directory too: promotion into a project proposes, it does not write there.

Once a promoted rule has landed in its target, the next run finds a memory file restating
something already stated there and collapses it under step 4 to a one-line provenance row:
what was promoted, where it lives now, and the date. That is what makes this idempotent, and
it is the rule against two sources of truth. Memory keeps why, when and who ruled;
instructions keep the rule itself.

## Bounds

One consolidation per persona at a time; union resurrects deletions raced by another lane.

Run in a topic with no build worktree, so the commits land on main. A kick in a topic where a
builder already worked joins that `build/<slug>` tree (`WORKTREE_JOIN` in `constants.py`), and
memory history written there sits on a branch nobody lands.

Git is the undo: `memory/` is tracked in the fleet repo, so edit directly and do not ask
permission first. That only holds while
every change is committed, so a run that cannot commit stops and reports instead of leaving
edits loose in the working tree.

Never write a persona's memory to make it agree with yours. If a persona's position looks
wrong to you, that is a finding for the topic, not an edit.

Keep `MEMORY.md` below the runner's injected-memory clip: `MEMORY_MAX_LINES` and
`MEMORY_MAX_BYTES` in `scripts/constants.py`, currently 200 lines and 25 KB. A consolidation
also ends with fewer cold-file words than it started, or reports what resisted.
