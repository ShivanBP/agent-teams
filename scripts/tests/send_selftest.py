"""Offline fixtures for send.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import contextlib
    import io

    import api
    import tests.cases as cases

    passed = failed = 0
    for text, expected in cases.WILDCARDS:
        got = strip_wildcards(text)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL strip_wildcards(%r) -> %r, wanted %r" % (text, got, expected))
    for as_name, text, expected in cases.PERSONA_MENTIONS:
        got = strip_persona_mentions(text, as_name, cases.PERSONA_MENTION_NAMES)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL strip_persona_mentions(%r, %r) -> %r, wanted %r" %
                  (text, as_name, got, expected))
    for path, index, exists, is_dir, size, is_symlink, expected in cases.ATTACHES:
        got = classify_attach(path, index, exists, is_dir, size, is_symlink)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL classify_attach(%r, %d) -> %r, wanted %r" % (path, index, got, expected))
    old_window = api.window
    try:
        for size, limit, should_refuse in cases.WINDOW:
            api.window = lambda as_name, value=limit: value
            stderr = io.StringIO()
            try:
                with contextlib.redirect_stderr(stderr):
                    guarded = _strip_and_guard("x" * size, "bridge")
                code = None
            except SystemExit as exc:
                guarded, code = None, exc.code
            refused = code == 3
            note = stderr.getvalue()
            ok = refused == should_refuse
            ok = ok and (refused or guarded == "x" * size)
            ok = ok and (not refused or str(size) in note and str(limit) in note)
            if ok:
                passed += 1
            else:
                failed += 1
                print("FAIL window guard size=%d limit=%d code=%r body=%r note=%r" %
                      (size, limit, code, guarded, note))
    finally:
        api.window = old_window
    for body, expect_body, expect_accepted in cases.EXTRACTS:
        got_body, got_accepted = _extract(body, [])
        if got_body == expect_body and got_accepted == expect_accepted:
            passed += 1
        else:
            failed += 1
            print("FAIL _extract(%r) -> (%r, %r)" % (body, got_body, got_accepted))
    for body, footer, expected in cases.FOOTER_BODIES:
        got = with_footer(body, footer)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL with_footer(%r, %r) -> %r, wanted %r" % (body, footer, got, expected))
    for as_name, expected in cases.VERB_GATE:
        got = verb_allowed(as_name)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL verb_allowed(%r) -> %r, wanted %r" % (as_name, got, expected))
    for channel, expected in cases.STATUS_CHANNELS:
        got = status_channel(channel)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL status_channel(%r) -> %r, wanted %r" % (channel, got, expected))
    # The guard has to refuse before any API call, so the row drives the verb, not the predicate.
    for verb, channel, should_refuse in cases.STATUS_TOPIC_GUARD:
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stderr(stderr):
                _refuse_status_topic(verb, channel, "a topic")
            code = None
        except SystemExit as exc:
            code = exc.code
        refused = code == 2
        if refused == should_refuse and (not refused or channel in stderr.getvalue()):
            passed += 1
        else:
            failed += 1
            print("FAIL _refuse_status_topic(%r, %r) code=%r note=%r"
                  % (verb, channel, code, stderr.getvalue()))
    import constants
    saved_identity = constants.BRIDGE_IDENTITY
    try:
        for identity, as_name, expected in cases.VERB_GATE_RENAMED:
            constants.BRIDGE_IDENTITY = identity
            got = verb_allowed(as_name)
            if got == expected:
                passed += 1
            else:
                failed += 1
                print("FAIL verb_allowed(%r) under BRIDGE_IDENTITY=%r -> %r wanted %r"
                      % (as_name, identity, got, expected))
    finally:
        constants.BRIDGE_IDENTITY = saved_identity
    old_ready, old_window, old_request, old_check = _ready, api.window, api.request, api.check
    calls = []
    try:
        globals()["_ready"] = lambda as_name, enforce=True: {"name": as_name}
        api.window = lambda as_name: 10000
        api.request = lambda cfg, method, path, params: calls.append(
            (cfg, method, path, params)) or {"result": "success"}
        api.check = lambda payload, what: payload
        for as_name, message_id, content, expected in cases.UPDATES:
            got = update(as_name, message_id, content)
            call = calls.pop(0)
            wanted = ({"name": as_name}, "PATCH", "/api/v1/messages/%d" % message_id,
                      {"content": expected})
            if got == message_id and call == wanted:
                passed += 1
            else:
                failed += 1
                print("FAIL update(%r, %r, %r) -> %r call %r, wanted %r" %
                      (as_name, message_id, content, got, call, wanted))
    finally:
        globals()["_ready"] = old_ready
        api.window, api.request, api.check = old_window, old_request, old_check

    old_ready, old_request, old_check = _ready, api.request, api.check
    calls = []
    try:
        globals()["_ready"] = lambda as_name: {"name": as_name}
        api.request = lambda cfg, method, path: calls.append((cfg, method, path)) or {
            "result": "success"}
        api.check = lambda payload, what: payload
        for as_name, message_id in cases.DELETES:
            got = delete(as_name, message_id)
            call = calls.pop(0)
            wanted = ({"name": as_name}, "DELETE", "/api/v1/messages/%d" % message_id)
            if got == message_id and call == wanted:
                passed += 1
            else:
                failed += 1
                print("FAIL delete(%r, %r) -> %r call %r, wanted %r" %
                      (as_name, message_id, got, call, wanted))
    finally:
        globals()["_ready"] = old_ready
        api.request, api.check = old_request, old_check

    # board_message drives the real post/update doors, so a row proves which one was spent.
    old_post, old_update, old_current = post, update, current_content
    for label, message_id, live, body, expected, expected_call in cases.BOARD_MESSAGES:
        doors = []
        saved_window = api.window
        try:
            api.window = lambda name: 10000
            globals()["post"] = lambda a, c, t, b, enforce=True: doors.append(
                ("post", b, enforce)) or 99
            globals()["update"] = lambda a, mid, b, enforce=True: doors.append(
                ("update", b, enforce)) or int(mid)
            globals()["current_content"] = lambda a, mid, live=live: live.get(int(mid))
            got = board_message("board-bot", "status", "a board", body, message_id)
        finally:
            globals()["post"], globals()["update"] = old_post, old_update
            globals()["current_content"] = old_current
            api.window = saved_window
        want_doors = [] if expected_call is None else [expected_call + (False,)]
        if got == expected and doors == want_doors:
            passed += 1
        else:
            failed += 1
            print("FAIL board_message %s -> %r doors %r, wanted %r doors %r" %
                  (label, got, doors, expected, want_doors))

    print("send.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
