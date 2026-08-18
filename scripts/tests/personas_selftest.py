"""Offline fixtures for personas.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import tempfile
    from pathlib import Path

    from tests import cases

    passed = failed = 0
    for label, rows, expected_names, expected_errors in cases.PERSONA_FIXTURES:
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            for name, body in rows:
                (directory / name).write_text(body)
            names, errors = _scan(directory)
        if names == set(expected_names) and len(errors) == expected_errors:
            passed += 1
        else:
            failed += 1
            print("FAIL %s -> names=%r errors=%r" % (label, names, errors))
    for name, expected in cases.PERSONA_DISPLAY_NAMES:
        got = display_name(name)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL display_name(%r) -> %r wanted %r" % (name, got, expected))
    print("personas.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
