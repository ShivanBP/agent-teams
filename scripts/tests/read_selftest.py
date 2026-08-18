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
    print("read.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
