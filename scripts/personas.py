"""Static persona name table with an offline fixture selftest."""

import argparse
import re
import sys
import tempfile
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"

PERSONAS = ("archie", "bob", "chella", "eve", "jan", "peter", "writer")

def _frontmatter(path):
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise ValueError("%s has no frontmatter" % path)
    fields = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _scan(directory):
    seen, errors = set(), []
    for path in sorted(directory.glob("*.md")):
        if path.stem in ("operator", "operator-reply"):
            continue
        try:
            name = _frontmatter(path).get("name")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if name != path.stem:
            errors.append("%s has name %r" % (path.name, name))
            continue
        if name in seen:
            errors.append("duplicate persona name %r" % name)
            continue
        seen.add(name)
    return seen, errors


def _selftest():
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
    print("personas.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    ap.error("nothing to do; personas.py is a library")
