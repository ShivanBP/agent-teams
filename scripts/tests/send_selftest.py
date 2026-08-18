"""Offline fixtures for send.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
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
        got = strip_persona_mentions(text, as_name)
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
    for size, limit, should_refuse in cases.WINDOW:
        refused = size > limit
        if refused == should_refuse:
            passed += 1
        else:
            failed += 1
            print("FAIL window boundary size=%d limit=%d refused=%s" % (size, limit, refused))
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
    old_ready, old_window, old_request, old_check = _ready, api.window, api.request, api.check
    calls = []
    try:
        globals()["_ready"] = lambda as_name: {"name": as_name}
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
    print("send.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
