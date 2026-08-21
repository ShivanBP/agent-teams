"""Offline fixtures for read.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import tests.cases as cases

    passed = failed = 0
    for content, expected in cases.INDENTS:
        got = indent(content)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL indent(%r) -> %r, wanted %r" % (content, got, expected))
    for anchor, expected in cases.ANCHORS:
        got = _anchor(anchor)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL anchor(%r) -> %r, wanted %r" % (anchor, got, expected))
    for newer, expected in cases.READ_WINDOWS:
        params = _window(30, newer)
        if params == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL read window newer=%r -> %r wanted %r" % (newer, params, expected))
    for args, expected in cases.READ_NARROWS:
        got = _narrow(*args)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _narrow%r -> %r wanted %r" % (args, got, expected))
    got = render_channels(cases.CHANNEL_LIST[0])
    if got == cases.CHANNEL_LIST[1]:
        passed += 1
    else:
        failed += 1
        print("FAIL render_channels(...) -> %r" % got)
    got = render_topics(cases.TOPIC_LIST[0], cases.TOPIC_LIST[1])
    if got == cases.TOPIC_LIST[2]:
        passed += 1
    else:
        failed += 1
        print("FAIL render_topics(...) -> %r" % got)
    got = render_cross_channel(
        cases.CROSS_CHANNEL_MESSAGES, "https://example.zulipchat.com", ids=True)
    lines = got.splitlines()
    if (len(lines) == 3 and "#setup > new" in lines[1] and lines[1].endswith("#2")
            and "#general > old" in lines[2] and lines[2].endswith("#1")):
        passed += 1
    else:
        failed += 1
        print("FAIL render_cross_channel ordering/ids -> %r" % got)
    got = render([cases.CROSS_CHANNEL_MESSAGES[0]], ids=True)
    if got.splitlines()[-1].endswith("#1"):
        passed += 1
    else:
        failed += 1
        print("FAIL render ids suffix -> %r" % got)
    for messages, expected in cases.TOPIC_LINE_MESSAGES:
        got = topic_line(messages, "https://example.zulipchat.com")
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL topic_line(...) -> %r, wanted %r" % (got, expected))
    for ctype, data, expected in cases.ATTACHMENT_RENDERS:
        got = render_attachment(ctype, data, "LINK")
        if got == "\n".join(expected):
            passed += 1
        else:
            failed += 1
            print("FAIL render_attachment(%r) -> %r" % (ctype, got))
    # --out is the only side effect in this module, so a row drives it rather than asserting source.
    import tempfile
    with tempfile.TemporaryDirectory() as box:
        target = Path(box) / "shot.png"
        payload = b"\x89PNG\r\n\x1a\n"
        got = render_attachment("image/png", payload, "LINK", out=str(target))
        if target.read_bytes() == payload and str(target) in got:
            passed += 1
        else:
            failed += 1
            print("FAIL render_attachment --out did not write the bytes: %r" % got)
    print("read.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
