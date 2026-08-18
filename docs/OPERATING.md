# Operating notes

Working rules for the running fleet. Start at README.md for what this is.
These are one estate's rules; adopters start from [OPERATING.example.md](OPERATING.example.md)
and write their own as their fleet teaches them.

## Status rules

Seed set, revised when Zulip teaches otherwise:

- #status carries topics "item-status" and "worker-status". First guesses, not schema.
  Split a topic when it visibly hosts two kinds of traffic.
- #status > "alerts" is the stall sweep's own topic: listener.py posts there when it pauses
  a loop behind a stalled inflight row, or when a stall has no loop to pause.
- #status > "board" is the live lane and todo overview, edited in place by the stall sweep.
- The wake that did the work posts its own status line through send.py. No wake exists
  just to update status.
- Latest post wins. Old status posts are never edited.
- Waiting-on-Mate items are bullets in the newest status post.
- Status topics are never resolved. Topic-as-session governs wake lanes only.

## Ticket flow

Tickets open in #maintenance or #setup and stay in the topic they opened in; design,
build and verification run in that one topic, no hand-off channel. #workbench is archived
with its history intact.

Before killing a non-Claude wake, read its per-lane tail under
`~/.config/agent-team/logs/wakes/`; a partial transcript survives until that lane's next wake.

## Links and attachments

Attachments post as links; only images, video, and audio preview inline. Text documents ship
as sha-pinned GitHub links from the repo they live in (commit and push first; `git remote -v`
answers whether the repo has one). [attach:] is the lane for images and binaries, and the
last-resort fallback for a file whose repo truly has no remote; a document written for the
fleet belongs in a repo with a remote. Clicking text attachments in the Mac desktop app fails
upstream (zulip-desktop #1113); if one must be opened, use the web app. Summaries and decisions
go in the body, always.

The share lane is live (2026-08-12): remote wired, first push landed. A document someone
will open later is committed and pushed
BEFORE it is linked; the link pins the commit, `.../blob/<sha>/<path>`, never `blob/main/...`
(HEAD links rot when files move; `git rev-parse HEAD` after the push gives the sha).
Concurrent lanes: `git pull --rebase` before push; on conflict, post the [attach:] fallback
instead of fighting the rebase mid-wake. Credentials are the ambient `gh auth` already in
this Mac's keychain, full account scope, not a repo-scoped token as the plan first named:
that credential predates this plan and is already reachable by any Bash-holding persona, so
the share lane narrows nothing. Accepted state unless Mate rules otherwise; the reopen
trigger is the first bad push.

What that credential does not carry is the settings. Main is protected as of 2026-08-18,
required check `selftests` and admins enforced, and takes no direct push from anyone, Mate
included; public changes land through a PR that merges itself on green. Repository settings,
branch protection among them, are capability grants: they change from Mate's seat only, never
from a wake, the admin-scoped `gh` notwithstanding.

## Permissions ledger

This fleet runs headless on one operator's Mac, under the single user account the personas
already share; there is nobody present to click approve, so a harness sandbox would only stop
the wake, not an attacker who already had that account. The trade is deliberate: harness
sandboxing is given up, and the persona text plus the current brief carry the scope guard.
It is written down here so the cost stays visible.

Personas can post, react, attach, and read, as themselves only (api.enforce_identity).
Persona posts cannot wake other personas; bridge-issued kicks are the exception.
Channel and topic management is Mate's job in the Zulip UI; the bridge's standing admin
grant below is the one exception.

Flag holders can select a provider (`-claude`, `-codex`, `-agy`, `-opencode`), a model
(`-opus`, `-fable`), and an effort level (`-low`, `-mid`, `-high`, `-xtra`) for any wake.
The current flag holders are Mate and John Fechter. The last explicit flag in each category
wins, and providers stay sticky with the topic's session. Switching provider starts a fresh
session. Default persona-provider-model-effort assignments live in
`config/persona-matrix.json`. Flags from anyone else are stripped and ignored. The tracked
`.example.json` is only a template; once the live file exists,
edits to the example are ignored. Config is read once at listener start; after editing the
live file, run `scripts/restart.sh`. Effort levels translate per provider; unsupported
combinations such as `agy + xtra` are rejected.
Arm a deferred restart only as
`nohup scripts/restart.sh 7200 >> ~/.config/agent-team/logs/deferred-restart.log 2>&1 &`,
never with `launchctl submit`, which relaunches it after every exit.

A Codex wake runs `sandbox_mode="danger-full-access"` with `approval_policy="never"`, granted
by Mate 2026-08-13 to match Claude Bob's reach. `workspace-write` hard-denies writes under
`.git`, so Bob could not stage or commit; the narrower fix (naming `.git` a writable root)
was rejected in favour of parity, and fencing is deferred, not decided. Codex is therefore
unsandboxed on this machine. Agy uses its unattended permission bypass. A Claude wake passes
`--dangerously-skip-permissions` for the same reason (2026-08-16): headless has nobody to
approve, and in a build worktree, an untrusted cwd, the default mode fenced Bash off entirely
so a builder could not run a selftest or commit. For all three, the persona text and current
brief are the initial scope guard.

The bridge identity holds a standing admin grant from Mate (2026-08-12): channel, topic,
folder, and message administration. Status topics are still never resolved, by anyone.
Persona permissions are unchanged by this grant. A new verb lands in send.py and this
ledger in the same commit.

Rail B, the bridge Zulip seat, may register a loop and fire kick one (2026-08-18). The
authority is gated behind Mate's direct tag: listener verifies the sender is Mate by user id,
the message is fresh, and the topic is unresolved before the seat wakes at all.

send.py --resolve and --move-to CHANNEL are bridge-only verbs on top of that grant (topic
naming convention batch, 2026-08-12); every other identity is refused before any API call.
Read-only API GETs under your own zuliprc are open to everyone; writes go through send.py
verbs only.

## Channel descriptions (canonical copy)

Zulip keeps no edit history for a channel description, so the repo holds the canonical text
and Mate pastes it in the UI. Machinery channels only; domain and personal channels are
freehanded. The link is `https://github.com/soto-mate/agent-teams/blob/main/docs/OPERATING.md`.

- **setup**: Features and design. One topic carries a feature from terms of record through build to verification. [Rules](https://github.com/soto-mate/agent-teams/blob/main/docs/OPERATING.md)
- **maintenance**: Tickets and repairs to the running fleet. A ticket stays in the topic it opened in. [Rules](https://github.com/soto-mate/agent-teams/blob/main/docs/OPERATING.md)
- **status**: Status board and alerts. Topics here are never resolved. [Rules](https://github.com/soto-mate/agent-teams/blob/main/docs/OPERATING.md)
- **scheduled-jobs**: Cron-woken runs and what they reported. [Rules](https://github.com/soto-mate/agent-teams/blob/main/docs/OPERATING.md)
- **test**: Throwaway wakes and probes. Nothing here is a record. [Rules](https://github.com/soto-mate/agent-teams/blob/main/docs/OPERATING.md)
- **announcements**: Fleet-wide notices from Mate. Not a work lane. [Rules](https://github.com/soto-mate/agent-teams/blob/main/docs/OPERATING.md)
