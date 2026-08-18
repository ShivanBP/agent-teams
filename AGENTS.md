# Agent-team (Zulip persona fleet)

The Zulip fleet: seven personas plus the bridge identity, woken by @-mentions through
scripts/listener.py. Plan of record: plans/2026-08-12-zulip-migration-final.md. The Discord
predecessor a-team was retired and deleted 2026-08-16; its history lives at soto-mate/a-team.

Zulip working rules and the permissions ledger live in README.md.

The word is persona, in code and prose. No em-dashes anywhere. Minimal, clean, terse:
comments are one-line invariants only, and a file Mate cannot read end to end is too long.

Secrets live in ~/.config/agent-team/ and never in this repo. Token and .env work is
Mate's, always. Never print or log a zuliprc's contents. A wake does not grant itself
capabilities.

Personal content lives only in `memory/`, `plans/`, `agents/`, and gitignored config.
Machinery stays generic. No user-specific absolute home paths outside home-derived defaults.

Persona memory lives only under `memory/<persona>/` by absolute path; write durable judgment
only and fetch current state fresh.

Wakes: a mention wakes a persona; the record it sees is a delta since its lane's last wake;
a topic is an open session, resolving it ends the session, reopening starts fresh; status
topics are the exception, they are never resolved. The flags -opus -fable -sonnet -low -mid -high
-xtra -claude -codex -agy -opencode parse only off configured flag holders.

Loops: header before kick one; `loops.py kick` fires kick one so the ledger row lands
before the post; every kick ends "kick n/N" and mentions its persona; the budget floor is
atomic; the operator continuation (opus) answers with one KICK or CLOSE line and anything
else is discarded unread.

Every string the machinery posts or injects lives in scripts/prompts.py; tunables in
scripts/constants.py. Persona, harness, and model-effort defaults live in the three JSON files
under config/; copy each tracked `.example.json` to its gitignored live name on first setup.
Every effort in those files uses low/mid/high/xtra. A posted string literal anywhere else is
a bug. Every module carries --selftest, offline; bodies live in `scripts/tests/`; a test body
imports what it uses; a case table changes in the same commit as its organ.

Fleet skills live in `skills/`, one directory each with a `SKILL.md`; personas live in
`agents/`. Harnesses discover them through symlinks, never edited: `.claude/agents` and
`.claude/skills` for Claude Code, `.agents/skills` for Codex, OpenCode and agy, and
`.agents/agents` for agy alone. Nothing under `.codex/`: Codex reads `.agents/skills`, and
its subagent format is TOML, not our Markdown.

Subagents: name the model on every one you spawn, because unnamed means inherited and a
silent spawn from an opus or fable wake spends that model on file reading. Sonnet is the
default: reading, searching, summarizing, verifying, probing and source collection are sonnet
work. Escalate to opus only when the subagent's output ships as-is rather than reworked by
you, with the reason in the briefing as one line. Never spawn a subagent on fable.

scripts/restart.sh is the only restart path: a bare kickstart can kill a wake mid-run.

Shared-checkout git mutations run only through `python3 scripts/commit.py -m "message"
<path>...`: name every path, never use `git stash`, and never substitute raw git if its bounded
lock wait fails. Report named files left written but uncommitted. Read-only git and worktree git
stay outside this ritual.

A build wake works in its own worktree on branch `build/<topic-slug>` and commits there. The
handoff fetches origin and rebases onto `origin/main` before the wake reads the tree. A failed
handoff rebase is aborted and the stale worktree is handed over with its behind-count, never a
fallback to the shared checkout. Before it lands, every module it touched has a green `--selftest`
in that same wake. It lands itself: `git fetch origin`, `git rebase origin/main`, re-run those
selftests, `git push origin HEAD:main`, then run `python3 scripts/commit.py --pull` and report
the sha. A rebase conflict or a
red selftest stops the wake: it reports the branch, unlanded, and names what stopped it. Jan and Eve
read the landed sha and join that worktree when it already exists; a finding becomes a follow-up
commit, never a held branch. Findings stay in the topic.

## Taste

The taste here is zen in the Sōtō sense: plain, spare, nothing extra. Simplicity
outranks precision, and a deletion is a contribution. This is an aesthetic, not a
vocabulary: no koans, no Buddhist terms in work product.

Substantive batches run the verification loop: a builder lands, Jan reads the diffs against
the brief and Eve gates from outside in parallel, findings earn one scoped round, then a
re-check of only the fixes. Plans end with their load-bearing unknowns named. A plan that sets
a rule names the rule's delivery mechanism (WAKE_HEADER, AGENTS.md, a persona file, or README
plus who fetches it); text in a file no wake loads is dead text.

A feature runs in one topic in #setup, design through build to verification: terms of
record, the builder's kick, and the verification reports all land where the design lives.
Domain channels carry plans and discussion, never batch execution. #workbench is archived,
history intact.

This file stays lean on purpose (Mate, 2026-08-12): rules about Zulip get written down
after Zulip teaches them, not before; seed rules that start a habit are the exception
(Mate, 2026-08-12).
