"""Periodic Sonnet sweep that proposes message residue without creating todo state."""

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
import loops
import prompts
import read as read_mod
import send as send_mod
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


def harvest(cursor=None, as_name=constants.OPERATOR_IDENTITY):
    cfg = api.load(as_name)
    messages = []
    next_cursor = int(cursor or 0)
    for channel in api.visible_streams(as_name):
        if channel == constants.STATUS_STREAM:
            continue
        stream_id = api.stream_id(as_name, channel)
        if stream_id is None:
            continue
        anchor = int(cursor) if cursor is not None else "newest"
        rows, error = read_mod.fetch(
            as_name, channel, limit=constants.TODO_SWEEP_FETCH_LIMIT,
            anchor=anchor, newer=cursor is not None)
        if error is not None:
            raise RuntimeError("todo harvest failed for %s: %s" % (channel, error))
        for message in rows:
            message_id = int(message["id"])
            if cursor is not None and message_id <= int(cursor):
                continue
            messages.append(message_record(channel, stream_id, message, cfg["site"]))
            next_cursor = max(next_cursor, message_id)
    kept, dropped = cap_messages(messages)
    if dropped:
        log.info("todo sweep dropped %d oldest messages at the input cap", dropped)
    return kept, next_cursor, dropped


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


def model_call(messages, run=subprocess.run, cwd=None, cost_fn=store.cost_append):
    prompt = prompts.TODO_SWEEP.format(
        messages=json.dumps(messages, ensure_ascii=False, sort_keys=True))
    return run_model(prompt, run=run, cwd=cwd, lane="todo", cost_fn=cost_fn)


def existing_titles(as_name=constants.OPERATOR_IDENTITY):
    names = {
        store.normalize_topic(row.get("topic")).strip().casefold()
        for row in loops.all_rows().values() if row.get("status") == loops.STATUS_OPEN
    }
    for channel in api.visible_streams(as_name):
        if channel == constants.STATUS_STREAM:
            continue
        stream_id = api.stream_id(as_name, channel)
        if stream_id is None:
            continue
        for topic in api.topics(as_name, stream_id):
            name = topic.get("name") or ""
            if name and not name.strip().startswith(constants.RESOLVED_PREFIX):
                names.add(store.normalize_topic(name).strip().casefold())
    return names


def filter_proposals(rows, messages, proposed, existing):
    if not isinstance(rows, list):
        return []
    sources = {row["permalink"]: row["id"] for row in messages}
    survivors = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"title", "permalink", "why"}:
            continue
        if not all(isinstance(row[key], str) and row[key].strip() for key in row):
            continue
        message_id = sources.get(row["permalink"])
        title = store.normalize_topic(row["title"]).strip().casefold()
        if message_id is None or str(message_id) in proposed or title in existing or message_id in seen:
            continue
        seen.add(message_id)
        survivors.append(dict(row, message_id=message_id))
    return survivors


def _safe_title(text):
    text = " ".join(str(text).split()).replace("@", "@\u200b")
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def render_proposal(rows):
    lines = [
        prompts.TODO_PROPOSAL_ROW.format(
            title=_safe_title(row["title"]), permalink=row["permalink"])
        for row in rows
    ]
    return prompts.TODO_PROPOSAL.format(rows="\n".join(lines))


def run_once(dry_run=False, harvest_fn=None, model_fn=None, existing_fn=None,
             load_fn=None, mutate_fn=None, post_fn=None, now_ts=None):
    harvest_fn = harvest_fn or harvest
    model_fn = model_fn or model_call
    existing_fn = existing_fn or existing_titles
    load_fn = load_fn or store.load
    mutate_fn = mutate_fn or store.mutate
    post_fn = post_fn or (lambda body: send_mod.post(
        constants.OPERATOR_IDENTITY, constants.STATUS_STREAM, constants.BOARD_TOPIC, body))
    state = load_fn("todo")
    messages, next_cursor, dropped = harvest_fn(state.get("cursor"))
    if not messages:
        if not dry_run:
            mutate_fn("todo", lambda data: dict(data, cursor=next_cursor))
        return [], dropped
    rows = filter_proposals(
        model_fn(messages), messages, state.get("proposed", {}), existing_fn())
    if dry_run:
        if rows:
            print(render_proposal(rows))
        return rows, dropped
    if rows:
        post_fn(render_proposal(rows))
    stamp = time.time() if now_ts is None else now_ts

    def save(data):
        data["cursor"] = next_cursor
        proposed = data.setdefault("proposed", {})
        for row in rows:
            proposed[str(row["message_id"])] = stamp

    mutate_fn("todo", save)
    return rows, dropped


def sweep_thread(interval=constants.TODO_SWEEP_MIN * 60):
    while True:
        try:
            run_once()
        except (Exception, SystemExit):
            log.exception("todo sweep pass failed")
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

    survivors = filter_proposals(*cases.TODO_FILTER_INPUT)
    if survivors == cases.TODO_FILTER_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL filter_proposals -> %r wanted %r" % (survivors, cases.TODO_FILTER_EXPECTED))
    proposal = render_proposal(survivors)
    if all(part in proposal for part in cases.TODO_PROPOSAL_CONTAINS):
        passed += 1
    else:
        failed += 1
        print("FAIL render_proposal did not neutralize or link its title")

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
    model_rows = model_call([], run=run_stub, cost_fn=costs.append)
    model_cwd = invoked.get("cwd", "")
    if model_rows == [] and invoked.get("empty") and str(constants.REPO_DIR) not in model_cwd \
            and not any("ZULIP" in key for key in invoked.get("env", {})) \
            and len(costs) == 1 and costs[0]["lane"] == "todo" \
            and costs[0]["usd"] == 0.012 and costs[0]["output_tokens"] == 3:
        passed += 1
    else:
        failed += 1
        print("FAIL model call cwd or environment was not empty and isolated: %r" % invoked)

    state, posts = {}, []

    def mutate(name, fn):
        result = fn(state)
        if isinstance(result, dict):
            state.clear()
            state.update(result)

    rows, got_dropped = run_once(
        harvest_fn=lambda cursor: (cases.TODO_RUN_MESSAGES, 22, 1),
        model_fn=lambda messages: cases.TODO_RUN_MODEL,
        existing_fn=lambda: set(), load_fn=lambda name: state,
        mutate_fn=mutate, post_fn=posts.append, now_ts=1234)
    if rows == cases.TODO_RUN_EXPECTED and got_dropped == 1 \
            and state == {"cursor": 22, "proposed": {"22": 1234}} and len(posts) == 1:
        passed += 1
    else:
        failed += 1
        print("FAIL run_once -> rows=%r dropped=%r state=%r posts=%r" %
              (rows, got_dropped, state, posts))

    cursor_calls = []
    state = {"cursor": cases.TODO_DEFAULT_CURSOR}
    old_harvest = harvest
    try:
        globals()["harvest"] = lambda cursor: cursor_calls.append(cursor) or ([], cursor, 0)
        run_once(load_fn=lambda name: state, mutate_fn=lambda name, fn: fn(state))
    finally:
        globals()["harvest"] = old_harvest
    if cursor_calls == [cases.TODO_DEFAULT_CURSOR]:
        passed += 1
    else:
        failed += 1
        print("FAIL default harvest wiring passed %r" % (cursor_calls,))

    print("todo.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.once and not args.dry_run:
        ap.error("give --once or --dry-run")
    run_once(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
