"""Cached Sonnet topic digests over message deltas; rendering remains deterministic."""

import argparse
import datetime
import json
import logging
import os
import re
import subprocess
import sys
import time

import api
import constants
import prompts
import read as read_mod
import store
import todo

log = logging.getLogger("agent-team.digest")
_LISTENER_START = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+) .*agent-team listener starting",
    re.MULTILINE)
_LAUNCHD_PID = re.compile(r"^\s*pid = (\d+)\s*$", re.MULTILINE)


def digest_key(stream_id, topic):
    return "%s:%s" % (stream_id, store.normalize_topic(topic))


def is_parked(stream_id, topic, load_fn=store.load):
    return digest_key(stream_id, topic) in load_fn(constants.PARKED_STATE)


def fetch_delta(as_name, stream_id, channel, topic, current):
    anchor = current.get("anchor_id")
    rows, error = read_mod.fetch(
        as_name, channel, topic=topic, limit=constants.DIGEST_FETCH_LIMIT,
        anchor=int(anchor) if anchor is not None else "newest", newer=anchor is not None)
    if error is not None:
        raise RuntimeError("digest fetch failed for %s > %s: %s" % (channel, topic, error))
    cfg = api.load(as_name)
    messages = [
        todo.message_record(channel, stream_id, row, cfg["site"])
        for row in rows if anchor is None or int(row["id"]) > int(anchor)
    ]
    next_anchor = max([int(anchor or 0)] + [row["id"] for row in messages])
    kept, dropped = todo.cap_messages(messages, constants.DIGEST_MAX_CHARS)
    if dropped:
        log.info("digest dropped %d oldest messages at the input cap for %s > %s",
                 dropped, channel, topic)
    return kept, next_anchor, dropped


def _listener_start_ts(text):
    matches = _LISTENER_START.findall(text)
    if not matches:
        return None
    try:
        return datetime.datetime.strptime(matches[-1], "%Y-%m-%d %H:%M:%S,%f").timestamp()
    except ValueError:
        return None


def _launchd_start_ts(run=subprocess.run, uid_fn=os.getuid):
    try:
        printed = run(
            ["launchctl", "print", "gui/%d/%s" % (uid_fn(), constants.LAUNCHD_LABEL)],
            capture_output=True, text=True, timeout=constants.DIGEST_FACT_TIMEOUT)
        match = _LAUNCHD_PID.search(printed.stdout or "") if printed.returncode == 0 else None
        if match is None:
            return None
        started = run(
            ["ps", "-o", "lstart=", "-p", match.group(1)], capture_output=True,
            text=True, timeout=constants.DIGEST_FACT_TIMEOUT)
        if started.returncode != 0:
            return None
        return datetime.datetime.strptime(
            (started.stdout or "").strip(), "%a %b %d %H:%M:%S %Y").timestamp()
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def last_restart_ts(log_path=None, run=subprocess.run, uid_fn=os.getuid):
    path = constants.LOGS_DIR / "listener.err.log" if log_path is None else log_path
    try:
        timestamp = _listener_start_ts(path.read_text(errors="replace"))
    except OSError:
        timestamp = None
    return timestamp if timestamp is not None else _launchd_start_ts(run, uid_fn)


def model_call(previous, messages, run_model_fn=todo.run_model,
               restart_ts_fn=last_restart_ts):
    restart_ts = restart_ts_fn()
    prompt = prompts.TOPIC_DIGEST.format(
        previous=json.dumps(previous or {}, ensure_ascii=False, sort_keys=True),
        messages=json.dumps(messages, ensure_ascii=False, sort_keys=True),
        summary_max=constants.DIGEST_SUMMARY_MAX,
        item_max=constants.DIGEST_ITEM_MAX,
        open_max=constants.DIGEST_OPEN_MAX,
        done_max=constants.DIGEST_DONE_MAX,
        last_restart_fact=(prompts.TOPIC_DIGEST_RESTART_FACT.format(last_restart_ts=restart_ts)
                           if restart_ts is not None else ""),
    )
    return run_model_fn(prompt, lane="digest")


def _clip(text, limit):
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit - len(prompts.DIGEST_CLIP_SUFFIX)].rstrip()
    if " " in head:
        head = head.rsplit(" ", 1)[0]
    return head + prompts.DIGEST_CLIP_SUFFIX


