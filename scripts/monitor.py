"""One-shot terminal snapshot of persona activity, cost, and loop kicks."""

import argparse
import datetime
import difflib
import json
import logging
import sys
import time

import api
import constants
import digest
import loops
import personas
import prompts
import runner
import send as send_mod
import store

log = logging.getLogger("agent-team.monitor")


def _ledger_rows(prefix):
    name = "%s-%s" % (prefix, datetime.date.today().isoformat())
    return store.load(name).get("rows", [])


def snapshot(inflight=None, cost_rows=None, kick_rows=None, matrix=None):
    inflight = store.inflight_all() if inflight is None else inflight
    cost_rows = _ledger_rows("cost") if cost_rows is None else cost_rows
    kick_rows = _ledger_rows("kicks") if kick_rows is None else kick_rows
    defaults = matrix or {name: constants.matrix_defaults(name) for name in personas.PERSONAS}
    data = {}
    for name in personas.PERSONAS:
        data[name] = {
            "provider": defaults[name]["provider"],
            "status": prompts.BOARD_IDLE_STATUS,
            "topic": None,
            "cost_today": 0.0,
            "runs_today": 0,
            "kicks_today": 0,
        }
    for info in inflight.values():
        name = info.get("persona")
        if name not in data:
            continue
        data[name]["status"] = prompts.BOARD_RUNNING
        data[name]["topic"] = info.get("topic")
        data[name]["provider"] = info.get("provider", data[name]["provider"])
    for row in cost_rows:
        name = row.get("persona")
        if name not in data:
            continue
        data[name]["cost_today"] += float(row.get("usd") or 0.0)
        data[name]["runs_today"] += 1
    for row in kick_rows:
        name = row.get("persona")
        if name in data:
            data[name]["kicks_today"] += 1
    return data


def lane_rows(inflight=None, now_ts=None, log_mtimes=None, actions=None):
    inflight = store.inflight_all() if inflight is None else inflight
    now_ts = time.time() if now_ts is None else now_ts
    rows = []
    for lane, info in sorted(inflight.items()):
        started = info.get("ts")
        running_s = max(0, now_ts - started) if started is not None else None
        provider = info.get("provider") or "claude"
        mtime = None
        if log_mtimes is not None:
            mtime = log_mtimes.get(lane)
        else:
            try:
                mtime = runner._wake_log_path(lane).stat().st_mtime
            except OSError:
                pass
        idle_s = max(0, now_ts - mtime) if mtime is not None and started is not None \
            and mtime >= started else None
        action = actions.get(lane) if actions is not None else \
            (runner.last_action(lane) if idle_s is not None else None)
        rows.append({
            "lane": lane,
            "stream_id": info.get("stream_id"),
            "message_id": info.get("message_id"),
            "persona": info.get("persona") or prompts.BOARD_UNKNOWN,
            "provider": provider,
            "topic": info.get("topic") or prompts.BOARD_UNKNOWN,
            "running_s": running_s,
            "idle_s": idle_s,
            "last_action": action,
            "stuck": running_s is not None and running_s > constants.STALL_MIN * 60,
        })
    return rows


def format_age(seconds):
    if seconds is None:
        return prompts.BOARD_UNKNOWN
    minutes = int(seconds // 60)
    if minutes < 60:
        return prompts.BOARD_AGE_MIN.format(minutes=minutes)
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return prompts.BOARD_AGE_HOUR.format(hours=hours, minutes=minutes)
    days, hours = divmod(hours, 24)
    return prompts.BOARD_AGE_DAY.format(days=days, hours=hours)


def render_lanes(rows):
    table = [prompts.BOARD_LANE_HEADERS]
    for row in rows:
        table.append((row["persona"], row["provider"], row["topic"],
                      format_age(row["running_s"]), format_age(row["idle_s"]),
                      row["last_action"] or prompts.BOARD_UNKNOWN,
                      prompts.BOARD_STUCK if row["stuck"] else ""))
    widths = [max(len(str(row[i])) for row in table) for i in range(len(table[0]))]
    return "\n".join("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)).rstrip()
                     for row in table)


def _running_status(row):
    if row["status"] == prompts.BOARD_RUNNING and row["topic"]:
        return prompts.BOARD_RUNNING_TOPIC.format(topic=row["topic"])
    return row["status"]


def render_table(data):
    rows = [prompts.BOARD_PERSONA_HEADERS]
    for name in personas.PERSONAS:
        row = data[name]
        status = _running_status(row)
        rows.append((name, row["provider"], status,
                     prompts.BOARD_COST.format(usd=row["cost_today"]), str(row["kicks_today"])))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)).rstrip()
                     for row in rows)


