"""Offline fixtures for digest.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import datetime

    import constants
    import tests.cases as cases

    passed = failed = 0
    model_calls = []
    model_call({}, [], run_model_fn=lambda prompt, lane: model_calls.append((prompt, lane)) or {},
               restart_ts_fn=lambda: 123.25)
    if len(model_calls) == 1 and model_calls[0][1] == "digest" \
            and "at most 120 characters" in model_calls[0][0] \
            and "at most 5 open items and 2 newest done items" in model_calls[0][0] \
            and "last_restart_ts: 123.250" in model_calls[0][0] \
            and "Prior digest:\n{}" in model_calls[0][0]:
        passed += 1
    else:
        failed += 1
        print("FAIL model_call did not use the digest cost lane: %r" % (model_calls,))
    without_restart = []
    model_call({}, [],
               run_model_fn=lambda prompt, lane: without_restart.append(prompt) or {},
               restart_ts_fn=lambda: None)
    if len(without_restart) == 1 and "last_restart_ts:" not in without_restart[0]:
        passed += 1
    else:
        failed += 1
        print("FAIL model_call did not omit an unknown restart: %r" % without_restart)

    expected_log_ts = datetime.datetime(2026, 8, 17, 23, 0, 0, 250000).timestamp()
    if _listener_start_ts(cases.DIGEST_RESTART_LOG) == expected_log_ts:
        passed += 1
    else:
        failed += 1
        print("FAIL listener start line was not parsed")

    class _ReadableLog:
        def read_text(self, **kwargs):
            return cases.DIGEST_RESTART_LOG

    fallback_calls = []
    log_ts = last_restart_ts(
        _ReadableLog(), run=lambda *args, **kwargs: fallback_calls.append(args))
    if log_ts == expected_log_ts and fallback_calls == []:
        passed += 1
    else:
        failed += 1
        print("FAIL listener log did not take precedence: %r calls=%r" %
              (log_ts, fallback_calls))

    class _MissingLog:
        def read_text(self, **kwargs):
            raise OSError("missing")

    class _Proc:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout

    commands = []

    def run_start(command, **kwargs):
        commands.append(command)
        return _Proc(cases.DIGEST_LAUNCHD_PRINT if command[0] == "launchctl"
                     else cases.DIGEST_PROCESS_START)

    expected_process_ts = datetime.datetime(2026, 8, 17, 22, 30, 0).timestamp()
    process_ts = last_restart_ts(_MissingLog(), run=run_start, uid_fn=lambda: 501)
    if process_ts == expected_process_ts and commands == [
            ["launchctl", "print", "gui/501/%s" % constants.LAUNCHD_LABEL],
            ["ps", "-o", "lstart=", "-p", "4321"]]:
        passed += 1
    else:
        failed += 1
        print("FAIL launchd process start fallback -> %r commands=%r" % (process_ts, commands))
    def run_missing(*args, **kwargs):
        raise OSError("missing")

    unknown = last_restart_ts(_MissingLog(), run=run_missing)
    if unknown is None:
        passed += 1
    else:
        failed += 1
        print("FAIL unreadable restart sources -> %r" % unknown)
    got = validate_digest(*cases.DIGEST_FILTER_INPUT)
    if got == cases.DIGEST_FILTER_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL validate_digest -> %r wanted %r" % (got, cases.DIGEST_FILTER_EXPECTED))
    bounded = validate_digest(*cases.DIGEST_BOUND_INPUT)
    if bounded == cases.DIGEST_BOUND_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL validate_digest bounds -> %r wanted %r" %
              (bounded, cases.DIGEST_BOUND_EXPECTED))
    cached = bound_cached(dict(cases.DIGEST_BOUND_MODEL, anchor_id=22, ts=1234))
    expected_cached = dict(cases.DIGEST_BOUND_CACHED_EXPECTED, anchor_id=22, ts=1234)
    if cached == expected_cached:
        passed += 1
    else:
        failed += 1
        print("FAIL bound_cached -> %r wanted %r" % (cached, expected_cached))
    migrated = bound_cached(cases.DIGEST_PREVIOUS)
    expected_migrated = dict(cases.DIGEST_PREVIOUS)
    expected_migrated["items"] = [dict(cases.DIGEST_PREVIOUS["items"][0], source_ts=None)]
    if migrated == expected_migrated:
        passed += 1
    else:
        failed += 1
        print("FAIL old cached item migration -> %r wanted %r" % (migrated, expected_migrated))
    roots = [validate_digest(payload, cases.DIGEST_MESSAGES, cases.DIGEST_PREVIOUS)
             for payload, expected in cases.DIGEST_ROOTS]
    expected_roots = [expected for payload, expected in cases.DIGEST_ROOTS]
    if roots == expected_roots:
        passed += 1
    else:
        failed += 1
        print("FAIL validate_digest root schemas -> %r wanted %r" %
              (roots, expected_roots))
    if all(validate_digest(payload, cases.DIGEST_MESSAGES, cases.DIGEST_PREVIOUS) is None
           for payload in cases.DIGEST_BAD_ROOTS):
        passed += 1
    else:
        failed += 1
        print("FAIL validate_digest accepted a bad root")
    safe = safe_text(cases.DIGEST_UNSAFE_TEXT)
    if safe == cases.DIGEST_SAFE_TEXT:
        passed += 1
    else:
        failed += 1
        print("FAIL safe_text -> %r wanted %r" % (safe, cases.DIGEST_SAFE_TEXT))

    state = {"7:topic": dict(cases.DIGEST_PREVIOUS)}

    def mutate(name, fn):
        fn(state)

    refreshed = refresh_topic(
        "bob", 7, "setup", "topic",
        fetch_fn=lambda *a: (cases.DIGEST_MESSAGES, 22, 0),
        model_fn=lambda previous, messages: cases.DIGEST_MODEL,
        load_fn=lambda name: state if name == "digests" else {}, mutate_fn=mutate, now_ts=1234)
    expected_state = {"7:topic": dict(cases.DIGEST_FILTER_EXPECTED, anchor_id=22, ts=1234)}
    if refreshed == expected_state["7:topic"] and state == expected_state:
        passed += 1
    else:
        failed += 1
        print("FAIL refresh_topic -> %r state=%r" % (refreshed, state))

    forced_fetches, forced_models = [], []
    refresh_topic(
        "bob", 7, "setup", "topic",
        fetch_fn=lambda *args: forced_fetches.append(args[-1]) or
        (cases.DIGEST_MESSAGES, 22, 0),
        model_fn=lambda previous, messages: forced_models.append((previous, messages)) or
        cases.DIGEST_MODEL,
        load_fn=lambda name: {"7:topic": cases.DIGEST_PREVIOUS} if name == "digests" else {},
        mutate_fn=lambda name, fn: None, now_ts=1234, force=True)
    if forced_fetches == [{}] and len(forced_models) == 1:
        passed += 1
    else:
        failed += 1
        print("FAIL forced refresh path -> fetches=%r models=%r" %
              (forced_fetches, forced_models))

    parked_calls = []
    parked_result = refresh_topic(
        "bob", 7, "setup", "topic",
        fetch_fn=lambda *args: parked_calls.append(args),
        load_fn=lambda name: {"7:topic": 1234} if name == constants.PARKED_STATE else {})
    if parked_result is None and parked_calls == []:
        passed += 1
    else:
        failed += 1
        print("FAIL parked refresh ran fetch: %r %r" % (parked_result, parked_calls))

    calls = []
    swept = sweep_once(
        streams_fn=lambda as_name: ["random", "setup"],
        stream_id_fn=lambda as_name, channel: 7,
        topics_fn=lambda as_name, stream_id: cases.DIGEST_SWEEP_TOPICS,
        load_fn=lambda name: cases.DIGEST_SWEEP_STATE if name == "digests" else {},
        refresh_fn=lambda *args: calls.append(args))
    if swept == cases.DIGEST_SWEEP_EXPECTED and calls == [
            (constants.OPERATOR_IDENTITY, 7, "setup", "dirty")]:
        passed += 1
    else:
        failed += 1
        print("FAIL sweep_once -> %r calls=%r" % (swept, calls))

    calls = []
    swept = sweep_once(
        streams_fn=lambda as_name: ["setup"],
        stream_id_fn=lambda as_name, channel: 7,
        topics_fn=lambda as_name, stream_id: cases.DIGEST_SWEEP_TOPICS,
        load_fn=lambda name: ({"7:dirty": 1} if name == constants.PARKED_STATE
                              else cases.DIGEST_SWEEP_STATE),
        refresh_fn=lambda *args: calls.append(args))
    if swept == [] and calls == []:
        passed += 1
    else:
        failed += 1
        print("FAIL parked sweep refreshed: %r calls=%r" % (swept, calls))

    print("digest.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
