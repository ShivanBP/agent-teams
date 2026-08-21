"""Offline fixtures for api.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import os
    import tests.cases as cases

    passed = failed = 0
    for params, expected in cases.ENCODE:
        got = _encode(params)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _encode(%r) -> %r, wanted %r" % (params, got, expected))
    for site, sid, sname, topic, mid, expected in cases.PERMALINKS:
        got = permalink(site, sid, sname, topic, mid)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL permalink(%r, %r, %r) -> %r, wanted %r" % (sid, sname, topic, got, expected))
    for site, sid, sname, topic, mid, expected in cases.TOPIC_PERMALINKS:
        got = permalink(site, sid, sname, topic, mid, operator="with")
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL permalink(..., operator='with') -> %r, wanted %r" % (got, expected))
    for url, expected in cases.UPLOAD_PATHS:
        got = upload_path(url)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL upload_path(%r) -> %r, wanted %r" % (url, got, expected))
    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"result":"success"}'

    def open_patch(req, timeout):
        captured.update({
            "url": req.full_url,
            "body": req.data,
            "content_type": req.get_header("Content-type"),
            "timeout": timeout,
        })
        return _Response()

    payload = request(
        {"site": "https://example", "email": "bot@example", "key": "secret"},
        "PATCH", "/api/v1/messages/7", {"content": "small edit"}, open_fn=open_patch)
    if payload == {"result": "success"} and captured == cases.PATCH_REQUEST_EXPECTED:
        passed += 1
    else:
        failed += 1
        print("FAIL PATCH request transport -> %r payload=%r" % (captured, payload))
    for asked, env, should_exit in cases.IDENTITY:
        before = os.environ.get("AGENT_TEAM_IDENTITY")
        if env is None:
            os.environ.pop("AGENT_TEAM_IDENTITY", None)
        else:
            os.environ["AGENT_TEAM_IDENTITY"] = env
        try:
            enforce_identity(asked)
            exited = False
        except SystemExit as exc:
            exited = exc.code == 2
        finally:
            os.environ.pop("AGENT_TEAM_IDENTITY", None)
            if before is not None:
                os.environ["AGENT_TEAM_IDENTITY"] = before
        if exited == should_exit:
            passed += 1
        else:
            failed += 1
            print("FAIL enforce_identity(%r) under %r exited=%s" % (asked, env, exited))
    old_load, old_request = load, request
    try:
        globals()["load"] = lambda name: {"name": name}
        for payload, expected in cases.TOPICS:
            globals()["request"] = lambda *a, payload=payload, **k: payload
            got = topics("bridge", 7)
            if got == expected:
                passed += 1
            else:
                failed += 1
                print("FAIL topics(...) -> %r wanted %r" % (got, expected))
        globals()["request"] = lambda *a, **k: cases.VISIBLE_STREAMS
        names = visible_streams("bridge")
        details = visible_streams("bridge", details=True)
        if (names == ["general", "setup"]
                and [stream["name"] for stream in details] == names):
            passed += 1
        else:
            failed += 1
            print("FAIL visible_streams details -> names=%r details=%r" % (names, details))
    finally:
        globals()["load"], globals()["request"] = old_load, old_request
    print("api.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
