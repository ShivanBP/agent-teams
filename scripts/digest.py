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
    if not isinstance(payload, dict) or not {"summary", "items"} <= set(payload):
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
                  load_fn=None, mutate_fn=None, now_ts=None, force=False):
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
    messages, next_anchor, dropped = fetch_fn(
        as_name, stream_id, channel, topic, {} if force else current)
    if not messages:
        return current or None
    rendered = validate_digest(model_fn(current, messages), messages, current)
    if rendered is None:
        raise ValueError("digest model returned an invalid root schema")
    rendered["anchor_id"] = next_anchor
    rendered["ts"] = time.time() if now_ts is None else now_ts

    def save(data):
        stored = data.get(key, {}).get("anchor_id") or 0
        if stored >= next_anchor:
            return
        data[key] = rendered

    mutate_fn("digests", save)
    return rendered


def sweep_once(as_name=constants.BRIDGE_IDENTITY, streams_fn=None, stream_id_fn=None,
               topics_fn=None, load_fn=None, refresh_fn=None, groups=None):
    streams_fn = streams_fn or api.visible_streams
    stream_id_fn = stream_id_fn or api.stream_id
    topics_fn = topics_fn or api.topics
    load_fn = load_fn or store.load
    refresh_fn = refresh_fn or refresh_topic
    cached = load_fn("digests")
    parked = load_fn(constants.PARKED_STATE)
    refreshed = []
    board_channels = constants.board_channels(groups)
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
    from tests import digest_selftest
    return digest_selftest.run(sys.modules[__name__])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    ap.error("nothing to do; digest.py is a library")
