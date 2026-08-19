"""Persona name table, read from the persona matrix, with an offline fixture selftest."""

import argparse
import re
import sys

import constants


def load_personas(matrix=None):
    """The fleet roster, in matrix order. The live matrix is the roster of record; constants
    falls back to the example matrix when it is absent, so a clone still has a fleet."""
    return tuple(matrix if matrix is not None else constants.persona_matrix())


def load_display_names(matrix=None):
    """Names the matrix spells out for itself. Anything else capitalizes, so only a name
    capitalize gets wrong needs a row."""
    rows = matrix if matrix is not None else constants.persona_matrix()
    return {name: row["display"] for name, row in rows.items()
            if isinstance(row, dict) and row.get("display")}


PERSONAS = load_personas()
DISPLAY_NAMES = load_display_names()
MENTION_NAMES = tuple(dict.fromkeys(PERSONAS + tuple(DISPLAY_NAMES.values())))


def display_name(name, names=None):
    return (DISPLAY_NAMES if names is None else names).get(name, name.capitalize())

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
        if path.stem in ("operator", constants.BRIDGE_IDENTITY):
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
    from tests import personas_selftest
    return personas_selftest.run(sys.modules[__name__])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    ap.error("nothing to do; personas.py is a library")
