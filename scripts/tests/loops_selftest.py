"""Offline fixtures for loops.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import contextlib
    import io
    import os
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

        # fire_kick's pre-flight: every refusal leaves the ledger untouched. resolve_header is
        # stubbed both ways, so no row reaches the network, and the identity-mismatch row exits
        # through api.enforce_identity rather than the refuse hook.
        row3 = open_loop("workshop", "phase 3 fire_kick", 555, budget=5, state_name=test_state)
        test_loop_ids.append(row3["id"])

        class _Refused(Exception):
            pass

        def _refuse(message):
            raise _Refused(message)

        saved_resolve = globals()["resolve_header"]
        saved_identity = os.environ.get("AGENT_TEAM_IDENTITY")
        try:
            for label, persona, as_name, identity, resolves in cases.FIRE_KICK_REFUSALS:
                globals()["resolve_header"] = (lambda *a, **k: (1, "t", "")) if resolves else (
                    lambda *a, **k: (None, None, None))
                if identity is None:
                    os.environ.pop("AGENT_TEAM_IDENTITY", None)
                else:
                    os.environ["AGENT_TEAM_IDENTITY"] = identity
                try:
                    with contextlib.redirect_stderr(io.StringIO()):
                        fire_kick(row3["id"], persona, "body", as_name, _refuse, state_name=test_state)
                    refused = False
                except (_Refused, SystemExit):
                    refused = True
                kicks = get(row3["id"], test_state)["kicks"]
                if refused and kicks == 0:
                    passed += 1
                else:
                    failed += 1
                    print("FAIL fire_kick %s -> refused=%r kicks=%r wanted True, 0"
                          % (label, refused, kicks))
        finally:
            globals()["resolve_header"] = saved_resolve
            os.environ.pop("AGENT_TEAM_IDENTITY", None)
            if saved_identity is not None:
                os.environ["AGENT_TEAM_IDENTITY"] = saved_identity
        # the kick default and the refusal both follow BRIDGE_IDENTITY: main() is driven for real
        # with fire_kick stubbed, so a default that parses but never reaches the call fails here.
        saved_bridge = constants.BRIDGE_IDENTITY
        try:
            for identity, argv, expected in cases.KICK_AS_DEFAULTS:
                constants.BRIDGE_IDENTITY = identity
                seen = []
                saved = (sys.argv, globals()["fire_kick"], sys.stdout)
                try:
                    sys.argv = ["loops.py"] + argv
                    globals()["fire_kick"] = lambda *a, **k: seen.append(a[3]) or {}
                    sys.stdout = io.StringIO()
                    main()
                finally:
                    sys.argv, globals()["fire_kick"], sys.stdout = saved
                got = seen[0] if seen else "fire_kick never called"
                if got == expected:
                    passed += 1
                else:
                    failed += 1
                    print("FAIL kick --as default under BRIDGE_IDENTITY=%r -> %r wanted %r"
                          % (identity, got, expected))

            constants.BRIDGE_IDENTITY = "courier"
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    fire_kick(row3["id"], "peter", "body", "bob", _refuse, state_name=test_state)
                note = ""
            except _Refused as exc:
                note = str(exc)
            if "courier" in note and "bridge" not in note:
                passed += 1
            else:
                failed += 1
                print("FAIL renamed-seat kick refusal -> %r" % note)
        finally:
            constants.BRIDGE_IDENTITY = saved_bridge
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