def render_activity(data):
    rows = []
    for name in personas.PERSONAS:
        row = data[name]
        status = _running_status(row)
        rows.append(prompts.BOARD_ACTIVITY_ROW.format(
            persona=digest.safe_text(name).replace("|", "\\|"),
            provider=digest.safe_text(row["provider"]).replace("|", "\\|"),
            status=digest.safe_text(status).replace("|", "\\|"),
            cost=prompts.BOARD_COST.format(usd=row["cost_today"]),
            kicks=row["kicks_today"],
        ))
    return prompts.BOARD_ACTIVITY.format(rows="\n".join(rows))


def _message(as_name, message_id):
    payload = api.request(
        api.load(as_name), "GET", "/api/v1/messages/%d" % int(message_id),
        {"apply_markdown": False},
    )
    return payload.get("message") if payload.get("result") == "success" else None


def _todo_key(row):
    return row.get("channel"), store.normalize_topic(row.get("name"))


def unresolved_topics(as_name=constants.OPERATOR_IDENTITY, stream_id_fn=None,
                      topics_fn=None, load_fn=None):
    stream_id_fn = stream_id_fn or api.stream_id
    topics_fn = topics_fn or api.topics
    load_fn = load_fn or api.load
    cfg = load_fn(as_name)
    rows = []
    for _, channels in constants.BOARD_GROUPS:
        for channel in channels:
            stream_id = stream_id_fn(as_name, channel)
            if stream_id is None:
                continue
            for topic in topics_fn(as_name, stream_id):
                name, message_id = topic.get("name") or "", topic.get("max_id")
                if not name or name.strip().startswith(constants.RESOLVED_PREFIX) \
                        or message_id is None:
                    continue
                rows.append({
                    "key": digest.digest_key(stream_id, name),
                    "channel": channel,
                    "stream_id": stream_id,
                    "name": name,
                    "permalink": api.permalink(
                        cfg["site"], stream_id, channel, name, message_id),
                })
    return rows


def parked_topics(as_name=constants.OPERATOR_IDENTITY, topics=None,
                  load_fn=None, mutate_fn=None):
    load_fn = load_fn or store.load
    mutate_fn = mutate_fn or store.mutate
    topics = unresolved_topics(as_name) if topics is None else topics
    current = load_fn(constants.PARKED_STATE)
    valid = {row["key"]: row for row in topics}
    stale = set(current) - set(valid)
    if stale:
        def prune(data):
            for key in stale:
                data.pop(key, None)
            return data

        mutate_fn(constants.PARKED_STATE, prune)
        current = {key: value for key, value in current.items() if key not in stale}
    return [row for row in topics if row["key"] in current]


def _topic_match(channel, topic, topics):
    for row in topics:
        if row["channel"] == channel and row["name"] == topic:
            return row
    names = [row["name"] for row in topics if row["channel"] == channel]
    if not names:
        names = ["%s > %s" % (row["channel"], row["name"]) for row in topics]
    nearest = difflib.get_close_matches(topic, names, n=3, cutoff=0)
    raise ValueError("no exact unresolved topic %s > %s; nearest: %s" %
                     (channel, topic, ", ".join(nearest) if nearest else "none"))


def set_parked(channel, topic, parked, as_name=constants.OPERATOR_IDENTITY,
               topics=None, mutate_fn=None, now_ts=None):
    topics = unresolved_topics(as_name) if topics is None else topics
    row = _topic_match(channel.strip(), topic.strip(), topics)
    mutate_fn = mutate_fn or store.mutate

    def save(data):
        if parked:
            data[row["key"]] = time.time() if now_ts is None else now_ts
        else:
            data.pop(row["key"], None)
        return data

    mutate_fn(constants.PARKED_STATE, save)
    return row


