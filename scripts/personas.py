"""Static persona name table with an offline fixture selftest."""

import argparse
import re
import sys

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
    from tests import personas_selftest
    return personas_selftest.run(sys.modules[__name__])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    ap.error("nothing to do; personas.py is a library")