def validate_digest(payload, messages, previous, allow_legacy=False):
    if not isinstance(payload, dict) or set(payload) != {"summary", "items"}:
        return None
    if not isinstance(payload["summary"], str) or not payload["summary"].strip() \
            or not isinstance(payload["items"], list):
        return None
    source_ts = {}
    for row in (previous or {}).get("items", []):
        if isinstance(row, dict) and isinstance(row.get("permalink"), str):
            value = row.get("source_ts")
            source_ts[row["permalink"]] = (
                value if isinstance(value, (int, float)) and not isinstance(value, bool) else None)
    for row in messages:
        if isinstance(row, dict) and isinstance(row.get("permalink"), str):
            value = row.get("timestamp")
            source_ts[row["permalink"]] = (
                value if isinstance(value, (int, float)) and not isinstance(value, bool) else None)
    allowed = set(source_ts)
    candidates = []
    seen = set()
    for row in payload["items"]:
        fields = {"done", "text", "permalink"}
        if not isinstance(row, dict) or (set(row) != fields | {"source_ts"}
                                        and not (allow_legacy and set(row) == fields)):
            continue
        if not isinstance(row["done"], bool) or not isinstance(row["text"], str) \
                or not row["text"].strip() or row["permalink"] not in allowed \
                or row["permalink"] in seen:
            continue
        seen.add(row["permalink"])
        candidates.append({
            "done": row["done"],
            "text": _clip(row["text"], constants.DIGEST_ITEM_MAX),
            "permalink": row["permalink"],
            "source_ts": source_ts[row["permalink"]],
        })
    open_items = [row for row in candidates if not row["done"]][:constants.DIGEST_OPEN_MAX]
    done_rows = [row for row in candidates if row["done"]]
    done_items = done_rows[-constants.DIGEST_DONE_MAX:] if constants.DIGEST_DONE_MAX else []
    kept = {id(row) for row in open_items + done_items}
    items = [row for row in candidates if id(row) in kept]
    return {
        "summary": _clip(payload["summary"], constants.DIGEST_SUMMARY_MAX),
        "items": items,
    }


def bound_cached(current):
    if not current:
        return {}
    payload = {name: current.get(name) for name in ("summary", "items")}
    bounded = validate_digest(payload, [], current, allow_legacy=True)
    if bounded is None:
        return {}
    for name in ("anchor_id", "ts"):
        if name in current:
            bounded[name] = current[name]
    return bounded


def safe_text(text):
    text = " ".join(str(text).split()).replace("@", "@\u200b")
    for char in ("\\", "[", "]", "`", "*", "_"):
        text = text.replace(char, "\\" + char)
    return text


def refresh_topic(as_name, stream_id, channel, topic, fetch_fn=None, model_fn=None,
                  load_fn=None, mutate_fn=None, now_ts=None):
    if (topic or "").strip().startswith(constants.RESOLVED_PREFIX):
        return None
    fetch_fn = fetch_fn or fetch_delta
    model_fn = model_fn or model_call
    load_fn = load_fn or store.load
    mutate_fn = mutate_fn or store.mutate
    if is_parked(stream_id, topic, load_fn):
        return None
    key = digest_key(stream_id, topic)
    current = bound_cached(load_fn("digests").get(key, {}))
    messages, next_anchor, dropped = fetch_fn(as_name, stream_id, channel, topic, current)
    if not messages:
        return current or None
    rendered = validate_digest(model_fn(current, messages), messages, current)
    if rendered is None:
        raise ValueError("digest model returned an invalid root schema")
    rendered["anchor_id"] = next_anchor
    rendered["ts"] = time.time() if now_ts is None else now_ts

    def save(data):
        data[key] = rendered

    mutate_fn("digests", save)
    return rendered


