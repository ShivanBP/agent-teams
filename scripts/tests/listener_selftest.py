"""Offline fixtures for listener.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import logging
    import os
    import tempfile
    import time
    from pathlib import Path

    import api
    import constants
    import digest
    import loops
    import monitor
    import personas
    import prompts
    import runner
    import send as send_mod
    import store
    import tests.cases as cases
    import todo

    passed = failed = 0
    for name in cases.LISTENER_LAZY_GLOBALS:
        if name not in globals():
            passed += 1
        else:
            failed += 1
            print("FAIL listener imported %s before runtime" % name)
    for event, expected in cases.MENTIONS:
        got = is_mention(event)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL is_mention(%r) -> %r wanted %r" % (event, got, expected))

    for sender_email, persona_emails, expected in cases.PERSONA_SENDERS:
        got = is_persona_sender(sender_email, persona_emails)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL is_persona_sender(%r, %r) -> %r wanted %r" %
                  (sender_email, persona_emails, got, expected))

    for topic, expected in cases.RESOLVED_TOPICS:
        got = is_resolved_topic(topic)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL is_resolved_topic(%r) -> %r wanted %r" % (topic, got, expected))

    dropped, parked = [], {"7:topic": 1234}
    saved_drop, saved_mutate = store.session_drop, store.mutate
    try:
        store.session_drop = dropped.append
        store.mutate = lambda name, fn: fn(parked)
        handle_topic_resolved(cases.RESOLVED_EVENT)
    finally:
        store.session_drop, store.mutate = saved_drop, saved_mutate
    if len(dropped) == len(personas.PERSONAS) and parked == {}:
        passed += 1
    else:
        failed += 1
        print("FAIL resolved topic left sessions or parking: %r %r" % (dropped, parked))

    vocabulary = {"-" + word for word in
                  set(constants.CLAUDE_MODELS) | set(constants.EFFORT_LEVELS)
                  | set(constants.PROVIDERS)}
    if set(FLAG_WORDS) == vocabulary:
        passed += 1
    else:
        failed += 1
        print("FAIL flag words %r do not derive from the configured vocabulary %r" %
              (sorted(FLAG_WORDS), sorted(vocabulary)))

    for content, expected in cases.FLAG_PARSES:
        got = parse_flags(content)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL parse_flags(%r) -> %r wanted %r" % (content, got, expected))

    for identity, flags, row, matrix, expected in cases.PROVIDER_SELECTIONS:
        got = provider_for_wake(identity, flags, row, matrix)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL provider_for_wake(%r, %r, %r) -> %r wanted %r" %
                  (identity, flags, row, got, expected))

    for identity, provider, model, effort, matrix, expected in cases.WAKE_SETTINGS:
        try:
            got = resolve_wake_settings(identity, provider, model, effort, matrix)
        except RuntimeError as exc:
            got = type(exc)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL resolve_wake_settings(%r, %r, %r, %r) -> %r wanted %r" %
                  (identity, provider, model, effort, got, expected))

    for rails, expect_exit in cases.RAIL_BOOTS:
        def lookup(rail, rails=rails):
            try:
                return dict(rails[rail])
            except KeyError:
                raise RuntimeError("rail %r is absent from the rails config" % rail)
        try:
            check_rails(lookup)
            exited = False
        except SystemExit:
            exited = True
        if exited == expect_exit:
            passed += 1
        else:
            failed += 1
            print("FAIL check_rails(%r) -> exited=%s wanted %s" % (rails, exited, expect_exit))

    for payload, fallback_channel, fallback_topic, expected in cases.LOCATION_REFETCH:
        got = _location_from_refetch(payload, fallback_channel, fallback_topic)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _location_from_refetch(%r, %r, %r) -> %r wanted %r" %
                  (payload, fallback_channel, fallback_topic, got, expected))

    for text, expected in cases.OPERATOR_DECISIONS:
        got = parse_operator_decision(text)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL parse_operator_decision(%r) -> %r wanted %r" % (text, got, expected))

    for msg_ts, now_ts, max_age, expected in cases.TAG_STALENESS:
        got = is_tag_stale(msg_ts, now_ts, max_age)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL is_tag_stale(%r, %r, %r) -> %r wanted %r" % (msg_ts, now_ts, max_age, got, expected))

    saved_resolution = (api.load, api.request, constants.AGENT_TEAM_MATE_EMAIL,
                        constants.AGENT_TEAM_MATE_EMAILS, dict(_USER_IDS))
    try:
        for singular, holder_emails, members, expected_mate, expected_holders, expected_mention in cases.USER_ID_RESOLUTIONS:
            calls = []
            constants.AGENT_TEAM_MATE_EMAIL = singular
            constants.AGENT_TEAM_MATE_EMAILS = holder_emails
            _USER_IDS.clear()
            api.load = lambda identity: identity

            def _users_request(cfg, method, path, members=members):
                calls.append((cfg, method, path))
                return {"result": "success", "members": members}

            api.request = _users_request
            got = (mate_user_id("selftest"), flag_holder_user_ids("selftest"), mate_mention())
            expected = (expected_mate, expected_holders, expected_mention)
            if got == expected and calls == [("selftest", "GET", "/api/v1/users")]:
                passed += 1
            else:
                failed += 1
                print("FAIL user id resolution -> %r calls=%r wanted %r and one GET" %
                      (got, calls, expected))
    finally:
        api.load, api.request = saved_resolution[:2]
        constants.AGENT_TEAM_MATE_EMAIL = saved_resolution[2]
        constants.AGENT_TEAM_MATE_EMAILS = saved_resolution[3]
        _USER_IDS.clear()
        _USER_IDS.update(saved_resolution[4])

    # Both rails driven for real with runner.run stubbed; the stub raises so no cost row is written.
    class _Stop(Exception):
        pass

    spawns = []
    reacts = []
    briefs = []
    failure_posts = []
    location_requests = []

    def _stub_run(persona, prompt, **kw):
        spawns.append((persona, kw.get("identity")))
        briefs.append(prompt)
        raise _Stop("529\nOverloaded")

    def _capture_failure_post(identity, channel, topic, body, **kw):
        failure_posts.append((identity, channel, topic, body, kw.get("footer", "")))

    def _location_request(cfg, method, path):
        location_requests.append((cfg, method, path))
        return {"result": "error"}

    receipts = []
    saved = (runner.run, loops.loop_for_lane, loops.budget_reached, build_delta_record, log.disabled,
             send_mod.react, send_mod.post, api.load, api.request)
    try:
        log.disabled = True
        runner.run = _stub_run
        send_mod.react = lambda *a: reacts.append(a)
        send_mod.post = _capture_failure_post
        api.load = lambda identity: identity
        api.request = _location_request
        loops.budget_reached = lambda *a, **k: False
        loops.loop_for_lane = lambda *a, **k: {"id": 1, "current_channel": None, "current_topic": None,
                                               "kicks": 0, "budget": 3, "header_id": 1, "header_text": ""}
        globals()["build_delta_record"] = lambda *a, **k: ("", None)
        handle_rail_a(1, "c", "t", "record", "reply")
        loops.loop_for_lane = lambda *a, **k: None
        # the fresh row is also rail B's spawn case; the stale row must return before both.
        for label, age_min, expected in cases.OPERATOR_TAG_RECEIPTS:
            del reacts[:]
            handle_operator_tag({"message": {"sender_id": 7, "id": 2, "stream_id": 1, "content": "go",
                                             "display_recipient": "c", "subject": "t",
                                             "timestamp": time.time() - age_min * 60}}, 7)
            receipts.append((label, list(reacts), expected))
    finally:
        runner.run, loops.loop_for_lane, loops.budget_reached = saved[:3]
        globals()["build_delta_record"] = saved[3]
        log.disabled = saved[4]
        send_mod.react, send_mod.post = saved[5:7]
        api.load, api.request = saved[7:9]

    for label, got, expected in receipts:
        want = [(constants.OPERATOR_IDENTITY, 2, constants.EMOJI_RECEIPT)] * expected
        if got == want:
            passed += 1
        else:
            failed += 1
            print("FAIL %s receipt -> %r wanted %r" % (label, got, want))

    for i, (label, persona, identity) in enumerate(cases.OPERATOR_SPAWNS):
        got = spawns[i] if i < len(spawns) else None
        if got == (persona, identity):
            passed += 1
        else:
            failed += 1
            print("FAIL %s spawn -> %r wanted %r" % (label, got, (persona, identity)))

    for label, index, substring in cases.OPERATOR_BRIEF_CONTAINS:
        got = briefs[index] if index < len(briefs) else ""
        if substring in got:
            passed += 1
        else:
            failed += 1
            print("FAIL %s brief lacks %r" % (label, substring))

    expected_failure_posts = [
        (constants.OPERATOR_IDENTITY, "c", "t",
         prompts.OPERATOR_CONTINUATION_FAILED.format(reason="529 Overloaded"), ""),
        (constants.OPERATOR_IDENTITY, "c", "t",
         prompts.OPERATOR_REPLY_FAILED.format(reason="529 Overloaded"), ""),
    ]
    if (failure_posts == expected_failure_posts and
            location_requests == [(constants.OPERATOR_IDENTITY, "GET", "/api/v1/messages/2")]):
        passed += 1
    else:
        failed += 1
        print("FAIL operator failure notices -> posts=%r requests=%r wanted posts=%r" %
              (failure_posts, location_requests, expected_failure_posts))

    notice_attempts = []
    saved_notice = (send_mod.post, api.load, api.request, log.disabled)

    def _fail_notice(*args, **kwargs):
        notice_attempts.append((args, kwargs))
        raise SystemExit("zulip unavailable")

    try:
        log.disabled = True
        send_mod.post = _fail_notice
        api.load = lambda identity: identity
        api.request = lambda *a, **k: {"result": "error"}
        _post_operator_failure(prompts.OPERATOR_REPLY_FAILED, RuntimeError("first\nsecond"),
                               "c", "t", 9)
        _post_operator_failure(prompts.OPERATOR_CONTINUATION_FAILED, SystemExit(), "c", "t")
    finally:
        send_mod.post, api.load, api.request, log.disabled = saved_notice
    if len(notice_attempts) == 2:
        passed += 1
    else:
        failed += 1
        print("FAIL operator failure notice send escaped or retried: %r" % (notice_attempts,))

    class _LogCapture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.messages = []

        def emit(self, record):
            self.messages.append(record.getMessage())

    for index, (label, sender_id, holder_ids, content, expected_flags, log_substring) in enumerate(
            cases.FLAG_HOLDER_WAKES):
        selected_flags = []
        capture = _LogCapture()
        old_level = log.level
        saved_wake = (runner.run, runner.wake_cwd, send_mod.react, send_mod.post,
                      build_delta_record, provider_for_wake)

        def _capture_provider(identity, flags, row):
            selected_flags.append(list(flags))
            return "claude"

        try:
            log.setLevel(logging.INFO)
            log.addHandler(capture)
            runner.run = _stub_run
            runner.wake_cwd = lambda *a, **k: (None, "")
            send_mod.react = lambda *a, **k: None
            send_mod.post = lambda *a, **k: None
            globals()["build_delta_record"] = lambda *a, **k: ("", None)
            globals()["provider_for_wake"] = _capture_provider
            handle_wake("bob", {"message": {"stream_id": "selftest-holder-%d" % index,
                                              "subject": label, "display_recipient": "c",
                                              "content": content, "sender_id": sender_id,
                                              "id": 20 + index}}, holder_ids)
        finally:
            log.removeHandler(capture)
            log.setLevel(old_level)
            runner.run, runner.wake_cwd = saved_wake[:2]
            send_mod.react, send_mod.post = saved_wake[2:4]
            globals()["build_delta_record"] = saved_wake[4]
            globals()["provider_for_wake"] = saved_wake[5]
        logged = log_substring is None or any(log_substring in message for message in capture.messages)
        if selected_flags == [expected_flags] and logged:
            passed += 1
        else:
            failed += 1
            print("FAIL %s selected flags %r logs=%r wanted %r log=%r" %
                  (label, selected_flags, capture.messages, expected_flags, log_substring))

    # The wake-failure path driven for real, everything outward stubbed: the run raises, and the
    # lane's dead session must be gone before any retry can resume it.
    for lane, session, error, expected in cases.WAKE_SESSION_CLEARED_ON_FAILURE:
        stream_id, topic, identity = lane.split(":")
        store.session_set(lane, *session)
        run_lanes = []

        def _raise_run(*a, **k):
            run_lanes.append(k.get("lane"))
            raise error

        saved_wake = (runner.run, runner.wake_cwd, send_mod.react, send_mod.post,
                      build_delta_record, log.disabled)
        try:
            log.disabled = True
            runner.run = _raise_run
            runner.wake_cwd = lambda *a, **k: (None, "")
            send_mod.react = lambda *a, **k: None
            send_mod.post = lambda *a, **k: None
            globals()["build_delta_record"] = lambda *a, **k: ("", None)
            handle_wake(identity, {"message": {"stream_id": stream_id, "subject": topic,
                                               "display_recipient": "c", "content": "go",
                                               "sender_id": 7, "id": 3}}, frozenset())
        finally:
            runner.run, runner.wake_cwd = saved_wake[0], saved_wake[1]
            send_mod.react, send_mod.post = saved_wake[2], saved_wake[3]
            globals()["build_delta_record"] = saved_wake[4]
            log.disabled = saved_wake[5]
        got = store.session_get(lane)
        store.session_drop(lane)
        if got == expected and run_lanes == [lane]:
            passed += 1
        else:
            failed += 1
            print("FAIL wake failure on lane %s left session %r, passed lanes %r wanted %r, [%r]" %
                  (lane, got, run_lanes, expected, lane))

    with tempfile.TemporaryDirectory() as root:
        wake_log = Path(root) / "wake.jsonl"
        wake_log.write_text("event\n")
        old_path = runner._wake_log_path
        old_case_log_disabled = log.disabled
        try:
            log.disabled = True
            runner._wake_log_path = lambda lane: wake_log
            for mtime, holder_result, socket_results, expected in cases.STALLED_WAKE_CHECKS:
                os.utime(wake_log, (mtime, mtime))
                calls = []

                class _Result:
                    def __init__(self, returncode, stdout):
                        self.returncode = returncode
                        self.stdout = stdout
                        self.stderr = ""

                def _run(cmd, **kwargs):
                    calls.append(cmd)
                    if "-iTCP" not in cmd:
                        return _Result(*holder_result)
                    return _Result(*socket_results[int(cmd[cmd.index("-p") + 1])])

                got = stalled_wake("lane", 1000, run=_run, own_pid=lambda: 100)
                if got is not None:
                    got["wake_log"] = Path(got["wake_log"]).name
                if got == expected:
                    passed += 1
                else:
                    failed += 1
                    print("FAIL stalled_wake mtime=%r calls=%r -> %r wanted %r" %
                          (mtime, calls, got, expected))
        finally:
            runner._wake_log_path = old_path
            log.disabled = old_case_log_disabled

    board_updates = []
    sweep_events = []
    inflight = {
        "lane-a": {"stream_id": 1, "topic": "one"},
        "lane-b": {"stream_id": 2, "topic": "two"},
    }
    old_inflight, old_update, old_stalled = store.inflight_all, monitor.update_board, stalled_wake
    old_post, old_loop, old_mutate = send_mod.post, loops.loop_for_lane, store.mutate
    old_log_disabled = log.disabled
    try:
        store.inflight_all = lambda: inflight
        monitor.update_board = lambda: board_updates.append(True)
        globals()["stalled_wake"] = lambda lane, now: (
            sweep_events.append("check:" + lane) or
            ({"pid": 12, "quiet_s": 601, "wake_log": "/tmp/wake"}
             if lane == "lane-a" else None))
        loops.loop_for_lane = lambda *a: None
        send_mod.post = lambda *a: sweep_events.append("post:" + a[3])
        store.mutate = lambda name, fn: fn(inflight)
        stall_sweep_once(now_ts=1000)

        retry_inflight = {"lane-c": {"stream_id": 3, "topic": "three"}}
        store.inflight_all = lambda: retry_inflight
        globals()["stalled_wake"] = lambda lane, now: {
            "pid": 13, "quiet_s": 700, "wake_log": "/tmp/retry"}

        def _fail_post(*args):
            sweep_events.append("post-failed")
            raise RuntimeError("offline")

        log.disabled = True
        send_mod.post = _fail_post
        stall_sweep_once(now_ts=1000)
    finally:
        store.inflight_all, monitor.update_board = old_inflight, old_update
        globals()["stalled_wake"] = old_stalled
        send_mod.post, loops.loop_for_lane, store.mutate = old_post, old_loop, old_mutate
        log.disabled = old_log_disabled
    expected_alert = prompts.STALLED_WAKE_ALERT.format(
        lane="lane-a", pid=12, quiet_min=10, wake_log="/tmp/wake")
    if (board_updates == [True, True]
            and sweep_events == ["check:lane-a", "check:lane-b", "post:" + expected_alert,
                                 "post-failed"]
            and inflight["lane-a"].get("alerted") is True
            and "alerted" not in inflight["lane-b"]
            and "alerted" not in retry_inflight["lane-c"]):
        passed += 1
    else:
        failed += 1
        print("FAIL stall_sweep_once order=%r board=%r inflight=%r" %
              (sweep_events, board_updates, inflight))

    sweep_threads = []

    class _Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            sweep_threads.append(self.kwargs)

    start_sweep_threads(_Thread)
    got_threads = [(row["name"], row["target"], row["daemon"]) for row in sweep_threads]
    if got_threads == [("stall-sweep", stall_sweep_thread, True),
                       ("todo-sweep", todo.sweep_thread, True)]:
        passed += 1
    else:
        failed += 1
        print("FAIL sweep thread startup -> %r" % (got_threads,))

    digest_calls = []
    old_stream_id, old_refresh = api.stream_id, digest.refresh_topic
    try:
        api.stream_id = lambda identity, channel: 7
        digest.refresh_topic = lambda *args: digest_calls.append(args) or "ok"
        got_digest = refresh_topic_digest("bob", "setup", "topic")
    finally:
        api.stream_id, digest.refresh_topic = old_stream_id, old_refresh
    if got_digest == "ok" and digest_calls == [("bob", 7, "setup", "topic")]:
        passed += 1
    else:
        failed += 1
        print("FAIL post-wake digest trigger -> %r calls=%r" % (got_digest, digest_calls))

    print("listener.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
