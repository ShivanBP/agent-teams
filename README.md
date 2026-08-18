# Agent-teams

[![selftests](https://github.com/soto-mate/agent-teams/actions/workflows/python-app.yml/badge.svg)](https://github.com/soto-mate/agent-teams/actions/workflows/python-app.yml)

Chat-woken agent personas. Mention one in Zulip, it wakes, works, replies, and sleeps.

![A mention with flags waking a persona, and the reply with its session footer](docs/img/wake-cycle.png)

`scripts/listener.py` long-polls Zulip. A mention wakes one persona on one of four harnesses
(Claude Code, Codex, agy, OpenCode), chosen by config or by a flag in the mention itself. The
wake sees only what it needs: the messages in that topic since its own last wake, its persona
file, and its memory. A topic is a session, so resolving one ends it and reopening one starts
fresh. Every string the machinery posts or injects lives in `scripts/prompts.py`. State and
rules live in git; Zulip renders views of them.

![The status board: open lanes and todos, edited in place by a scheduled sweep](docs/img/status-board.png)

## Quickstart

```sh
git clone https://github.com/soto-mate/agent-teams.git && cd agent-teams
python3 -m venv .venv && .venv/bin/python -m pip install zulip
for f in scripts/*.py; do .venv/bin/python "$f" --selftest || break; done
cp agents.examples/*.md agents/
for f in config/*.example.json; do cp "$f" "${f%.example.json}.json"; done
# then write one zuliprc per persona under ~/.config/agent-team/
scripts/restart.sh
```

A fresh clone passes every selftest offline, with no credentials. Bot credentials are
zuliprc files under `~/.config/agent-team/` and never live in this repository.
Adopting this for your own fleet: [ADOPTING.md](ADOPTING.md). Working rules for a running
fleet: [docs/OPERATING.md](docs/OPERATING.md).

Published as-is. No support is promised. MIT licensed, see [LICENSE](LICENSE).
