"""Shared digest sweep helpers and the isolated sweep runner."""

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time

import api
import constants
import store

log = logging.getLogger("agent-team.todo")


def message_record(channel, stream_id, message, site):
    topic = message.get("subject") or ""
    return {
        "id": int(message["id"]),
        "channel": channel,
        "topic": topic,
        "sender": message.get("sender_full_name") or "",
        "content": message.get("content") or "",
        "timestamp": message.get("timestamp"),
        "permalink": api.permalink(site, stream_id, channel, topic, message["id"]),
    }


def cap_messages(messages, max_chars):
    rows = list(sorted(messages, key=lambda row: row["id"]))
    encoded = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows]
    total = sum(len(row) for row in encoded) + max(0, len(encoded) - 1) + 2
    dropped = 0
    while encoded and total > max_chars:
        total -= len(encoded.pop(0)) + (1 if encoded else 0)
        rows.pop(0)
        dropped += 1
    return rows, dropped


def _model_env():
    repo = str(constants.REPO_DIR)
    blocked = {"PWD", "OLDPWD", "PYTHONPATH", "VIRTUAL_ENV"}
    return {
        key: value for key, value in os.environ.items()
        if key not in blocked and "ZULIP" not in key.upper()
        and not key.startswith("AGENT_TEAM_") and repo not in value
        and "zuliprc" not in value.lower()
    }


def model_command(prompt, rail=None):
    """The sweep's own command, not runner's: no tools, no session, and a temp cwd. On agy the
    empty cwd is the whole fence, there being no --tools there."""
    rail = rail or constants.digest_rail()
    effort = constants.translate_effort(rail["provider"], rail["effort"])
    if rail["provider"] == "agy":
        return [
            constants.AGY_BIN, "--dangerously-skip-permissions", "--disable-slash-commands",
            "--model", rail["model"], "--effort", effort, "--output-format", "json",
            "--print-timeout", "%ds" % constants.RUN_TIMEOUT, "-p", prompt,
        ]
    if rail["provider"] != "claude":
        raise RuntimeError("digest seat provider %r is not supported" % rail["provider"])
    return [
        "claude", "-p", "--model", rail["model"], "--effort", effort,
        "--output-format", "json",
        "--tools", "", "--safe-mode", "--disable-slash-commands",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--no-session-persistence", prompt,
    ]


def _agy_payload(stdout):
    """agy's --output-format json envelope, read from the last JSON line so a stream-json
    tail parses too."""
    for line in reversed((stdout or "").splitlines()):
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if not isinstance(candidate, dict):
            continue
        if candidate.get("event") == "result" and isinstance(candidate.get("result"), dict):
            return candidate["result"]
        if "status" in candidate:
            return candidate
    raise ValueError("todo sweep model returned no JSON envelope")


def parse_envelope(provider, stdout):
    """(result text, cost fields) per provider. agy bills nothing, so its row records no usd."""
    if provider == "agy":
        payload = _agy_payload(stdout)
        if payload.get("status") != "SUCCESS":
            raise RuntimeError("todo sweep model status %r" % payload.get("status"))
        usage = payload.get("usage") or {}
        result = payload.get("response")
        row = {
            "usd": 0.0,
            "turns": 1,
            "cache_read": usage.get("cache_read_tokens", 0),
            "cache_creation": 0,
            "input_tokens": usage.get("input_tokens", 0),
        }
    else:
        envelope = json.loads(stdout)
        usage = envelope.get("usage") or {}
        result = envelope.get("result")
        row = {
            "usd": float(envelope.get("total_cost_usd") or 0.0),
            "turns": int(envelope.get("num_turns") or 0),
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "cache_creation": usage.get("cache_creation_input_tokens", 0),
            "input_tokens": usage.get("input_tokens", 0),
        }
    for key in ("output_tokens", "thinking_tokens", "total_tokens"):
        if key in usage:
            row[key] = usage[key]
    if not isinstance(result, str):
        raise ValueError("todo sweep model returned no JSON result string")
    return result, row


def _record_cost(rail, row, lane, cost_fn):
    cost_fn(dict(row, persona=constants.BRIDGE_IDENTITY, lane=lane,
                 provider=rail["provider"], model=rail["model"], effort=rail["effort"]))


def parse_model_json(result):
    text = result.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[len("```json\n"):-len("\n```")]
    return json.loads(text)


def run_model(prompt, run=subprocess.run, cwd=None, lane=None, cost_fn=store.cost_append,
              rail=None):
    rail = rail or constants.digest_rail()

    def invoke(path):
        proc = run(
            model_command(prompt, rail), cwd=path, env=_model_env(), capture_output=True,
            text=True, timeout=constants.RUN_TIMEOUT)
        if proc.returncode != 0:
            raise RuntimeError("todo sweep model failed: %s" % proc.stderr.strip()[-500:])
        result, row = parse_envelope(rail["provider"], proc.stdout)
        if lane is not None:
            _record_cost(rail, row, lane, cost_fn)
        return parse_model_json(result)

    if cwd is not None:
        return invoke(cwd)
    with tempfile.TemporaryDirectory(prefix="agent-team-todo-") as path:
        return invoke(path)


def sweep_thread(interval=constants.DIGEST_SWEEP_MIN * 60):
    while True:
        try:
            import digest
            digest.sweep_once()
        except (Exception, SystemExit):
            log.exception("digest sweep pass failed")
        time.sleep(interval)


def _selftest():
    from tests import todo_selftest
    return todo_selftest.run(sys.modules[__name__])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    ap.error("nothing to do; todo.py is a library")


if __name__ == "__main__":
    sys.exit(main())