def sweep_once(as_name=constants.OPERATOR_IDENTITY, streams_fn=None, stream_id_fn=None,
               topics_fn=None, load_fn=None, refresh_fn=None):
    streams_fn = streams_fn or api.visible_streams
    stream_id_fn = stream_id_fn or api.stream_id
    topics_fn = topics_fn or api.topics
    load_fn = load_fn or store.load
    refresh_fn = refresh_fn or refresh_topic
    cached = load_fn("digests")
    parked = load_fn(constants.PARKED_STATE)
    refreshed = []
    board_channels = {channel for _, channels in constants.BOARD_GROUPS for channel in channels}
    for channel in streams_fn(as_name):
        if channel not in board_channels:
            continue
        stream_id = stream_id_fn(as_name, channel)
        if stream_id is None:
            continue
        for topic in topics_fn(as_name, stream_id):
            name, max_id = topic.get("name") or "", topic.get("max_id")
            if not name or name.strip().startswith(constants.RESOLVED_PREFIX) or max_id is None:
                continue
            if channel == constants.STATUS_STREAM and name == constants.BOARD_TOPIC:
                continue
            if digest_key(stream_id, name) in parked:
                continue
            current = cached.get(digest_key(stream_id, name), {})
            if int(max_id) <= int(current.get("anchor_id") or 0):
                continue
            refresh_fn(as_name, stream_id, channel, name)
            refreshed.append((stream_id, name))
    return refreshed


