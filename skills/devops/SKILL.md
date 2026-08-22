---
name: devops
description: How a change lands in this repo. Read before the first commit of any build, verification or follow-up wake.
---

# Devops

A build wake works in its own worktree on branch `build/<topic-slug>` and commits there. The
handoff fetches origin and rebases onto `origin/main` before the wake reads the tree. A failed
handoff rebase is aborted and the stale worktree is handed over with its behind-count, never a
fallback to the shared checkout. Before it lands, every module it touched has a green `--selftest`
in that same wake. It lands itself: `git fetch origin`, `git rebase origin/main`, re-run those
selftests, push the branch, then `gh pr create`, `gh pr merge --auto --rebase`,
`gh pr checks --watch`, and `gh pr view --json mergeCommit` for the merged sha; then run
`python3 scripts/commit.py --pull` and report that sha. The wake waits for the merge: it never
closes on an open PR. A rebase conflict, a red check or a red selftest stops the wake: it
reports the branch and the PR, unlanded, and names what stopped it. Jan and Eve read the landed
sha and join that worktree when it already exists; a finding becomes a follow-up PR from the
same worktree, never a held branch. Findings stay in the topic.
