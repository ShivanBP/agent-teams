"""One-shot terminal snapshot of persona activity, cost, and loop kicks."""

import argparse
import datetime
import json
import sys

import constants
import personas
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
        print(json.dumps(data, indent=2))
    else:
        print(render_table(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