def _selftest():
    import tests.cases as cases

    passed = failed = 0
    model_calls = []
    model_call({}, [], run_model_fn=lambda prompt, lane: model_calls.append((prompt, lane)) or {},
               restart_ts_fn=lambda: 123.25)
    if len(model_calls) == 1 and model_calls[0][1] == "digest" \
            and "at most 120 characters" in model_calls[0][0] \
            and "at most 5 open items and 2 newest done items" in model_calls[0][0] \
            and "last_restart_ts: 123.250" in model_calls[0][0] \
            and "Prior digest:\n{}" in model_calls[0][0]:
        passed += 1
    else:
        failed += 1
        print("FAIL model_call did not use the digest cost lane: %r" % (model_calls,))
    without_restart = []
    model_call({}, [],
               run_model_fn=lambda prompt, lane: without_restart.append(prompt) or {},
               restart_ts_fn=lambda: None)
    if len(without_restart) == 1 and "last_restart_ts:" not in without_restart[0]:
        passed += 1
    else:
        failed += 1
        print("FAIL model_call did not omit an unknown restart: %r" % without_restart)

    expected_log_ts = datetime.datetime(2026, 8, 17, 23, 0, 0, 250000).timestamp()
    if _listener_start_ts(cases.DIGEST_RESTART_LOG) == expected_log_ts:
        passed += 1
    else:
        failed += 1
        print("FAIL listener start line was not parsed")

    class _ReadableLog:
        def read_text(self, **kwargs):
            return cases.DIGEST_RESTART_LOG

    fallback_calls = []
    log_ts = last_restart_ts(
        _ReadableLog(), run=lambda *args, **kwargs: fallback_calls.append(args))
    if log_ts == expected_log_ts and fallback_calls == []:
        passed += 1
    else:
        failed += 1
        print("FAIL listener log did not take precedence: %r calls=%r" %
              (log_ts, fallback_calls))

    class _MissingLog:
        def read_text(self, **kwargs):
            raise OSError("missing")

    class _Proc:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout

    commands = []

    def run_start(command, **kwargs):
        commands.append(command)
        return _Proc(cases.DIGEST_LAUNCHD_PRINT if command[0] == "launchctl"
                     else cases.DIGEST_PROCESS_START)

    expected_process_ts = datetime.datetime(2026, 8, 17, 22, 30, 0).timestamp()
    process_ts = last_restart_ts(_MissingLog(), run=run_start, uid_fn=lambda: 501)
    if process_ts == expected_process_ts and commands == [
            ["launchctl", "print", "gui/501/%s" % constants.LAUNCHD_LABEL],
            ["ps", "-o", "lstart=", "-p", "4321"]]:
        passed += 1
    else:
        failed += 1
        print("FAIL launchd process start fallback -> %r commands=%r" % (process_ts, commands))
    def run_missing(*args, **kwargs):
        raise OSError("missing")

    unknown = last_restart_ts(_MissingLog(), run=run_missing)
    if unknown is None:
        passed += 1
    else:
        failed += 1
        print("FAIL unreadable restart sources -> %r" % unknown)
    got = validate_digest(*cases.DIGEST_FILTER_INPUT)
    if got == cases.DIGEST_FILTER_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL validate_digest -> %r wanted %r" % (got, cases.DIGEST_FILTER_EXPECTED))
    bounded = validate_digest(*cases.DIGEST_BOUND_INPUT)
    if bounded == cases.DIGEST_BOUND_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL validate_digest bounds -> %r wanted %r" %
              (bounded, cases.DIGEST_BOUND_EXPECTED))
    cached = bound_cached(dict(cases.DIGEST_BOUND_MODEL, anchor_id=22, ts=1234))
    expected_cached = dict(cases.DIGEST_BOUND_CACHED_EXPECTED, anchor_id=22, ts=1234)
    if cached == expected_cached:
        passed += 1
    else:
        failed += 1
        print("FAIL bound_cached -> %r wanted %r" % (cached, expected_cached))
    migrated = bound_cached(cases.DIGEST_PREVIOUS)
    expected_migrated = dict(cases.DIGEST_PREVIOUS)
    expected_migrated["items"] = [dict(cases.DIGEST_PREVIOUS["items"][0], source_ts=None)]
    if migrated == expected_migrated:
        passed += 1
    else:
        failed += 1
        print("FAIL old cached item migration -> %r wanted %r" % (migrated, expected_migrated))
    if all(validate_digest(*row) is None for row in cases.DIGEST_BAD_ROOTS):
        passed += 1
    else:
        failed += 1
        print("FAIL validate_digest accepted a bad root")
    safe = safe_text(cases.DIGEST_UNSAFE_TEXT)
    if safe == cases.DIGEST_SAFE_TEXT:
        passed += 1
    else:
        failed += 1
        print("FAIL safe_text -> %r wanted %r" % (safe, cases.DIGEST_SAFE_TEXT))

    state = {"7:topic": dict(cases.DIGEST_PREVIOUS)}

    def mutate(name, fn):
        fn(state)

    refreshed = refresh_topic(
        "bob", 7, "setup", "topic",
        fetch_fn=lambda *a: (cases.DIGEST_MESSAGES, 22, 0),
        model_fn=lambda previous, messages: cases.DIGEST_MODEL,
        load_fn=lambda name: state if name == "digests" else {}, mutate_fn=mutate, now_ts=1234)
    expected_state = {"7:topic": dict(cases.DIGEST_FILTER_EXPECTED, anchor_id=22, ts=1234)}
    if refreshed == expected_state["7:topic"] and state == expected_state:
        passed += 1
    else:
        failed += 1
        print("FAIL refresh_topic -> %r state=%r" % (refreshed, state))

    parked_calls = []
    parked_result = refresh_topic(
        "bob", 7, "setup", "topic",
        fetch_fn=lambda *args: parked_calls.append(args),
        load_fn=lambda name: {"7:topic": 1234} if name == constants.PARKED_STATE else {})
    if parked_result is None and parked_calls == []:
        passed += 1
    else:
        failed += 1
        print("FAIL parked refresh ran fetch: %r %r" % (parked_result, parked_calls))

    calls = []
    swept = sweep_once(
        streams_fn=lambda as_name: ["random", "setup"],
        stream_id_fn=lambda as_name, channel: 7,
        topics_fn=lambda as_name, stream_id: cases.DIGEST_SWEEP_TOPICS,
        load_fn=lambda name: cases.DIGEST_SWEEP_STATE if name == "digests" else {},
        refresh_fn=lambda *args: calls.append(args))
    if swept == cases.DIGEST_SWEEP_EXPECTED and calls == [
            (constants.OPERATOR_IDENTITY, 7, "setup", "dirty")]:
        passed += 1
    else:
        failed += 1
        print("FAIL sweep_once -> %r calls=%r" % (swept, calls))

    calls = []
    swept = sweep_once(
        streams_fn=lambda as_name: ["setup"],
        stream_id_fn=lambda as_name, channel: 7,
        topics_fn=lambda as_name, stream_id: cases.DIGEST_SWEEP_TOPICS,
        load_fn=lambda name: ({"7:dirty": 1} if name == constants.PARKED_STATE
                              else cases.DIGEST_SWEEP_STATE),
        refresh_fn=lambda *args: calls.append(args))
    if swept == [] and calls == []:
        passed += 1
    else:
        failed += 1
        print("FAIL parked sweep refreshed: %r calls=%r" % (swept, calls))

    print("digest.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    ap.error("nothing to do; digest.py is a library")
