"""One-shot terminal snapshot of persona activity, cost, and loop kicks."""

import argparse
import datetime
import json
import sys
import time

import constants
import personas
import runner
import store


def _ledger_rows(prefix):
    name = "%s-%s" % (prefix, datetime.date.today().isoformat())
    return store.load(name).get("rows", [])


def snapshot(inflight=None, cost_rows=None, kick_rows=None, matrix=None):
    inflight = store.inflight_all() if inflight is None else inflight
    cost_rows = _ledger_rows("cost") if cost_rows is None else cost_rows
    kick_rows = _ledger_rows("kicks") if kick_rows is None else kick_rows
    defaults = matrix or {name: constants.matrix_defaults(name) for name in personas.PERSONAS}
    data = {}
    for name in personas.PERSONAS:
        data[name] = {
            "provider": defaults[name]["provider"],
            "status": "--",
            "topic": None,
            "cost_today": 0.0,
            "runs_today": 0,
            "kicks_today": 0,
        }
    for info in inflight.values():
        name = info.get("persona")
        if name not in data:
            continue
        data[name]["status"] = "running"
        data[name]["topic"] = info.get("topic")
        data[name]["provider"] = info.get("provider", data[name]["provider"])
    for row in cost_rows:
        name = row.get("persona")
        if name not in data:
            continue
        data[name]["cost_today"] += float(row.get("usd") or 0.0)
        data[name]["runs_today"] += 1
    for row in kick_rows:
        name = row.get("persona")
        if name in data:
            data[name]["kicks_today"] += 1
    return data


def lane_rows(inflight=None, now_ts=None, log_mtimes=None):
    inflight = store.inflight_all() if inflight is None else inflight
    now_ts = time.time() if now_ts is None else now_ts
    rows = []
    for lane, info in sorted(inflight.items()):
        started = info.get("ts")
        running_s = max(0, now_ts - started) if started is not None else None
        provider = info.get("provider") or "claude"
        mtime = None
        if provider != "claude":
            if log_mtimes is not None:
                mtime = log_mtimes.get(lane)
            else:
                try:
                    mtime = runner._wake_log_path(lane).stat().st_mtime
                except OSError:
                    pass
        idle_s = max(0, now_ts - mtime) if mtime is not None and started is not None \
            and mtime >= started else None
        rows.append({
            "lane": lane,
            "persona": info.get("persona") or "-",
            "provider": provider,
            "topic": info.get("topic") or "-",
            "running_s": running_s,
            "idle_s": idle_s,
            "stuck": running_s is not None and running_s > constants.STALL_MIN * 60,
        })
    return rows


def format_age(seconds):
    if seconds is None:
        return "-"
    minutes = int(seconds // 60)
    if minutes < 60:
        return "%dm" % minutes
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return "%dh %02dm" % (hours, minutes)
    days, hours = divmod(hours, 24)
    return "%dd %02dh" % (days, hours)


def render_lanes(rows):
    table = [("Persona", "Provider", "Topic", "Running", "Idle", "State")]
    for row in rows:
        table.append((row["persona"], row["provider"], row["topic"],
                      format_age(row["running_s"]), format_age(row["idle_s"]),
                      "STUCK" if row["stuck"] else ""))
    widths = [max(len(str(row[i])) for row in table) for i in range(len(table[0]))]
    return "\n".join("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)).rstrip()
                     for row in table)


def render_table(data):
    rows = [("Persona", "Provider", "Status", "Cost Today", "Kicks Today")]
    for name in personas.PERSONAS:
        row = data[name]
        status = row["status"]
        if status == "running" and row["topic"]:
            status = "running (%s)" % row["topic"]
        rows.append((name, row["provider"], status,
                     "$%.3f" % row["cost_today"], str(row["kicks_today"])))
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(str(value).ljust(widths[i]) for i, value in enumerate(row)).rstrip()
                     for row in rows)


def _selftest():
    import tests.cases as cases

    passed = failed = 0
    try:
        store.inflight_all()
        _ledger_rows("cost")
        _ledger_rows("kicks")
        passed += 3
    except (OSError, ValueError) as exc:
        failed += 1
        print("FAIL monitor state paths are not readable: %s" % exc)

    got = snapshot(**cases.MONITOR_INPUT)
    if got == cases.MONITOR_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL snapshot(...) -> %r wanted %r" % (got, cases.MONITOR_EXPECTED))
    table = render_table(got)
    if table.startswith("Persona") and len(table.splitlines()) == len(personas.PERSONAS) + 1:
        passed += 1
    else:
        failed += 1
        print("FAIL rendered table is empty or incomplete")
    lanes = lane_rows(**cases.MONITOR_LANE_INPUT)
    if lanes == cases.MONITOR_LANE_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL lane_rows(...) -> %r wanted %r" % (lanes, cases.MONITOR_LANE_EXPECTED))
    lane_table = render_lanes(lanes)
    if "STUCK" in lane_table and lane_table.count("jan") == 2 and "-" in lane_table:
        passed += 1
    else:
        failed += 1
        print("FAIL rendered lanes lost duplicate personas, idle fallback, or STUCK")
    for seconds, expected in cases.MONITOR_AGES:
        got_age = format_age(seconds)
        if got_age == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL format_age(%r) -> %r wanted %r" % (seconds, got_age, expected))

    print("monitor.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    data = snapshot()
    if args.json:
        print(json.dumps({"lanes": lane_rows(), "personas": data}, indent=2))
    else:
        print(render_lanes(lane_rows()))
        print()
        print(render_table(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
