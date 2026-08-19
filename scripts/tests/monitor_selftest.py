"""Offline fixtures for monitor.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import json
    import pathlib
    import tempfile

    import api
    import constants
    import prompts
    import send as send_mod
    import store
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
    if table.startswith("Persona") and len(table.splitlines()) == len(got) + 1:
        passed += 1
    else:
        failed += 1
        print("FAIL rendered table is empty or incomplete")
    activity = render_activity(got)
    if activity.startswith("## Activity today\n\n| Persona |") \
            and len(activity.splitlines()) == len(got) + 4:
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
    if "STUCK" in lane_table and lane_table.count("maat") == 2 and "-" in lane_table:
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
            and sweeps == [constants.BRIDGE_IDENTITY] \
            and board_refreshes == [{"as_name": constants.BRIDGE_IDENTITY}] * 3 \
            and single_refresh == digest_refresh == board_refresh == {"activity": (99, False)}:
        passed += 1
    else:
        failed += 1
        print("FAIL refresh_board modes -> %r %r %r" %
              (refreshes, sweeps, board_refreshes))

    states, current, boards = {}, {}, []
    saved = store.load, store.mutate, send_mod.board_message
    try:
        store.load = lambda name: dict(states.get(name, {}))

        def mutate_state(name, fn):
            states[name] = fn(dict(states.get(name, {})))

        def board_stub(as_name, channel, topic, body, message_id=None):
            boards.append((as_name, channel, topic, body, message_id))
            if message_id is None:
                message_id = 98 + len([row for row in boards if row[4] is None])
                current[message_id] = body
                return message_id, True
            if current.get(message_id) == body:
                return message_id, False
            current[message_id] = body
            return message_id, True

        store.mutate = mutate_state
        send_mod.board_message = board_stub
        first = update_board(content="one")
        unchanged = update_board(content="one")
        changed = update_board(content="two")
        split = update_board(contents={
            "activity": "activity", "workshop": "workshop", "domains": "domains"})
    finally:
        store.load, store.mutate, send_mod.board_message = saved
    if first == {"activity": (99, True)} and unchanged == {"activity": (99, False)} \
            and changed == {"activity": (99, True)} \
            and split == {"activity": (99, True), "workshop": (100, True),
                          "domains": (101, True)} \
            and states == {"board": {"message_id": 99},
                           "board-workshop": {"message_id": 100},
                           "board-domains": {"message_id": 101}} \
            and [row[2] for row in boards] == [constants.BOARD_TOPIC] * 6 \
            and [row[4] for row in boards] == [None, 99, 99, 99, None, None]:
        passed += 1
    else:
        failed += 1
        print("FAIL update_board sequence: %r %r %r split=%r states=%r boards=%r" %
              (first, unchanged, changed, split, states, boards))

    states = {
        "board": {"message_id": 99},
        "board-workshop": {"message_id": 100},
        "board-domains": {"message_id": 101},
    }
    alerts, update_attempts = [], []
    fail_activity = [True]
    saved = store.load, store.mutate, send_mod.post, send_mod.board_message
    log_disabled = log.disabled
    try:
        store.load = lambda name: dict(states.get(name, {}))

        def mutate_failure_state(name, fn):
            states[name] = fn(dict(states.get(name, {})))

        def board_section(as_name, channel, topic, body, message_id=None):
            update_attempts.append(message_id)
            if message_id == 99 and fail_activity[0]:
                raise SystemExit("414")
            return message_id, True

        store.mutate = mutate_failure_state
        send_mod.post = lambda *args: alerts.append(args) or 200
        send_mod.board_message = board_section
        log.disabled = True
        isolated = update_board(contents={
            "activity": "new activity", "workshop": "new workshop", "domains": "new domains"})
        repeated = update_board(contents={"activity": "new activity"})
        failed_state = dict(states["board"])
        fail_activity[0] = False
        recovered = update_board(contents={"activity": "new activity"})
    finally:
        store.load, store.mutate, send_mod.post, send_mod.board_message = saved
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

    # domain_board: the id lives in the domain repo, so each row gets its own throwaway root.
    for label, channel, root, body, window, state, refusal in cases.DOMAIN_BOARDS:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / constants.DOMAIN_BOARD_STATE
            if state is not None:
                path.write_text(state if isinstance(state, str) else json.dumps(state))
            sent = []

            def board_stub(as_name, channel, topic, body, message_id=None):
                sent.append((as_name, channel, topic, body, message_id))
                return (message_id or 55), True

            log_disabled = log.disabled
            try:
                log.disabled = True
                got = domain_board(channel, body, root=(tmp if root else ""),
                                   window_fn=lambda name: window, board_fn=board_stub)
                error = None
            except ValueError as exc:
                got, error = None, str(exc)
            finally:
                log.disabled = log_disabled
            written = json.loads(path.read_text()) if path.is_file() else None
            if refusal:
                ok = error is not None and refusal in error and not sent
            else:
                prior = state.get("message_id") if isinstance(state, dict) else None
                ok = (error is None and got == ((prior or 55), True)
                      and len(sent) == 1
                      and sent[0][2] == constants.DOMAIN_BOARD_TOPIC.format(channel=channel)
                      and sent[0][4] == prior
                      and written == {"message_id": prior or 55})
            if ok:
                passed += 1
            else:
                failed += 1
                print("FAIL domain_board %s -> %r error=%r sent=%r written=%r" %
                      (label, got, error, sent, written))

    print("monitor.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
