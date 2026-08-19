"""One-shot terminal snapshot of persona activity, cost, and loop kicks."""

import argparse
import datetime
import difflib
import json
import logging
import pathlib
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
    for name in defaults:
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
    for name, row in data.items():
        status = _running_status(row)
        rows.append((name, row["provider"], status,
                     prompts.BOARD_COST.format(usd=row["cost_today"]), str(row["kicks_today"])))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)).rstrip()
                     for row in rows)


def render_activity(data):
    rows = []
    for name, row in data.items():
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


def unresolved_topics(as_name=constants.BRIDGE_IDENTITY, stream_id_fn=None,
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


def parked_topics(as_name=constants.BRIDGE_IDENTITY, topics=None,
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


def set_parked(channel, topic, parked, as_name=constants.BRIDGE_IDENTITY,
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
    board_channels = constants.board_channels()
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
                 as_name=constants.BRIDGE_IDENTITY, now_ts=None, parked=None):
    return "\n\n".join(content for _, content in _board_sections(
        lanes, persona_rows, todos, digests, as_name, now_ts, parked))


def _board_sections(lanes=None, persona_rows=None, todos=None, digests=None,
                    as_name=constants.BRIDGE_IDENTITY, now_ts=None, parked=None):
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
                as_name=constants.BRIDGE_IDENTITY, now_ts=None, force_split=False,
                parked=None):
    sections = _board_sections(lanes, persona_rows, todos, digests, as_name, now_ts, parked)
    combined = "\n\n".join(content for _, content in sections)
    if not force_split and len(combined) <= limit:
        return {"activity": combined}
    return dict(sections)


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


def update_board(as_name=constants.BRIDGE_IDENTITY, content=None, contents=None):
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
            new_id, changed = send_mod.board_message(
                as_name, constants.STATUS_STREAM, constants.BOARD_TOPIC, body, message_id)
            _board_working(state_name, new_id if message_id is None else None)
            results[name] = (new_id, changed)
        except (Exception, SystemExit):
            log.exception("board section %s failed to update", name)
            results[name] = (message_id, None)
            try:
                _board_failed(as_name, state_name, name)
            except (Exception, SystemExit):
                log.exception("board section %s failure state could not be saved", name)
    return results


def domain_board(channel, body, root=None, as_name=constants.BRIDGE_IDENTITY,
                 window_fn=None, board_fn=None):
    """The board of a mapped channel's domain: one message in the status channel, edited in
    place forever. Its id lives in the domain repo, not in fleet state, because the domain owns
    what its board says and should carry the pointer with it."""
    root = constants.domain_root(channel) if root is None else root
    if not root:
        raise ValueError(prompts.DOMAIN_BOARD_UNMAPPED.format(channel=channel))
    window = (window_fn or api.window)(as_name)
    if len(body) > window:
        raise ValueError(prompts.DOMAIN_BOARD_TOO_LONG.format(size=len(body), window=window))
    path = pathlib.Path(root) / constants.DOMAIN_BOARD_STATE
    state = {}
    if path.is_file():
        try:
            state = json.loads(path.read_text())
        except ValueError:
            log.warning("board state at %s is malformed; posting a fresh board", path)
    message_id, changed = (board_fn or send_mod.board_message)(
        as_name, constants.STATUS_STREAM,
        constants.DOMAIN_BOARD_TOPIC.format(channel=channel), body,
        state.get("message_id"))
    if state.get("message_id") != message_id:
        state["message_id"] = message_id
        path.write_text(json.dumps(state, indent=2) + "\n")
    return message_id, changed


def refresh_board(channel=None, topic=None, digests=False,
                  as_name=constants.BRIDGE_IDENTITY, topics=None,
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
    from tests import monitor_selftest
    return monitor_selftest.run(sys.modules[__name__])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--digests", action="store_true")
    ap.add_argument("command", nargs="?",
                    choices=("park", "unpark", "parked", "refresh", "board"))
    ap.add_argument("channel", nargs="?")
    ap.add_argument("topic", nargs="?")
    ap.add_argument("--body-file", help="board: the rendered body to put in the domain's board")
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
        if args.command == "board":
            if args.channel is None or args.topic is not None:
                ap.error("board takes a channel and nothing else")
            if not args.body_file:
                ap.error("board requires --body-file")
            try:
                message_id, changed = domain_board(
                    args.channel, pathlib.Path(args.body_file).read_text())
            except (ValueError, OSError) as exc:
                ap.error(str(exc))
            print("%d %s" % (message_id, "updated" if changed else "unchanged"))
            return 0
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
