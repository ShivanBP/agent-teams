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

    command = model_command("prompt")
    env = _model_env()
    tools_off = command[command.index("--tools") + 1] == ""
    mcp_config = json.loads(command[command.index("--mcp-config") + 1])
    if all(part in command for part in cases.TODO_COMMAND_CONTAINS) and tools_off \
            and mcp_config == {"mcpServers": {}} and str(constants.REPO_DIR) not in command \
            and not any("ZULIP" in key for key in env):
        passed += 1
    else:
        failed += 1
        print("FAIL model command or environment widened the sweep: %r" % command)

    invoked = {}

    class _Proc:
        returncode = 0
        stdout = json.dumps({
            "result": "[]", "total_cost_usd": 0.012, "num_turns": 1,
            "usage": {"input_tokens": 12, "output_tokens": 3},
        })
        stderr = ""

    def run_stub(command, **kwargs):
        invoked.update(kwargs)
        invoked["empty"] = os.listdir(kwargs["cwd"]) == []
        return _Proc()

    costs = []
    model_rows = run_model("prompt", run=run_stub, lane="digest", cost_fn=costs.append)
    model_cwd = invoked.get("cwd", "")
    if model_rows == [] and invoked.get("empty") and str(constants.REPO_DIR) not in model_cwd \
            and not any("ZULIP" in key for key in invoked.get("env", {})) \
            and len(costs) == 1 and costs[0]["lane"] == "digest" \
            and costs[0]["usd"] == 0.012 and costs[0]["output_tokens"] == 3:
        passed += 1
    else:
        failed += 1
        print("FAIL model call cwd or environment was not empty and isolated: %r" % invoked)

    print("todo.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
