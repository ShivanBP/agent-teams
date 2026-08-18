"""One-shot terminal snapshot of persona activity, cost, and loop kicks."""

import argparse
import datetime
import json
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


def render_table(data):
    rows = [prompts.BOARD_PERSONA_HEADERS]
    for name in personas.PERSONAS:
        row = data[name]
        status = row["status"]
        if status == prompts.BOARD_RUNNING and row["topic"]:
            status = prompts.BOARD_RUNNING_TOPIC.format(topic=row["topic"])
        rows.append((name, row["provider"], status,
                     prompts.BOARD_COST.format(usd=row["cost_today"]), str(row["kicks_today"])))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)).rstrip()
                     for row in rows)


def render_activity(data):
    rows = []
    for name in personas.PERSONAS:
        row = data[name]
        status = row["status"]
        if status == prompts.BOARD_RUNNING and row["topic"]:
            status = prompts.BOARD_RUNNING_TOPIC.format(topic=row["topic"])
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


def render_board(lanes=None, persona_rows=None, todos=None, digests=None,
                 as_name=constants.OPERATOR_IDENTITY, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    lanes = lane_rows() if lanes is None else lanes
    persona_rows = snapshot() if persona_rows is None else persona_rows
    if todos is None:
        todos = merge_todos(_loop_todos(as_name), _topic_todos(as_name))
    digests = store.load("digests") if digests is None else digests
    lane_map = {}
    for lane in lanes:
        key = (lane.get("stream_id"), store.normalize_topic(lane.get("topic")))
        lane_map.setdefault(key, []).append(lane)
    topic_map = {(row.get("channel"), store.normalize_topic(row.get("name"))): row for row in todos}
    sections = [render_activity(persona_rows)]
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
                cached = digests.get(digest.digest_key(*key), {}) if key[0] is not None else {}
                group_lines.append(_render_topic(
                    topic, lane_map.get(key, []), cached,
                    _show_digest_items(topic, now_ts)))
        if group_lines:
            sections.append(prompts.BOARD_GROUP_HEADING.format(group=group) + "\n" +
                            "\n".join(group_lines))
    return "\n\n".join(sections)


def _current_content(as_name, message_id):
    message = _message(as_name, message_id)
    return message.get("content") if message else None


def update_board(as_name=constants.OPERATOR_IDENTITY, content=None):
    content = render_board(as_name=as_name) if content is None else content
    state = store.load("board")
    message_id = state.get("message_id")
    if message_id is None:
        message_id = send_mod.post(
            as_name, constants.STATUS_STREAM, constants.BOARD_TOPIC, content)
        store.mutate("board", lambda data: {"message_id": message_id})
        return message_id, True
    if _current_content(as_name, message_id) == content:
        return message_id, False
    send_mod.update(as_name, message_id, content)
    return message_id, True


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

    state, posts, updates = {}, [], []
    saved = store.load, store.mutate, send_mod.post, send_mod.update, _current_content
    try:
        store.load = lambda name: dict(state)
        store.mutate = lambda name, fn: state.update(fn(dict(state)))
        send_mod.post = lambda *args: posts.append(args) or 99
        send_mod.update = lambda *args: updates.append(args) or args[1]
        globals()["_current_content"] = lambda as_name, message_id: state.get("content")
        first = update_board(content="one")
        state["content"] = "one"
        unchanged = update_board(content="one")
        changed = update_board(content="two")
    finally:
        store.load, store.mutate, send_mod.post, send_mod.update = saved[:4]
        globals()["_current_content"] = saved[4]
    if first == (99, True) and unchanged == (99, False) and changed == (99, True) \
            and len(posts) == 1 and updates == [(constants.OPERATOR_IDENTITY, 99, "two")]:
        passed += 1
    else:
        failed += 1
        print("FAIL update_board post/edit/skip sequence: %r %r %r posts=%r updates=%r" %
              (first, unchanged, changed, posts, updates))

    print("monitor.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
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
