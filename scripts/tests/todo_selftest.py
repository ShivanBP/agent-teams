"""Offline fixtures for todo.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import json
    import os

    import constants
    import tests.cases as cases

    passed = failed = 0
    record = message_record(
        "setup", 7,
        {"id": 21, "subject": "topic", "timestamp": 210, "sender_full_name": "Bob"},
        "https://example")
    if record["timestamp"] == 210 and record["id"] == 21 \
            and record["permalink"].endswith("/near/21"):
        passed += 1
    else:
        failed += 1
        print("FAIL message record lost its source timestamp: %r" % record)
    model_json_ok = True
    for raw, expected in cases.TODO_MODEL_JSON:
        try:
            got = parse_model_json(raw)
        except ValueError:
            got = None
        if got != expected:
            model_json_ok = False
            print("FAIL parse_model_json %r -> %r wanted %r" % (raw, got, expected))
    if model_json_ok:
        passed += 1
    else:
        failed += 1
    kept, dropped = cap_messages(cases.TODO_CAP_INPUT, cases.TODO_CAP_CHARS)
    if kept == cases.TODO_CAP_EXPECTED and dropped == cases.TODO_CAP_DROPPED:
        passed += 1
    else:
        failed += 1
        print("FAIL cap_messages -> %r dropped=%r" % (kept, dropped))

    command_ok = not any("ZULIP" in key for key in _model_env())
    for rail, expected in cases.TODO_COMMANDS:
        command = model_command("prompt", rail)
        if not all(part in command for part in expected) or str(constants.REPO_DIR) in command:
            command_ok = False
            print("FAIL %s sweep command widened the sweep: %r" % (rail["provider"], command))
    fenced = model_command("prompt", cases.TODO_RAIL_CLAUDE)
    if fenced[fenced.index("--tools") + 1] != "" \
            or json.loads(fenced[fenced.index("--mcp-config") + 1]) != {"mcpServers": {}}:
        command_ok = False
        print("FAIL claude sweep command lost its tool fence: %r" % fenced)
    if command_ok:
        passed += 1
    else:
        failed += 1

    envelope_ok = True
    for provider, stdout, expected in cases.TODO_ENVELOPES:
        try:
            got = parse_envelope(provider, stdout)
        except (RuntimeError, ValueError) as exc:
            got = type(exc)
        if got != expected:
            envelope_ok = False
            print("FAIL parse_envelope %s %r -> %r wanted %r" % (provider, stdout, got, expected))
    if envelope_ok:
        passed += 1
    else:
        failed += 1

    invoked = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def run_stub(command, **kwargs):
        invoked.update(kwargs)
        invoked["empty"] = os.listdir(kwargs["cwd"]) == []
        return _Proc()

    run_ok = True
    for rail, stdout, expected in cases.TODO_RUNS:
        _Proc.stdout = stdout
        costs = []
        rows = run_model("prompt", run=run_stub, lane="digest", cost_fn=costs.append, rail=rail)
        model_cwd = invoked.get("cwd", "")
        if rows != [] or not invoked.get("empty") or str(constants.REPO_DIR) in model_cwd \
                or any("ZULIP" in key for key in invoked.get("env", {})) \
                or costs != [dict(expected, persona=constants.BRIDGE_IDENTITY, lane="digest")]:
            run_ok = False
            print("FAIL %s sweep run: costs=%r invoked=%r" % (rail["provider"], costs, invoked))
    if run_ok:
        passed += 1
    else:
        failed += 1

    print("todo.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
