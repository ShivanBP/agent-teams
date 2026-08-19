"""Offline fixtures for personas.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import json
    import tempfile
    from pathlib import Path

    import constants
    from tests import cases

    passed = failed = 0
    roster = load_personas(json.loads(constants.MATRIX_EXAMPLE_PATH.read_text()))
    if roster == cases.PERSONA_EXAMPLE_ROSTER:
        passed += 1
    else:
        failed += 1
        print("FAIL load_personas(example) -> %r wanted %r"
              % (roster, cases.PERSONA_EXAMPLE_ROSTER))
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
    saved_identity = constants.BRIDGE_IDENTITY
    try:
        for label, identity, rows, expected_names in cases.PERSONA_SCAN_RENAMED:
            constants.BRIDGE_IDENTITY = identity
            with tempfile.TemporaryDirectory() as root:
                directory = Path(root)
                for name, body in rows:
                    (directory / name).write_text(body)
                names, _errors = _scan(directory)
            if names == expected_names:
                passed += 1
            else:
                failed += 1
                print("FAIL %s -> names=%r wanted %r" % (label, names, expected_names))
    finally:
        constants.BRIDGE_IDENTITY = saved_identity

    example = json.loads(constants.MATRIX_EXAMPLE_PATH.read_text())
    names = load_display_names(example)
    if names == cases.PERSONA_DISPLAY_MAP:
        passed += 1
    else:
        failed += 1
        print("FAIL load_display_names(example) -> %r wanted %r"
              % (names, cases.PERSONA_DISPLAY_MAP))
    for name, expected in cases.PERSONA_DISPLAY_NAMES:
        got = display_name(name, names)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL display_name(%r) -> %r wanted %r" % (name, got, expected))
    print("personas.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