def _loop_todos(as_name):
    cfg = api.load(as_name)
    rows = []
    for row in loops.all_rows().values():
        if row.get("status") != loops.STATUS_OPEN:
            continue
        channel, topic, message_id = row.get("channel"), row.get("topic"), row.get("header_id")
        stream_id = api.stream_id(as_name, channel)
        if stream_id is None or not topic or message_id is None:
            continue
        rows.append({
            "channel": channel,
            "stream_id": stream_id,
            "name": topic,
            "max_id": int(message_id),
            "timestamp": row.get("opened_ts") or 0,
            "permalink": api.permalink(cfg["site"], stream_id, channel, topic, message_id),
        })
    return rows


def _topic_todos(as_name, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    cutoff = now_ts - constants.BOARD_TOPIC_DAYS * 24 * 60 * 60
    cfg = api.load(as_name)
    rows = []
    board_channels = {channel for _, channels in constants.BOARD_GROUPS for channel in channels}
    for channel in api.visible_streams(as_name):
        if channel not in board_channels:
            continue
        stream_id = api.stream_id(as_name, channel)
        if stream_id is None:
            continue
        for topic in api.topics(as_name, stream_id):
            name, message_id = topic.get("name"), topic.get("max_id")
            if not name or name.strip().startswith(constants.RESOLVED_PREFIX) or message_id is None:
                continue
            message = _message(as_name, message_id)
            if message is None:
                continue
            if message.get("timestamp", 0) < cutoff:
                break
            rows.append({
                "channel": channel,
                "stream_id": stream_id,
                "name": name,
                "max_id": int(message_id),
                "timestamp": message.get("timestamp", 0),
                "permalink": api.permalink(cfg["site"], stream_id, channel, name, message_id),
            })
    return rows


def merge_todos(loop_rows, topic_rows):
    merged = []
    seen = {}
    for row in list(loop_rows) + list(topic_rows):
        key = _todo_key(row)
        if key in seen:
            prior = seen[key]
            for name, value in row.items():
                if name not in prior or prior[name] is None:
                    prior[name] = value
            continue
        copy = dict(row)
        seen[key] = copy
        merged.append(copy)
    return merged


def _digest_stamp(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else prompts.BOARD_UNKNOWN


def _show_digest_items(topic, now_ts):
    timestamp = topic.get("timestamp")
    cutoff = now_ts - constants.BOARD_IDLE_HOURS * 60 * 60
    return timestamp is None or float(timestamp) >= cutoff


def _render_topic(topic, topic_lanes, cached, show_items):
    lines = [prompts.BOARD_TOPIC_ROW.format(
        topic=digest.safe_text(topic["name"]), permalink=topic["permalink"])]
    if cached:
        lines.append(prompts.BOARD_DIGEST_LINE.format(
            summary=digest.safe_text(cached.get("summary") or prompts.BOARD_UNKNOWN),
            stamp=_digest_stamp(cached.get("ts"))))
        for item in cached.get("items", []) if show_items else []:
            lines.append(prompts.BOARD_ITEM.format(
                mark="x" if item.get("done") else " ", text=digest.safe_text(item.get("text") or ""),
                permalink=item.get("permalink") or topic["permalink"]))
    else:
        lines.append(prompts.BOARD_DIGEST_PENDING)
    for lane in topic_lanes:
        action = prompts.BOARD_ACTION.format(
            action=lane["last_action"], age=format_age(lane["idle_s"])) \
            if lane["last_action"] else prompts.BOARD_ACTION_UNKNOWN
        lines.append(prompts.BOARD_LANE.format(
            persona=digest.safe_text(lane["persona"]), provider=digest.safe_text(lane["provider"]),
            running=format_age(lane["running_s"]), idle=format_age(lane["idle_s"]),
            action=action,
            stuck=prompts.BOARD_STUCK_SUFFIX if lane["stuck"] else ""))
    return "\n".join(lines)


def _render_parked(rows, lane_map):
    if not rows:
        return ""
    rendered = []
    for row in rows:
        lanes = "".join(prompts.BOARD_PARKED_LANE.format(
            persona=digest.safe_text(lane["persona"]),
            running=format_age(lane["running_s"]),
            stuck=prompts.BOARD_STUCK_SUFFIX if lane["stuck"] else "",
        ) for lane in lane_map.get((row["stream_id"], store.normalize_topic(row["name"])), []))
        rendered.append(prompts.BOARD_PARKED_ROW.format(
            topic=digest.safe_text(row["name"]), permalink=row["permalink"], lanes=lanes))
    return prompts.BOARD_PARKED.format(count=len(rows), rows="\n".join(rendered))


def render_board(lanes=None, persona_rows=None, todos=None, digests=None,
                 as_name=constants.OPERATOR_IDENTITY, now_ts=None, parked=None):
    return "\n\n".join(content for _, content in _board_sections(
        lanes, persona_rows, todos, digests, as_name, now_ts, parked))


def _board_sections(lanes=None, persona_rows=None, todos=None, digests=None,
                    as_name=constants.OPERATOR_IDENTITY, now_ts=None, parked=None):
    now_ts = time.time() if now_ts is None else now_ts
    lanes = lane_rows() if lanes is None else lanes
    persona_rows = snapshot() if persona_rows is None else persona_rows
    supplied_todos = todos is not None
    if todos is None:
        todos = merge_todos(_loop_todos(as_name), _topic_todos(as_name))
    if parked is None:
        parked = [] if supplied_todos else parked_topics(as_name)
    digests = store.load("digests") if digests is None else digests
    lane_map = {}
    for lane in lanes:
        key = (lane.get("stream_id"), store.normalize_topic(lane.get("topic")))
        lane_map.setdefault(key, []).append(lane)
    parked_keys = {row["key"] for row in parked}
    topic_map = {
        (row.get("channel"), store.normalize_topic(row.get("name"))): row
        for row in todos
        if digest.digest_key(row.get("stream_id"), row.get("name")) not in parked_keys
    }
    sections = [("activity", render_activity(persona_rows))]
    for group, channels in constants.BOARD_GROUPS:
        group_lines = []
        for channel in channels:
            channel_topics = [row for (name, _), row in topic_map.items() if name == channel]
            if not channel_topics:
                continue
            group_lines.append(prompts.BOARD_CHANNEL_ROW.format(
                channel=digest.safe_text(channel)))
            for topic in channel_topics:
                key = (topic.get("stream_id"), store.normalize_topic(topic.get("name")))
                cached = digest.bound_cached(digests.get(digest.digest_key(*key), {})) \
                    if key[0] is not None else {}
                group_lines.append(_render_topic(
                    topic, lane_map.get(key, []), cached,
                    _show_digest_items(topic, now_ts)))
        body = prompts.BOARD_GROUP_HEADING.format(group=group)
        if group_lines:
            body += "\n" + "\n".join(group_lines)
        sections.append((group.casefold(), body))
    parked_body = _render_parked(parked, lane_map)
    if parked_body:
        name, body = sections[-1]
        sections[-1] = (name, body + "\n\n" + parked_body)
    return sections


def board_parts(limit, lanes=None, persona_rows=None, todos=None, digests=None,
                as_name=constants.OPERATOR_IDENTITY, now_ts=None, force_split=False,
                parked=None):
    sections = _board_sections(lanes, persona_rows, todos, digests, as_name, now_ts, parked)
    combined = "\n\n".join(content for _, content in sections)
    if not force_split and len(combined) <= limit:
        return {"activity": combined}
    return dict(sections)


def _current_content(as_name, message_id):
    message = _message(as_name, message_id)
    return message.get("content") if message else None


def _board_working(state_name, message_id=None):
    def save(data):
        if message_id is not None:
            data["message_id"] = message_id
        data.pop("failed", None)
        return data

    store.mutate(state_name, save)


def _board_failed(as_name, state_name, section):
    claimed = []

    def claim(data):
        if not data.get("failed"):
            data["failed"] = True
            claimed.append(True)
        return data

    store.mutate(state_name, claim)
    if not claimed:
        return
    try:
        send_mod.post(
            as_name, constants.STATUS_STREAM, constants.ALERTS_TOPIC,
            prompts.BOARD_UPDATE_ALERT.format(section=section))
    except (Exception, SystemExit):
        log.exception("failed to post board update alert for section %s", section)

        def clear(data):
            data.pop("failed", None)
            return data

        store.mutate(state_name, clear)


def update_board(as_name=constants.OPERATOR_IDENTITY, content=None, contents=None):
    split_live = any(store.load(constants.BOARD_STATE_KEYS[name]).get("message_id")
                     for name in ("workshop", "domains"))
    if contents is None:
        contents = {"activity": content} if content is not None else board_parts(
            api.window(as_name), as_name=as_name, force_split=split_live)
    results = {}
    for name, body in contents.items():
        state_name = constants.BOARD_STATE_KEYS[name]
        message_id = store.load(state_name).get("message_id")
        try:
            if message_id is None:
                message_id = send_mod.post(
                    as_name, constants.STATUS_STREAM, constants.BOARD_TOPIC, body)
                _board_working(state_name, message_id)
                results[name] = (message_id, True)
                continue
            if _current_content(as_name, message_id) == body:
                _board_working(state_name)
                results[name] = (message_id, False)
                continue
            send_mod.update(as_name, message_id, body)
            _board_working(state_name)
            results[name] = (message_id, True)
        except (Exception, SystemExit):
            log.exception("board section %s failed to update", name)
            results[name] = (message_id, None)
            try:
                _board_failed(as_name, state_name, name)
            except (Exception, SystemExit):
                log.exception("board section %s failure state could not be saved", name)
    return results


def refresh_board(channel=None, topic=None, digests=False,
                  as_name=constants.OPERATOR_IDENTITY, topics=None,
                  refresh_fn=None, sweep_fn=None, update_fn=None):
    refresh_fn = refresh_fn or digest.refresh_topic
    sweep_fn = sweep_fn or digest.sweep_once
    update_fn = update_fn or update_board
    if digests:
        sweep_fn(as_name)
    elif channel is not None:
        row = _topic_match(
            channel.strip(), topic.strip(),
            unresolved_topics(as_name) if topics is None else topics)
        refresh_fn(
            as_name, row["stream_id"], row["channel"], row["name"], force=True)
    return update_fn(as_name=as_name)


def _selftest():
    import tests.cases as cases

    passed = failed = 0
    try:
        store.inflight_all()
        _ledger_rows("cost")
        _ledger_rows("kicks")
        passed += 3
    except (OSError, ValueError) as exc:
        failed += 1
        print("FAIL monitor state paths are not readable: %s" % exc)

    got = snapshot(**cases.MONITOR_INPUT)
    if got == cases.MONITOR_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL snapshot(...) -> %r wanted %r" % (got, cases.MONITOR_EXPECTED))
    table = render_table(got)
    if table.startswith("Persona") and len(table.splitlines()) == len(personas.PERSONAS) + 1:
        passed += 1
    else:
        failed += 1
        print("FAIL rendered table is empty or incomplete")
    activity = render_activity(got)
    if activity.startswith("## Activity today\n\n| Persona |") \
            and len(activity.splitlines()) == len(personas.PERSONAS) + 4:
        passed += 1
    else:
        failed += 1
        print("FAIL activity Markdown table is empty or incomplete")
    lanes = lane_rows(**cases.MONITOR_LANE_INPUT)
    if lanes == cases.MONITOR_LANE_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL lane_rows(...) -> %r wanted %r" % (lanes, cases.MONITOR_LANE_EXPECTED))
    lane_table = render_lanes(lanes)
    if "STUCK" in lane_table and lane_table.count("jan") == 2 and "-" in lane_table:
        passed += 1
    else:
        failed += 1
        print("FAIL rendered lanes lost duplicate personas, idle fallback, or STUCK")
    for seconds, expected in cases.MONITOR_AGES:
        got_age = format_age(seconds)
        if got_age == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL format_age(%r) -> %r wanted %r" % (seconds, got_age, expected))
    for now_ts, timestamp, expected in cases.BOARD_ITEM_VISIBILITY:
        got_visibility = _show_digest_items({"timestamp": timestamp}, now_ts)
        if got_visibility == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _show_digest_items(%r, %r) -> %r wanted %r" %
                  (now_ts, timestamp, got_visibility, expected))
    merged = merge_todos(*cases.BOARD_TODO_INPUT)
    if merged == cases.BOARD_TODO_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL merge_todos(...) -> %r wanted %r" % (merged, cases.BOARD_TODO_EXPECTED))
    board = render_board(
        cases.BOARD_RENDER_LANES, got, cases.BOARD_RENDER_TOPICS,
        cases.BOARD_RENDER_DIGESTS, now_ts=200000)
    if all(part in board for part in cases.BOARD_RENDER_CONTAINS) and \
            all(part not in board for part in cases.BOARD_RENDER_FORBIDDEN):
        passed += 1
    else:
        failed += 1
        print("FAIL render_board(...) missing a required section or link")
    parked_board = render_board(
        cases.BOARD_RENDER_LANES, got, cases.BOARD_RENDER_TOPICS,
        cases.BOARD_RENDER_DIGESTS, now_ts=200000, parked=cases.BOARD_RENDER_PARKED)
    if all(part in parked_board for part in cases.BOARD_PARKED_CONTAINS) \
            and all(part not in parked_board for part in cases.BOARD_PARKED_FORBIDDEN):
        passed += 1
    else:
        failed += 1
        print("FAIL parked render lost its spoiler, link, or live lane")
    combined_parts = board_parts(
        10000, cases.BOARD_RENDER_LANES, got, cases.BOARD_RENDER_TOPICS,
        cases.BOARD_RENDER_DIGESTS, now_ts=200000)
    split_parts = board_parts(
        1, cases.BOARD_RENDER_LANES, got, cases.BOARD_RENDER_TOPICS,
        cases.BOARD_RENDER_DIGESTS, now_ts=200000)
    if combined_parts == {"activity": board} \
            and list(split_parts) == ["activity", "workshop", "domains"]:
        passed += 1
    else:
        failed += 1
        print("FAIL board_parts combined=%r split=%r" %
              (list(combined_parts), list(split_parts)))

    saved_topics = (api.visible_streams, api.stream_id, api.topics, api.load, _message)
    topic_calls = []
    try:
        api.visible_streams = lambda as_name: [constants.STATUS_STREAM, "setup"]
        api.stream_id = lambda as_name, channel: 9 if channel == constants.STATUS_STREAM else 7
        api.topics = lambda as_name, stream_id: ([
            {"name": constants.BOARD_TOPIC, "max_id": 12},
        ] if stream_id == 9 else [
            {"name": "recent", "max_id": 11},
            {"name": constants.RESOLVED_PREFIX + " done", "max_id": 10},
            {"name": "old", "max_id": 9},
            {"name": "older", "max_id": 8},
        ])
        api.load = lambda as_name: {"site": "https://example"}
        globals()["_message"] = lambda as_name, message_id: topic_calls.append(message_id) or {
            "timestamp": {12: 999950, 11: 999900, 9: 100, 8: 50}[message_id]}
        recent = _topic_todos("bridge", now_ts=1000000)
    finally:
        api.visible_streams, api.stream_id, api.topics, api.load = saved_topics[:4]
        globals()["_message"] = saved_topics[4]
    if recent == cases.BOARD_RECENT_EXPECTED and topic_calls == [12, 11, 9]:
        passed += 1
    else:
        failed += 1
        print("FAIL recent topic sweep -> %r calls=%r" % (recent, topic_calls))

    park_state = {"7:Build board": 1, "9:resolved": 2}

    def mutate_parked(name, fn):
        result = fn(park_state)
        if isinstance(result, dict) and result is not park_state:
            park_state.clear()
            park_state.update(result)

    parked = parked_topics(
        topics=cases.PARK_TOPICS, load_fn=lambda name: dict(park_state),
        mutate_fn=mutate_parked)
    if parked == [cases.PARK_TOPICS[0]] and park_state == {"7:Build board": 1}:
        passed += 1
    else:
        failed += 1
        print("FAIL parked_topics prune -> %r state=%r" % (parked, park_state))

    unresolved = unresolved_topics(
        "bob", stream_id_fn=lambda as_name, channel: 7 if channel == "setup" else None,
        topics_fn=lambda as_name, stream_id: cases.PARK_API_TOPICS,
        load_fn=lambda as_name: {"site": "https://example"})
    if unresolved == cases.PARK_API_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL unresolved_topics -> %r wanted %r" %
              (unresolved, cases.PARK_API_EXPECTED))

    park_state = {}
    parked_row = set_parked(
        "setup", "Build board", True, topics=cases.PARK_TOPICS,
        mutate_fn=mutate_parked, now_ts=1234)
    parked_state = dict(park_state)
    unparked_row = set_parked(
        "setup", "Build board", False, topics=cases.PARK_TOPICS,
        mutate_fn=mutate_parked, now_ts=1235)
    try:
        set_parked("setup", "Build bord", True, topics=cases.PARK_TOPICS,
                   mutate_fn=mutate_parked)
        mismatch = "accepted"
    except ValueError as exc:
        mismatch = str(exc)
    if parked_row == cases.PARK_TOPICS[0] and unparked_row == cases.PARK_TOPICS[0] \
            and parked_state == {"7:Build board": 1234} and park_state == {} \
            and "Build board" in mismatch:
        passed += 1
    else:
        failed += 1
        print("FAIL set_parked exact control -> %r %r %r %r" %
              (parked_row, unparked_row, parked_state, mismatch))

    refreshes, sweeps, board_refreshes = [], [], []
    update_stub = lambda **kwargs: board_refreshes.append(kwargs) or {"activity": (99, False)}
    single_refresh = refresh_board(
        "setup", "Build board", topics=cases.PARK_TOPICS,
        refresh_fn=lambda *args, **kwargs: refreshes.append((args, kwargs)),
        update_fn=update_stub)
    digest_refresh = refresh_board(
        digests=True, sweep_fn=lambda as_name: sweeps.append(as_name), update_fn=update_stub)
    board_refresh = refresh_board(update_fn=update_stub)
    if refreshes == [(("bridge", 7, "setup", "Build board"), {"force": True})] \
            and sweeps == [constants.OPERATOR_IDENTITY] \
            and board_refreshes == [{"as_name": constants.OPERATOR_IDENTITY}] * 3 \
            and single_refresh == digest_refresh == board_refresh == {"activity": (99, False)}:
        passed += 1
    else:
        failed += 1
        print("FAIL refresh_board modes -> %r %r %r" %
              (refreshes, sweeps, board_refreshes))

    states, current, posts, updates = {}, {}, [], []
    saved = store.load, store.mutate, send_mod.post, send_mod.update, _current_content
    try:
        store.load = lambda name: dict(states.get(name, {}))

        def mutate_state(name, fn):
            states[name] = fn(dict(states.get(name, {})))

        store.mutate = mutate_state
        send_mod.post = lambda *args: posts.append(args) or (98 + len(posts))
        send_mod.update = lambda *args: updates.append(args) or args[1]
        globals()["_current_content"] = lambda as_name, message_id: current.get(message_id)
        first = update_board(content="one")
        current[99] = "one"
        unchanged = update_board(content="one")
        changed = update_board(content="two")
        current[99] = "two"
        split = update_board(contents={
            "activity": "activity", "workshop": "workshop", "domains": "domains"})
    finally:
        store.load, store.mutate, send_mod.post, send_mod.update = saved[:4]
        globals()["_current_content"] = saved[4]
    if first == {"activity": (99, True)} and unchanged == {"activity": (99, False)} \
            and changed == {"activity": (99, True)} \
            and split == {"activity": (99, True), "workshop": (100, True),
                          "domains": (101, True)} \
            and states == {"board": {"message_id": 99},
                           "board-workshop": {"message_id": 100},
                           "board-domains": {"message_id": 101}} \
            and len(posts) == 3 \
            and updates == [(constants.OPERATOR_IDENTITY, 99, "two"),
                            (constants.OPERATOR_IDENTITY, 99, "activity")]:
        passed += 1
    else:
        failed += 1
        print("FAIL update_board sequence: %r %r %r split=%r states=%r posts=%r updates=%r" %
              (first, unchanged, changed, split, states, posts, updates))

    states = {
        "board": {"message_id": 99},
        "board-workshop": {"message_id": 100},
        "board-domains": {"message_id": 101},
    }
    alerts, update_attempts = [], []
    fail_activity = [True]
    saved = store.load, store.mutate, send_mod.post, send_mod.update, _current_content
    log_disabled = log.disabled
    try:
        store.load = lambda name: dict(states.get(name, {}))

        def mutate_failure_state(name, fn):
            states[name] = fn(dict(states.get(name, {})))

        def update_section(as_name, message_id, body):
            update_attempts.append(message_id)
            if message_id == 99 and fail_activity[0]:
                raise SystemExit("414")
            return message_id

        store.mutate = mutate_failure_state
        send_mod.post = lambda *args: alerts.append(args) or 200
        send_mod.update = update_section
        globals()["_current_content"] = lambda as_name, message_id: "old"
        log.disabled = True
        isolated = update_board(contents={
            "activity": "new activity", "workshop": "new workshop", "domains": "new domains"})
        repeated = update_board(contents={"activity": "new activity"})
        failed_state = dict(states["board"])
        fail_activity[0] = False
        recovered = update_board(contents={"activity": "new activity"})
    finally:
        store.load, store.mutate, send_mod.post, send_mod.update = saved[:4]
        globals()["_current_content"] = saved[4]
        log.disabled = log_disabled
    if isolated == {"activity": (99, None), "workshop": (100, True),
                    "domains": (101, True)} \
            and repeated == {"activity": (99, None)} \
            and recovered == {"activity": (99, True)} \
            and failed_state == {"message_id": 99, "failed": True} \
            and states["board"] == {"message_id": 99} and len(alerts) == 1 \
            and alerts[0][1:3] == (constants.STATUS_STREAM, constants.ALERTS_TOPIC) \
            and prompts.BOARD_UPDATE_ALERT.format(section="activity") == alerts[0][3]:
        passed += 1
    else:
        failed += 1
        print("FAIL update_board isolation: %r %r %r state=%r alerts=%r attempts=%r" %
              (isolated, repeated, recovered, states, alerts, update_attempts))

    print("monitor.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--digests", action="store_true")
    ap.add_argument("command", nargs="?", choices=("park", "unpark", "parked", "refresh"))
    ap.add_argument("channel", nargs="?")
    ap.add_argument("topic", nargs="?")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if args.digests and args.command != "refresh":
        ap.error("--digests requires refresh")
    if args.command:
        if args.json:
            ap.error("--json does not combine with commands")
        if args.command == "refresh":
            if args.digests and (args.channel is not None or args.topic is not None):
                ap.error("refresh --digests takes no channel or topic")
            if not args.digests and ((args.channel is None) != (args.topic is None)):
                ap.error("refresh takes both channel and topic, or neither")
            try:
                results = refresh_board(args.channel, args.topic, args.digests)
            except ValueError as exc:
                ap.error(str(exc))
            failed_sections = [name for name, result in results.items() if result[1] is None]
            print("refreshed board" if not failed_sections else
                  "refresh failed: %s" % ", ".join(failed_sections))
            return 1 if failed_sections else 0
        if args.command == "parked":
            if args.channel is not None or args.topic is not None:
                ap.error("parked takes no channel or topic")
            rows = parked_topics()
            print("\n".join("%s > %s" % (row["channel"], row["name"]) for row in rows)
                  or "none")
            return 0
        if args.channel is None or args.topic is None:
            ap.error("%s requires channel and topic" % args.command)
        try:
            row = set_parked(
                args.channel, args.topic, args.command == "park")
        except ValueError as exc:
            ap.error(str(exc))
        print("%s %s > %s" % (args.command, row["channel"], row["name"]))
        return 0
    data = snapshot()
    if args.json:
        print(json.dumps({"lanes": lane_rows(), "personas": data}, indent=2))
    else:
        print(render_lanes(lane_rows()))
        print()
        print(render_table(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
