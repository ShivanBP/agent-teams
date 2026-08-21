"""Offline fixtures for prompts.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import os
    import time

    import tests.cases as cases

    passed = failed = 0
    for substring in cases.WAKE_HEADER_CONTAINS:
        if substring in WAKE_HEADER:
            passed += 1
        else:
            failed += 1
            print("FAIL WAKE_HEADER missing %r" % substring)

    for template, values, expected in [
        (PROGRESS_LINE, {"age": "5m", "said": "testing"}, "Working, 5m: testing"),
        (PROGRESS_DONE, {"age": "12m"}, "Done, 12m."),
    ]:
        got = template.format(**values)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL progress prompt %r -> %r wanted %r" % (template, got, expected))

    for attr, substring in cases.PROMPT_CONTAINS:
        if substring in globals()[attr]:
            passed += 1
        else:
            failed += 1
            print("FAIL %s missing %r" % (attr, substring))

    for body, record, notice, domain, kill_at, expected in cases.WAKE_PROMPTS:
        got = wake_prompt(body, record, notice, domain, kill_at)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL wake_prompt(%r, %r, %r, %r, %r) -> %r wanted %r" %
                  (body, record, notice, domain, kill_at, got, expected))

    # Read in UTC: the rows name a wall clock, and the runner's timeout is wall time.
    saved_tz = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        for started, timeout, expected in cases.KILL_CLOCKS:
            got = kill_clock(started, timeout)
            if got == expected:
                passed += 1
            else:
                failed += 1
                print("FAIL kill_clock(%r, %r) -> %r wanted %r" %
                      (started, timeout, got, expected))
    finally:
        if saved_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = saved_tz
        time.tzset()

    for body, notice, expected in cases.NOTICED_REPLIES:
        got = with_notice(body, notice)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL with_notice(%r, %r) -> %r wanted %r" %
                  (body, notice, got, expected))

    for args, required, forbidden in cases.PROVIDER_PROMPTS:
        got = provider_prompt(*args)
        if all(part in got for part in required) and all(part not in got for part in forbidden):
            passed += 1
        else:
            failed += 1
            print("FAIL provider_prompt%r -> %r" % (args, got))

    for args, expected in cases.MEMORY_FRAMES:
        got = memory_frame(*args)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL memory_frame%r -> %r wanted %r" % (args, got, expected))

    for memory, wake, expected in cases.WITH_MEMORY_FRAMES:
        got = with_memory_frame(memory, wake)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL with_memory_frame(%r, %r) -> %r wanted %r" %
                  (memory, wake, got, expected))

    for args, expected in cases.WAKE_FOOTERS:
        got = wake_footer(*args)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL wake_footer%r -> %r wanted %r" % (args, got, expected))

    for text, expected in cases.OPEN_FENCES:
        got = open_fence(text)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL open_fence(%r) -> %r wanted %r" % (text, got, expected))

    for summary, required in cases.STATE_BLOCKS:
        got = state_block(summary)
        if all(part in got for part in required):
            passed += 1
        else:
            failed += 1
            print("FAIL state_block(%r) -> %r" % (summary, got))

    print("prompts.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
