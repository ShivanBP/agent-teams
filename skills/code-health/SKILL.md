---
name: code-health
description: Check this repo against its own stated invariants. Use for a code health check, a repo hygiene pass, or before a release, and after a batch lands to confirm it did not erode a rule. Reports findings, does not fix them.
---

# Code health

This checks the invariants this repo states about itself, not general code quality. A generic
review already exists for whoever has it; what no generic tool knows is the rules written in
`AGENTS.md`. Those are the subject here.

Report findings. Do not fix them in the same run: a health check that edits is a batch, and
it stops being safe to run whenever anyone wants a reading.

## Read first

`AGENTS.md` at the repo root, every time, plus `docs/OPERATING.md` where `AGENTS.md`
delegates to it. Derive the invariant list from those two, and open the report with how many
you derived. That count is the only thing telling a later reader whether the run measured the
repo's rules or a stale copy of them.

## Method

How to check, never what to check. The what lives in `AGENTS.md`; a list of it here goes stale
the week after it is written, and it did.

- **Scope.** The em-dash and vocabulary rules govern code and active prose. `memory/` and
  `plans/` hold historical records, including Discord-era ones, and rewriting those is noise
  rather than cleanup.
- **Comments.** The one-line-invariant rule targets explanatory inline blocks. A module
  docstring saying what the module is, is not a finding.
- **Posted strings.** Grep the modules that post, not the ones that look like they might.
- **File length.** A judgment about one reader, so give the line count and the reason instead
  of a verdict.
- **The sha.** Report the commit measured. Two commits landed inside ten minutes of one report
  (2026-08-16) and nobody could tell how stale it was without rebuilding the timeline.

## Report

Group findings by invariant, worst first, each with a path and one line saying what breaks.
Separate the two kinds explicitly: a violation of a stated rule, and a place where the rule
itself no longer fits what the code does. The second kind is a proposal for Mate, not a
defect, and mixing them makes the report unusable.

Close with the one finding you would fix first if you could fix only one.

## Bounds

Read only. No edits, no commits, no restarts.

Never report a finding you have not opened the file to confirm. A grep hit is a candidate,
not a finding.

If the repo is mid-batch, with a working tree that is not clean, say so at the top of the
report. A health check against a moving tree measures the batch, not the repo.
