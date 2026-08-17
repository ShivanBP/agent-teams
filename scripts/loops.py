"""Loop registry for the operator rails. Row: {id, header_id, budget, kicks, status, channel,
topic, opened_ts}. Addressing at kick time always re-resolves header_id through the `with`
narrow (invariant: display names key nothing); channel/topic on the row are informational only,
recorded at open time for `list`, and never load-bearing for where a kick lands.
"""

import argparse
import json
import sys
import time
import uuid

import api
import constants
import store

STATE_NAME = "loops"

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_PAUSED = "paused"


def _rows(data):
    return data.setdefault("rows", {})


def open_loop(channel, topic, header_id, budget=constants.LOOP_BUDGET_DEFAULT, state_name=STATE_NAME):
    loop_id = uuid.uuid4().hex[:8]
    row = {
        "id": loop_id,
        "header_id": int(header_id),
        "budget": int(budget),
        "kicks": 0,
        "status": STATUS_OPEN,
        "channel": channel,
        "topic": topic,
        "opened_ts": time.time(),
    }

    def fn(data):
        _rows(data)[loop_id] = row

    store.mutate(state_name, fn)
    return row


def get(loop_id, state_name=STATE_NAME):
    return store.load(state_name).get("rows", {}).get(loop_id)


def all_rows(state_name=STATE_NAME):
    return store.load(state_name).get("rows", {})


def close(loop_id, status=STATUS_CLOSED, state_name=STATE_NAME):
    def fn(data):
        row = _rows(data).get(loop_id)
        if row is not None:
            row["status"] = status

    store.mutate(state_name, fn)
    return get(loop_id, state_name)


def pause(loop_id, state_name=STATE_NAME):
    """A sweep pauses, never continues; nothing in this module resumes a paused loop."""
    return close(loop_id, status=STATUS_PAUSED, state_name=state_name)


def kick_record(loop_id, persona=None, state_name=STATE_NAME):
    """Check-and-increment inside one store.mutate: at or over budget, returns False without
    appending (invariant: two concurrent continuations can never both record past budget).
    On success, also appends the kick ledger row (store.kick_append) and returns the new count.
    This is the ledger row that must land before the kick posts, so callers write this first and
    only post the kick once it returns a truthy count."""
    outcome = {}

    def fn(data):
        row = _rows(data).get(loop_id)
        if row is None:
            raise KeyError(loop_id)
        if _budget_reached(row["kicks"], row["budget"]):
            outcome["ok"] = False
            return
        row["kicks"] += 1
        outcome["ok"] = True
        outcome["n"] = row["kicks"]

    store.mutate(state_name, fn)
    if not outcome.get("ok"):
        return False
    n = outcome["n"]
    store.kick_append({"loop_id": loop_id, "persona": persona, "n": n})
    return n


def _budget_reached(kicks, budget):
    return kicks >= budget


def budget_reached(loop_id, state_name=STATE_NAME):
    row = get(loop_id, state_name)
    if row is None:
        return True
    return _budget_reached(row["kicks"], row["budget"])


# --- header resolution: the `with` narrow, re-resolved every time, never cached -----------------

def _extract_location(payload):
    """Pure: pulls (stream_id, topic, content) out of a with-narrow response. Kept separate from
    the network call so --selftest exercises the parsing without a live server."""
    if payload.get("result") != "success":
        return None, None, None
    messages = payload.get("messages", [])
    if not messages:
        return None, None, None
    msg = messages[0]
    return msg.get("stream_id"), msg.get("subject"), msg.get("content")


def _with_narrow_params(header_id):
    """Pure: the GET /messages params for the with-narrow header lookup. anchor must be the
    header id itself (probe 4, live-verified) -- anchor="newest" with num_before=num_after=0
    returns zero messages on this server, always. include_anchor defaults true, so this shape
    returns exactly the header message at its current stream/topic."""
    return {
        "anchor": int(header_id),
        "num_before": 0,
        "num_after": 0,
        "narrow": [{"operator": "with", "operand": int(header_id)}],
        "apply_markdown": False,
    }


