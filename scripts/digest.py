"""Cached Sonnet topic digests over message deltas; rendering remains deterministic."""

import argparse
import json
import logging
import sys
import time

import api
import constants
import prompts
import read as read_mod
import store
import todo

log = logging.getLogger("agent-team.digest")


def digest_key(stream_id, topic):
    return "%s:%s" % (stream_id, store.normalize_topic(topic))


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


def model_call(previous, messages, run_model_fn=todo.run_model):
    prompt = prompts.TOPIC_DIGEST.format(
        previous=json.dumps(previous or {}, ensure_ascii=False, sort_keys=True),
        messages=json.dumps(messages, ensure_ascii=False, sort_keys=True),
        summary_max=constants.DIGEST_SUMMARY_MAX,
        item_max=constants.DIGEST_ITEM_MAX,
        open_max=constants.DIGEST_OPEN_MAX,
        done_max=constants.DIGEST_DONE_MAX,
    )
    return run_model_fn(prompt, lane="digest")


def validate_digest(payload, messages, previous):
    if not isinstance(payload, dict) or set(payload) != {"summary", "items"}:
        return None
    if not isinstance(payload["summary"], str) or not payload["summary"].strip() \
            or not isinstance(payload["items"], list):
        return None
    allowed = {row["permalink"] for row in messages}
    allowed.update(
        row.get("permalink") for row in (previous or {}).get("items", [])
        if isinstance(row, dict) and isinstance(row.get("permalink"), str))
    candidates = []
    seen = set()
    for row in payload["items"]:
        if not isinstance(row, dict) or set(row) != {"done", "text", "permalink"}:
            continue
        if not isinstance(row["done"], bool) or not isinstance(row["text"], str) \
                or not row["text"].strip() or row["permalink"] not in allowed \
                or row["permalink"] in seen:
            continue
        seen.add(row["permalink"])
        candidates.append(dict(row, text=row["text"].strip()[:constants.DIGEST_ITEM_MAX]))
    open_items = [row for row in candidates if not row["done"]][:constants.DIGEST_OPEN_MAX]
    done_rows = [row for row in candidates if row["done"]]
    done_items = done_rows[-constants.DIGEST_DONE_MAX:] if constants.DIGEST_DONE_MAX else []
    kept = {id(row) for row in open_items + done_items}
    items = [row for row in candidates if id(row) in kept]
    return {
        "summary": payload["summary"].strip()[:constants.DIGEST_SUMMARY_MAX],
        "items": items,
    }


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
    key = digest_key(stream_id, topic)
    current = load_fn("digests").get(key, {})
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
    model_call({}, [], run_model_fn=lambda prompt, lane: model_calls.append((prompt, lane)) or {})
    if len(model_calls) == 1 and model_calls[0][1] == "digest" \
            and "at most 120 characters" in model_calls[0][0] \
            and "at most 5 open items and 2 newest done items" in model_calls[0][0] \
            and "Prior digest:\n{}" in model_calls[0][0]:
        passed += 1
    else:
        failed += 1
        print("FAIL model_call did not use the digest cost lane: %r" % (model_calls,))
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
        load_fn=lambda name: state, mutate_fn=mutate, now_ts=1234)
    expected_state = {"7:topic": dict(cases.DIGEST_FILTER_EXPECTED, anchor_id=22, ts=1234)}
    if refreshed == expected_state["7:topic"] and state == expected_state:
        passed += 1
    else:
        failed += 1
        print("FAIL refresh_topic -> %r state=%r" % (refreshed, state))

    calls = []
    swept = sweep_once(
        streams_fn=lambda as_name: ["random", "setup"],
        stream_id_fn=lambda as_name, channel: 7,
        topics_fn=lambda as_name, stream_id: cases.DIGEST_SWEEP_TOPICS,
        load_fn=lambda name: cases.DIGEST_SWEEP_STATE,
        refresh_fn=lambda *args: calls.append(args))
    if swept == cases.DIGEST_SWEEP_EXPECTED and calls == [
            (constants.OPERATOR_IDENTITY, 7, "setup", "dirty")]:
        passed += 1
    else:
        failed += 1
        print("FAIL sweep_once -> %r calls=%r" % (swept, calls))

    print("digest.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    ap.error("nothing to do; digest.py is a library")
