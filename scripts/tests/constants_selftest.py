"""Offline fixtures for constants.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    import json
    import os
    import tests.cases as cases

    # prompts.py is imported here, not at module scope: constants.py stays a leaf every other
    # module can load first, with no dependencies of its own beyond stdlib.
    import prompts

    passed = failed = 0
    try:
        example = json.loads(MATRIX_EXAMPLE_PATH.read_text())
        valid_rows = all(
            isinstance(row, dict) and set(row) == {"provider", "model", "effort"}
            and row["effort"] in _EFFORT_SCALE
            for row in example.values()
        )
        if example and valid_rows:
            passed += 1
        else:
            failed += 1
            print("FAIL persona matrix example is empty or its rows are invalid")
    except (OSError, ValueError) as exc:
        failed += 1
        print("FAIL persona matrix example does not load: %s" % exc)

    try:
        harness = json.loads(HARNESS_DEFAULTS_EXAMPLE_PATH.read_text())
        valid_rows = all(
            isinstance(row, dict) and set(row) == {"model", "effort"}
            and isinstance(row["model"], str) and bool(row["model"])
            and row["effort"] in _EFFORT_SCALE
            for row in harness.values()
        )
        if set(harness) == {"codex", "agy", "opencode"} and valid_rows:
            passed += 1
        else:
            failed += 1
            print("FAIL harness defaults example keys or rows are invalid")
    except (OSError, ValueError) as exc:
        failed += 1
        print("FAIL harness defaults example does not load: %s" % exc)

    try:
        model_efforts = json.loads(MODEL_EFFORT_DEFAULTS_EXAMPLE_PATH.read_text())
        if set(model_efforts) == {"opus", "fable"} and all(
                level in _EFFORT_SCALE for level in model_efforts.values()):
            passed += 1
        else:
            failed += 1
            print("FAIL model effort defaults example is invalid")
    except (OSError, ValueError) as exc:
        failed += 1
        print("FAIL model effort defaults example does not load: %s" % exc)

    for provider, level, expected in cases.EFFORT_TRANSLATIONS:
        try:
            got = translate_effort(provider, level)
        except RuntimeError as exc:
            got = type(exc)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL translate_effort(%r, %r) -> %r wanted %r" %
                  (provider, level, got, expected))

    var = "AGENT_TEAM_TEST_NUM_CASE"
    for raw, default, cast, expect_exit, expected in cases.NUM_PARSES:
        if raw is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = raw
        try:
            got = _num(var, default, cast)
            exited = False
        except SystemExit:
            got = None
            exited = True
        finally:
            os.environ.pop(var, None)
        if exited == expect_exit and (expect_exit or got == expected):
            passed += 1
        else:
            failed += 1
            print("FAIL _num(raw=%r, default=%r) -> got=%r exited=%s wanted=%r exit=%s" %
                  (raw, default, got, exited, expected, expect_exit))

    for plural, singular, expected in cases.MATE_EMAIL_SETS:
        got = _mate_emails(plural, singular)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _mate_emails(%r, %r) -> %r wanted %r" %
                  (plural, singular, got, expected))

    channels = board_channels()
    if channels == cases.BOARD_CHANNELS:
        passed += 1
    else:
        failed += 1
        print("FAIL board_channels() -> %r wanted %r" %
              (channels, cases.BOARD_CHANNELS))

    # prose-agreement pins: constants own the numbers, prompts.py owns the hand-written words
    # describing them; this catches the two drifting apart (Jan's 2026-08-12 finding).
    files_word = _WORDS.get(ATTACH_MAX_FILES, str(ATTACH_MAX_FILES))
    mb = ATTACH_MAX_BYTES // (1024 * 1024)
    pins = [
        ("prompts.ATTACH_TOO_MANY", "%s files" % files_word, prompts.ATTACH_TOO_MANY),
        ("prompts.ATTACH_TOO_LARGE", "%dMB" % mb, prompts.ATTACH_TOO_LARGE),
        ("prompts.WAKE_HEADER (files)", "%d files max" % ATTACH_MAX_FILES, prompts.WAKE_HEADER),
        ("prompts.WAKE_HEADER (bytes)", "%dMB each" % mb, prompts.WAKE_HEADER),
    ]
    for label, needle, haystack in pins:
        if needle in haystack:
            passed += 1
        else:
            failed += 1
            print("FAIL %s does not say %r (ATTACH_MAX_FILES=%d, ATTACH_MAX_BYTES=%d)" %
                  (label, needle, ATTACH_MAX_FILES, ATTACH_MAX_BYTES))

    print("constants.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0
