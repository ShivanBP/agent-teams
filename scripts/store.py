"""Atomic state under ~/.config/agent-team/state/. One mutate() choke point: fcntl lock, tmp+rename."""

import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import time

import constants

STATE_DIR = constants.STATE_DIR


def _path(name):
    return STATE_DIR / ("%s.json" % name)


def _lock_path(name):
    return STATE_DIR / ("%s.lock" % name)


@contextlib.contextmanager
def _locked(name, timeout=None):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_lock_path(name)), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if timeout is None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("lock %s unavailable" % name)
                    time.sleep(min(0.05, remaining))
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load(name):
    """Read name.json; {} if absent. No lock: callers needing consistency use mutate()."""
    path = _path(name)
    if not path.is_file():
        return {}
    with open(path) as f:
        return json.load(f)


def mutate(name, fn):
    """Every write is tmp+rename under this one choke point; two writers cannot lose each other.

    fn(data) mutates the dict in place (or returns a replacement dict). Runs under the
    per-name fcntl lock so a read-modify-write is atomic against concurrent mutate() calls.
    """
    with _locked(name):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = _path(name)
        data = {}
        if path.is_file():
            with open(path) as f:
                data = json.load(f)
        result = fn(data)
        if isinstance(result, dict):
            data = result
        tmp = STATE_DIR / (".%s.json.tmp-%d" % (name, os.getpid()))
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return data


def normalize_topic(topic):
    """Strip Zulip's resolve prefix (plus any single following space) before building any lane
    key (finding 1a): a resolved rename must not orphan the row. The one normalize function;
    every lane_key call runs through it."""
    t = (topic or "").strip()
    if t.startswith(constants.RESOLVED_PREFIX):
        t = t[len(constants.RESOLVED_PREFIX):]
        if t.startswith(" "):
            t = t[1:]
    return t


def lane_key(stream_id, topic, persona):
    """lane = stream_id:topic:persona, topic normalized so resolve/unresolve never orphans the row."""
    return "%s:%s:%s" % (stream_id, normalize_topic(topic), persona)


def lane_lock(lane):
    """A lock held from session read to write-back for one lane, separate from any single
    state file's mutate() lock, so a long-running wake does not block other lanes' cost/inflight
    writes for its duration. Two resumes of one session interleaving transcripts is the failure
    this prevents."""
    digest = hashlib.sha1(lane.encode()).hexdigest()[:16]
    return _locked("lane-%s" % digest)


# --- sessions: provider and session id always travel together --------------------------------

def session_get(lane):
    return load("sessions").get(lane)


def session_provider(row):
    return (row or {}).get("provider") or "claude"


def session_for_provider(row, provider):
    if not row or session_provider(row) != provider:
        return None
    return row.get("session_id")


def session_set(lane, session_id, record_anchor, provider):
    def fn(data):
        data[lane] = {
            "session_id": session_id,
            "record_anchor": record_anchor,
            "provider": provider,
        }

    mutate("sessions", fn)


def session_drop(lane):
    def fn(data):
        data.pop(lane, None)

    mutate("sessions", fn)


# --- inflight: written outside the lane lock so waiters stay visible ------------------------

def inflight_add(lane, info=None):
    def fn(data):
        data[lane] = dict(info or {}, ts=time.time())

    mutate("inflight", fn)


def inflight_clear(lane):
    def fn(data):
        data.pop(lane, None)

    mutate("inflight", fn)


def inflight_all():
    return load("inflight")


# --- daily ledgers: cost and kicks, one file per calendar day --------------------------------

def _today():
    return datetime.date.today().isoformat()


def cost_append(row):
    """row carries persona, lane, provider, usd, turns, cache anatomy and ts."""
    name = "cost-%s" % _today()

    def fn(data):
        data.setdefault("rows", []).append(dict(row, ts=row.get("ts", time.time())))

    mutate(name, fn)


def kick_append(row):
    name = "kicks-%s" % _today()

    def fn(data):
        data.setdefault("rows", []).append(dict(row, ts=row.get("ts", time.time())))

    mutate(name, fn)


# --- state summary: what an operator brief would otherwise spend turns reading ----------------

def _clock(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "-"


def state_summary(now=None, day=None, loops_name="loops", inflight_name="inflight"):
    """The ledger facts an operator seat asks for: open loops, lanes inflight, today's wakes,
    today's kicks, today's spend. Read-only, and every file name is injectable so a selftest
    never touches a live ledger. The cost ledger is the wake log; there is no separate one."""
    import loops  # local: loops imports store, so this cannot be a module-level import

    now = time.time() if now is None else now
    day = _today() if day is None else day
    cost_rows = load("cost-%s" % day).get("rows", [])
    return {
        "day": day,
        "open_loops": [
            {"id": row.get("id"), "channel": row.get("channel"), "topic": row.get("topic"),
             "kicks": row.get("kicks", 0), "budget": row.get("budget", 0)}
            for row in loops.all_rows(loops_name).values()
            if row.get("status") == loops.STATUS_OPEN
        ],
        "inflight": [
            {"lane": lane, "age_min": int(max(0, now - row.get("ts", now)) // 60)}
            for lane, row in sorted(load(inflight_name).items())
        ],
        "wakes": [{"persona": row.get("persona"), "at": _clock(row.get("ts"))} for row in cost_rows],
        "kicks": len(load("kicks-%s" % day).get("rows", [])),
        "spend": round(sum(float(row.get("usd") or 0) for row in cost_rows), 2),
    }


# --- catch-up: last seen message id per identity ---------------------------------------------

def seen_get(identity):
    return load("seen").get(identity)


def seen_set(identity, message_id):
    def fn(data):
        data[identity] = message_id

    mutate("seen", fn)


def _selftest():
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


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    ap.error("nothing to do; store.py is a library")
