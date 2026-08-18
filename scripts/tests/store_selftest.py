"""Offline fixtures for store.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import tests.cases as cases

    passed = failed = 0

    for stream_id, topic, persona, expected in cases.LANE_KEYS:
        got = lane_key(stream_id, topic, persona)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL lane_key(%r, %r, %r) -> %r wanted %r" % (stream_id, topic, persona, got, expected))

    probe = "selftest-probe"
    probe_path = _path(probe)
    if probe_path.is_file():
        probe_path.unlink()
    try:
        mutate(probe, lambda d: d.__setitem__("n", 0))
        for _ in range(5):
            mutate(probe, lambda d: d.__setitem__("n", d.get("n", 0) + 1))
        got = load(probe).get("n")
        if got == 5:
            passed += 1
        else:
            failed += 1
            print("FAIL mutate() sequential increments -> %r wanted 5" % got)
    finally:
        if probe_path.is_file():
            probe_path.unlink()
        lock = _lock_path(probe)
        if lock.is_file():
            lock.unlink()

    lane = lane_key("selftest", "probe topic", "peter")
    try:
        session_set(lane, "sid-1", 42, "codex")
        row = session_get(lane)
        if row == {"session_id": "sid-1", "record_anchor": 42, "provider": "codex"}:
            passed += 1
        else:
            failed += 1
            print("FAIL session_set/get -> %r" % row)

        checks = [
            (session_for_provider(row, "codex"), "sid-1"),
            (session_for_provider(row, "claude"), None),
            (session_provider({"session_id": "legacy"}), "claude"),
            (session_for_provider({"session_id": "legacy"}, "claude"), "legacy"),
        ]
        for got, expected in checks:
            if got == expected:
                passed += 1
            else:
                failed += 1
                print("FAIL session provider helper -> %r wanted %r" % (got, expected))
    finally:
        session_drop(lane)
    if session_get(lane) is None:
        passed += 1
    else:
        failed += 1
        print("FAIL session_drop left a row")

    try:
        inflight_add(lane, {"persona": "peter"})
        if lane in inflight_all():
            passed += 1
        else:
            failed += 1
            print("FAIL inflight_add did not register lane")
    finally:
        inflight_clear(lane)
    if lane not in inflight_all():
        passed += 1
    else:
        failed += 1
        print("FAIL inflight_clear left a row")

    # state_summary against probe ledgers; the live loops/inflight/cost/kicks files are never
    # named here, so a selftest run cannot disturb what the operator rails read.
    probe_names = [cases.STATE_PROBE_LOOPS, cases.STATE_PROBE_INFLIGHT,
                   "cost-%s" % cases.STATE_PROBE_DAY, "kicks-%s" % cases.STATE_PROBE_DAY]
    for fixture, now, expected in cases.STATE_SUMMARIES:
        try:
            mutate(cases.STATE_PROBE_LOOPS, lambda d: fixture["loops"])
            mutate(cases.STATE_PROBE_INFLIGHT, lambda d: fixture["inflight"])
            mutate("cost-%s" % cases.STATE_PROBE_DAY, lambda d: {"rows": fixture["cost"]})
            mutate("kicks-%s" % cases.STATE_PROBE_DAY, lambda d: {"rows": fixture["kicks"]})
            got = state_summary(now=now, day=cases.STATE_PROBE_DAY,
                                loops_name=cases.STATE_PROBE_LOOPS,
                                inflight_name=cases.STATE_PROBE_INFLIGHT)
        finally:
            for name in probe_names:
                for path in (_path(name), _lock_path(name)):
                    if path.is_file():
                        path.unlink()
        if got.get("day") != cases.STATE_PROBE_DAY:
            failed += 1
            print("FAIL state_summary day -> %r" % got.get("day"))
        else:
            passed += 1
        for key, want in expected.items():
            if got.get(key) == want:
                passed += 1
            else:
                failed += 1
                print("FAIL state_summary[%r] -> %r wanted %r" % (key, got.get(key), want))

    # the wall clock itself, shape only: the rendered hour is timezone-dependent, the fallback is not.
    stamp = _clock(1000.0)
    if len(stamp) == 5 and stamp[2] == ":" and _clock(None) == "-":
        passed += 1
    else:
        failed += 1
        print("FAIL _clock -> %r and %r" % (stamp, _clock(None)))

    print("store.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