def resolve_header(as_name, header_id):
    """GET /messages narrow [{"operator": "with", "operand": header_id}] -> the header message's
    current stream, topic and content."""
    cfg = api.load(as_name)
    payload = api.request(cfg, "GET", "/api/v1/messages", _with_narrow_params(header_id))
    return _extract_location(payload)


def loop_for_lane(as_name, stream_id, topic, state_name=STATE_NAME):
    """The open loop, if any, whose header currently resolves to this stream_id+topic. Paused and
    closed loops are never continued. Returns the row plus current_channel/current_topic/
    header_text resolved fresh, or None."""
    normalized = store.normalize_topic(topic)
    for row in all_rows(state_name).values():
        if row.get("status") != STATUS_OPEN:
            continue
        cur_stream, cur_topic, header_text = resolve_header(as_name, row["header_id"])
        if cur_stream == stream_id and store.normalize_topic(cur_topic) == normalized:
            return dict(row, current_channel=cur_stream, current_topic=cur_topic, header_text=header_text)
    return None


# --- CLI -----------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    op = sub.add_parser("open")
    op.add_argument("--channel", required=True)
    op.add_argument("--topic", required=True)
    op.add_argument("--header", required=True, type=int)
    op.add_argument("--budget", type=int, default=constants.LOOP_BUDGET_DEFAULT)

    cl = sub.add_parser("close")
    cl.add_argument("--id", required=True)

    kk = sub.add_parser("kick")
    kk.add_argument("--id", required=True)
    kk.add_argument("--persona", required=True)
    kk.add_argument("--body", required=True)
    kk.add_argument("--as", dest="as_name", default="bridge")

    ls = sub.add_parser("list")
    ls.add_argument("--all", action="store_true")
    ls.add_argument("--json", action="store_true")

    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())

    if args.cmd == "open":
        row = open_loop(args.channel, args.topic, args.header, args.budget)
        print(json.dumps(row, indent=2))
    elif args.cmd == "close":
        row = close(args.id)
        if row is None:
            ap.error("no such loop: %s" % args.id)
        print(json.dumps(row, indent=2))
    elif args.cmd == "kick":
        # The opener's verb for kick one: the ledger row lands before the kick posts, same
        # order Rail A follows, so the thread and the ledger can never disagree on the count.
        row = get(args.id)
        if row is None or row.get("status") != STATUS_OPEN:
            ap.error("no open loop: %s" % args.id)
        n = kick_record(args.id, persona=args.persona)
        if not n:
            ap.error("budget reached for loop %s; no kick recorded" % args.id)
        import prompts
        import send as send_mod
        stream_id, topic, _content = resolve_header(args.as_name, row["header_id"])
        if topic is None:
            ap.error("header %s did not resolve; kick recorded as %d but NOT posted; post by hand" % (row["header_id"], n))
        body = prompts.MENTION.format(name=args.persona.capitalize(), body=args.body)
        mid = send_mod.post(args.as_name, stream_id, topic, prompts.kick_body(body, n, row["budget"]))
        print(json.dumps({"kick": n, "budget": row["budget"], "message_id": mid}, indent=2))
    elif args.cmd == "list":
        rows = list(all_rows().values())
        if not args.all:
            rows = [r for r in rows if r.get("status") == STATUS_OPEN]
        rows.sort(key=lambda r: r.get("opened_ts", 0))
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print(
                    "%s  %-7s budget=%d kicks=%d header=%s topic=%r"
                    % (r["id"], r["status"], r["budget"], r["kicks"], r["header_id"], r.get("topic"))
                )
    else:
        ap.error("choose open, close, list, or --selftest")


def _selftest():
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


if __name__ == "__main__":
    main()
