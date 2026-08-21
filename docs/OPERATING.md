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
- A restart replays the inflight rows it finds: a wake killed mid-run runs again from its
  mention, once, logged as `replay:`.
- The wake that did the work posts its own status line through send.py. No wake exists
  just to update status.
- Latest post wins. Old status posts are never edited.
- Waiting-on-Mate items are bullets in the newest status post.
- Status topics are never resolved. Topic-as-session governs wake lanes only.

## Ticket flow

Tickets open in #maintenance or #setup and stay in the topic they opened in; design,
build and verification run in that one topic, no hand-off channel. #workbench is archived
with its history intact.

voice-connector takes one build topic at a time: one checkout, one live service, no worktrees.

Before killing a non-Claude wake, read its per-lane tail under
`~/.config/agent-team/logs/wakes/`; a partial transcript survives until that lane's next wake.

## Links and attachments

Attachments post as links; only images, video, and audio preview inline. Text documents tracked
in a public repo ship as sha-pinned GitHub links (commit and push first). [attach:] is the lane
for images, binaries, and any text not publicly pushed: plans, private-overlay files, repos with
no remote. Clicking text attachments in the Mac desktop app fails upstream (zulip-desktop #1113,
still open 2026-08-18); if one must be opened, use the web app. Summaries and decisions go in
the body, always.

The share lane is live (2026-08-12): remote wired, first push landed. A document going the
link lane is committed and pushed BEFORE it is linked; the link pins the commit,
`.../blob/<sha>/<path>`, never `blob/main/...` (HEAD links rot when files move;
`git rev-parse HEAD` after the push gives the sha).
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
The listener deletes a completed progress message through that persona's identity; the realm
limits deletion to the identity's own message.
Persona posts cannot wake other personas; bridge-issued kicks are the exception.
Channel and topic management is Mate's job in the Zulip UI; the bridge's standing admin
grant below is the one exception.

Flag holders can select a provider (`-claude`, `-codex`, `-agy`, `-opencode`), a model
(`-opus`, `-fable`), and an effort level (`-low`, `-mid`, `-high`, `-xtra`) for any wake.
The current flag holders are Mate and John Fechter, resolved from `AGENT_TEAM_MATE_EMAILS`.
A post from the browser seat goes out as the bridge, which is not among them, so flags written
there are stripped; adding bridge's bot email to that variable is what turns them on.
The last explicit flag in each category wins, and providers stay sticky with the topic's
session. Switching provider starts a fresh session. Default persona-provider-model-effort
assignments live in `config/persona-matrix.json`. Flags from anyone else are stripped and
ignored. The tracked `.example.json` is only a template; once the live file exists, edits to
the example are ignored. Config is read once at listener start; after editing the
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
Both refuse a status channel outright, bridge included (browser Zulip seat, 2026-08-20): the
never-resolved rule was prose in a help string until then, so any seat could still spend the
call.

send.py --channel-create, --channel-update, --channel-archive, --subscribe and --unsubscribe
are bridge-only on the same grant (browser Zulip seat, 2026-08-20). No user management, no role
changes, no bot creation: those are not in the grant and are not implemented. --channel-archive
and a --channel-update --rename refuse a status channel before any API call, for the same reason
--resolve does.

What the server actually allows, measured 2026-08-20 rather than assumed: channel
administration belongs to a channel's creator, not to organization administrators alone. Bridge
is a member-role bot and can create a channel and later update, rename or archive that one; a
channel it did not create refuses with "You do not have permission to administer this channel."
Subscribing and unsubscribing other users is not gated that way and works anywhere. So the plan's
expectation that archive needs an admin role was wrong on this server: it needs to have been the
creator. Nothing here grants bridge reach over channels the operator made.

--subscribe resolves the channel id before it acts. POST /users/me/subscriptions subscribes *or
creates*, so without that lookup a mistyped channel name would quietly make a channel.

The browser is bridge's fourth seat (2026-08-20): the claude.ai connector in
`~/Projects/voice-connector` calls the verbs above through one argv into this repo's own
read.py and send.py. One GitHub login gates every tool; the identity is pinned in the connector
and is never a tool argument; claude.ai prompts Mate in the browser before each write call.
Its dispatch tool runs Claude Code on this Mac at wake parity, granted by Mate 2026-08-20 in
#setup: a verified login carries the same trust as a verified mention, so a dispatched run has
a wake's reach, push and pull requests included, and the connector holds no tool fences of its
own. What it keeps are a wake's ceilings, which are about spend and not trust: per-job budget,
daily budget, job count, and a job timeout matching RUN_TIMEOUT.

Read-only API GETs under your own zuliprc are open to everyone; writes go through send.py
verbs only.

`monitor.py board CHANNEL --body-file PATH` is the one write that does not go out under the
running wake's own name (jobfinder recut, 2026-08-18). A Zulip message is editable only by its
author, proven that day: a persona seat PATCHing another bot's message gets "You don't have
permission to edit this message". A board authored by whichever persona happened to run the
sweep could therefore never be edited again, so the board goes out under the board identity.
The grant is narrow by construction and is the whole of it: the caller chooses neither the
identity nor the channel nor the topic, only the body, and the channel must already be mapped
in `config/domains.json`. The blast radius is one message in one never-resolved status topic.
In code the grant is `enforce=False`, passed from `send.board_message` and nowhere else.

## Domain map

`config/domains.json` maps a channel name to the repo its wakes work against. A wake in a mapped
channel gets one extra header line naming that root and saying its CLAUDE.md and `skills/` apply,
read by path: nothing loads them for the session. The file is gitignored, its tracked
`.example.json` ships empty, and every value is an absolute path.

Mapping is by exact channel name, so a domain with several channels lists each one, all pointing
at the same root. Jobfinder maps `jobfinder`, `jobfinder-status` and `jobfinder-setup` to
`~/Projects/jobfinder`.

A domain's board lives in its own status channel by convention, `#<channel>-status > board`,
never in the shared `#status`, which carries the fleet's own board. The destination is derived,
never an argument: a board that can be aimed ends up in two places. `monitor.py board CHANNEL`
refuses loudly when that status channel does not exist, and the domain's `board.json` records
which channel and topic it was posted to, so moving the convention reposts rather than editing
the message it left behind.

Channels also group in `config/channels.json`, one group per heading on the fleet board. A
domain's channels belong in their own group. Adding a group is a config edit only; the board's
per-section state keys are derived from the group name.

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
