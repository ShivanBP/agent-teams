# Adopting agent-teams

Give this file to a desktop agent. The agent prepares the machinery. A human handles every
credential.

## Verify the clone

Clone the public repository. Create the runtime environment and install the one external
Python package:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install zulip
```

Before adding personas or config, run every offline selftest:

```sh
for module in scripts/*.py; do .venv/bin/python "$module" --selftest || exit 1; done
```

Stop on any failure. An untouched clone is expected to pass with no environment variables.

## Create the fleet

Copy the starters, keep `operator.md` and `operator-reply.md`, and choose two or three persona
files to begin:

```sh
cp agents.examples/*.md agents/
```

Rename and rewrite the chosen personas. Keep each filename, its frontmatter `name`, the
`PERSONAS` tuple in `scripts/personas.py`, and the persona matrix keys identical. Remove
unused starter rows.

Create the three live, gitignored config files and edit them:

```sh
cp config/persona-matrix.example.json config/persona-matrix.json
cp config/harness-defaults.example.json config/harness-defaults.json
cp config/model-effort-defaults.example.json config/model-effort-defaults.json
```

Each persona's matrix row selects its harness: `claude` runs Claude Code, `codex` runs Codex,
`agy` runs agy, and `opencode` runs OpenCode. Install and log in only to the CLIs named by
the live matrix.

Optional browser tools start from `.mcp.example.json`; copy it to the gitignored `.mcp.json`
and edit it for the local install.

## Create the private overlay

`memory/`, `plans/`, and `agents/` are ignored by the public repository. `commit.py` refuses
personal paths until a private overlay exists. The human creates or chooses a private Git
remote and supplies its URL. The agent may then run:

```sh
mkdir -p .private memory plans agents
git init --bare .private/.git
git --git-dir=.private/.git --work-tree=. config core.bare false
git --git-dir=.private/.git --work-tree=. config status.showUntrackedFiles no
git --git-dir=.private/.git --work-tree=. config user.name "Agent Team"
git --git-dir=.private/.git --work-tree=. config user.email "agent-team@localhost"
git --git-dir=.private/.git --work-tree=. remote add origin "$PRIVATE_REPO_URL"
git --git-dir=.private/.git --work-tree=. add -f -- memory plans agents
git --git-dir=.private/.git --work-tree=. commit -m "Seed private overlay"
git --git-dir=.private/.git --work-tree=. branch -M private-overlay
git --git-dir=.private/.git --work-tree=. push -u origin private-overlay
```

The remote may retain an archival `main`; the overlay uses `private-overlay`. Verify the
split before continuing:

```sh
git status --short
git --git-dir=.private/.git --work-tree=. status --short
```

## Prepare the runtime

Create `~/.config/agent-team/` with `logs/` and `state/`. Put `bridge.zuliprc` there, plus
one `<persona>.zuliprc` for each persona. Put `AGENT_TEAM_MATE_EMAIL` in
`~/.config/agent-team/.env`. Set path overrides there only when the defaults do not match
the machine: `AGENT_TEAM_CONFIG_DIR`, `AGENT_TEAM_STATE_DIR`, `AGENT_TEAM_LOGS_DIR`,
`AGENT_TEAM_MEMORY_DIR`, `CODEX_BIN`, `AGY_BIN`, or `OPENCODE_BIN`.
An install retaining a nondefault launchd label also sets `AGENT_TEAM_LAUNCHD_LABEL` there.

Render the launchd template from the repository root:

```sh
repo_dir=$(pwd -P)
sed -e "s|__HOME__|$HOME|g" -e "s|__REPO_DIR__|$repo_dir|g" \
  launchd/com.agent-team.plist.example > launchd/com.agent-team.plist
```

The human now creates the Zulip organization and one bot per persona, downloads each
zuliprc, installs every selected harness CLI, and logs in to those CLIs. An agent must not
read, create, copy, or edit credentials.

Run all selftests again. Start only through `scripts/restart.sh`. In Zulip, mention one
persona in a topic. Adoption is complete when that persona wakes, works, and replies there.

## Cut over an existing fleet

Cutover is a desktop operation after the export gate passes. Back up the old root, clone the
public repository into a new folder, and copy only these private surfaces from the old root:
`memory/`, `plans/`, `agents/`, `.mcp.json`, and the three live `config/*.json` files. Run the
private-overlay bootstrap above with the archived private repository as its remote. Its
existing `main` remains the old estate history; new personal commits use `private-overlay`.

Render and load the launchd template from the new root. If the installed job retains its old
label, render that label into the plist, put the same value in
`AGENT_TEAM_LAUNCHD_LABEL`, and pass it to the first restart. Start only with
`scripts/restart.sh`. After one smoke wake, verify that public `git status` is clean and that
the overlay log contains the copied personal files:

```sh
git status --short
git --git-dir=.private/.git --work-tree=. log -1 --stat
```
