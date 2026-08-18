"""Shared digest sweep helpers and isolated Sonnet runner."""

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


def model_command(prompt):
    return [
        "claude", "-p", "--model", constants.DIGEST_MODEL, "--output-format", "json",
        "--tools", "", "--safe-mode", "--disable-slash-commands",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--no-session-persistence", prompt,
    ]


def _record_cost(envelope, lane, cost_fn):
    usage = envelope.get("usage") or {}
    row = {
        "persona": constants.OPERATOR_IDENTITY,
        "lane": lane,
        "usd": float(envelope.get("total_cost_usd") or 0.0),
        "turns": int(envelope.get("num_turns") or 0),
        "cache_read": usage.get("cache_read_input_tokens", 0),
        "cache_creation": usage.get("cache_creation_input_tokens", 0),
        "input_tokens": usage.get("input_tokens", 0),
        "provider": "claude",
        "model": constants.DIGEST_MODEL,
        "effort": None,
    }
    for key in ("output_tokens", "thinking_tokens", "total_tokens"):
        if key in usage:
            row[key] = usage[key]
    cost_fn(row)


def parse_model_json(result):
    text = result.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[len("```json\n"):-len("\n```")]
    return json.loads(text)


def run_model(prompt, run=subprocess.run, cwd=None, lane=None, cost_fn=store.cost_append):
    def invoke(path):
        proc = run(
            model_command(prompt), cwd=path, env=_model_env(), capture_output=True,
            text=True, timeout=constants.RUN_TIMEOUT)
        if proc.returncode != 0:
            raise RuntimeError("todo sweep model failed: %s" % proc.stderr.strip()[-500:])
        envelope = json.loads(proc.stdout)
        result = envelope.get("result")
        if not isinstance(result, str):
            raise ValueError("todo sweep model returned no JSON result string")
        if lane is not None:
            _record_cost(envelope, lane, cost_fn)
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
