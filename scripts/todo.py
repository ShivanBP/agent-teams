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
        "permalink": api.permalink(site, stream_id, channel, topic, message["id"]),
    }


def cap_messages(messages, max_chars=constants.TODO_SWEEP_MAX_CHARS):
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
        "claude", "-p", "--model", constants.TODO_SWEEP_MODEL, "--output-format", "json",
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
        "model": constants.TODO_SWEEP_MODEL,
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


def sweep_thread(interval=constants.TODO_SWEEP_MIN * 60):
    while True:
        try:
            import digest
            digest.sweep_once()
        except (Exception, SystemExit):
            log.exception("digest sweep pass failed")
        time.sleep(interval)


def _selftest():
    import tests.cases as cases

    passed = failed = 0
    model_json_ok = True
    for raw, expected in cases.TODO_MODEL_JSON:
        try:
            got = parse_model_json(raw)
        except ValueError:
            got = None
        if got != expected:
            model_json_ok = False
            print("FAIL parse_model_json %r -> %r wanted %r" % (raw, got, expected))
    if model_json_ok:
        passed += 1
    else:
        failed += 1
    kept, dropped = cap_messages(cases.TODO_CAP_INPUT, cases.TODO_CAP_CHARS)
    if kept == cases.TODO_CAP_EXPECTED and dropped == cases.TODO_CAP_DROPPED:
        passed += 1
    else:
        failed += 1
        print("FAIL cap_messages -> %r dropped=%r" % (kept, dropped))

    command = model_command("prompt")
    env = _model_env()
    tools_off = command[command.index("--tools") + 1] == ""
    mcp_config = json.loads(command[command.index("--mcp-config") + 1])
    if all(part in command for part in cases.TODO_COMMAND_CONTAINS) and tools_off \
            and mcp_config == {"mcpServers": {}} and str(constants.REPO_DIR) not in command \
            and not any("ZULIP" in key for key in env):
        passed += 1
    else:
        failed += 1
        print("FAIL model command or environment widened the sweep: %r" % command)

    invoked = {}

    class _Proc:
        returncode = 0
        stdout = json.dumps({
            "result": "[]", "total_cost_usd": 0.012, "num_turns": 1,
            "usage": {"input_tokens": 12, "output_tokens": 3},
        })
        stderr = ""

    def run_stub(command, **kwargs):
        invoked.update(kwargs)
        invoked["empty"] = os.listdir(kwargs["cwd"]) == []
        return _Proc()

    costs = []
    model_rows = run_model("prompt", run=run_stub, lane="digest", cost_fn=costs.append)
    model_cwd = invoked.get("cwd", "")
    if model_rows == [] and invoked.get("empty") and str(constants.REPO_DIR) not in model_cwd \
            and not any("ZULIP" in key for key in invoked.get("env", {})) \
            and len(costs) == 1 and costs[0]["lane"] == "digest" \
            and costs[0]["usd"] == 0.012 and costs[0]["output_tokens"] == 3:
        passed += 1
    else:
        failed += 1
        print("FAIL model call cwd or environment was not empty and isolated: %r" % invoked)

    print("todo.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    ap.error("nothing to do; todo.py is a library")


if __name__ == "__main__":
    sys.exit(main())
