"""Offline fixtures for loops.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import store
    import tests.cases as cases

    passed = failed = 0

    for payload, expected in cases.WITH_NARROW:
        got = _extract_location(payload)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _extract_location(%r) -> %r wanted %r" % (payload, got, expected))

    for kicks, budget, expected in cases.BUDGET_REACHED:
        got = _budget_reached(kicks, budget)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _budget_reached(%r, %r) -> %r wanted %r" % (kicks, budget, got, expected))

    for header_id, expected in cases.WITH_NARROW_PARAMS:
        got = _with_narrow_params(header_id)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _with_narrow_params(%r) -> %r wanted %r" % (header_id, got, expected))

    # Local round trip against an isolated state name; no network, cleaned up after. kick_record
    # also writes the real daily kick ledger (store.kick_append is not state-scoped), so those
    # test rows are swept out of it in the finally block.
    test_state = "loops-selftest"
    test_path = store._path(test_state)
    test_lock = store._lock_path(test_state)
    if test_path.is_file():
        test_path.unlink()
    test_loop_ids = []
    try:
        row = open_loop("workshop", "phase 3 selftest", 123456, budget=2, state_name=test_state)
        test_loop_ids.append(row["id"])
        if row["status"] == STATUS_OPEN and row["kicks"] == 0 and row["budget"] == 2:
            passed += 1
        else:
            failed += 1
            print("FAIL open_loop initial row: %r" % row)

        if not budget_reached(row["id"], state_name=test_state):
            passed += 1
        else:
            failed += 1
            print("FAIL budget_reached true on a fresh loop")

        n = kick_record(row["id"], state_name=test_state)
        if n == 1 and get(row["id"], test_state)["kicks"] == 1:
            passed += 1
        else:
            failed += 1
            print("FAIL kick_record first call -> %r" % n)

        n = kick_record(row["id"], state_name=test_state)
        if n == 2 and budget_reached(row["id"], state_name=test_state):
            passed += 1
        else:
            failed += 1
            print("FAIL kick_record second call -> %r, budget_reached=%r" % (n, budget_reached(row["id"], state_name=test_state)))

        # at budget: check-and-append refuses instead of over-recording (finding 3, the race fix)
        n = kick_record(row["id"], state_name=test_state)
        if n is False and get(row["id"], test_state)["kicks"] == 2:
            passed += 1
        else:
            failed += 1
            print("FAIL kick_record at budget -> %r wanted False, kicks still 2" % n)

        closed = close(row["id"], state_name=test_state)
        if closed["status"] == STATUS_CLOSED:
            passed += 1
        else:
            failed += 1
            print("FAIL close did not flip status: %r" % closed)

        row2 = open_loop("workshop", "phase 3 selftest 2", 999, budget=1, state_name=test_state)
        test_loop_ids.append(row2["id"])
        paused = pause(row2["id"], state_name=test_state)
        if paused["status"] == STATUS_PAUSED:
            passed += 1
        else:
            failed += 1
            print("FAIL pause did not flip status: %r" % paused)

        if get("no-such-id", state_name=test_state) is None:
            passed += 1
        else:
            failed += 1
            print("FAIL get on a missing id returned something")
    finally:
        if test_path.is_file():
            test_path.unlink()
        if test_lock.is_file():
            test_lock.unlink()
        if test_loop_ids:
            import datetime as _datetime

            kicks_name = "kicks-%s" % _datetime.date.today().isoformat()

            def _scrub(data, ids=tuple(test_loop_ids)):
                rows = data.get("rows", [])
                data["rows"] = [r for r in rows if r.get("loop_id") not in ids]

            store.mutate(kicks_name, _scrub)

    print("loops.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
