"""Estate tripwire: shared machinery carries no estate values, so any estate must be able to
run scripts/ unmodified. Comments and docstrings are exempt, so attribution lines survive."""

import ast
import io
import re
import tokenize

HOME_ROOTS = ("/Users/", "/home/")


def names(matrix):
    """Every estate name a matrix spells: its keys and the display names it overrides."""
    found = set(matrix)
    for row in matrix.values():
        if isinstance(row, dict) and row.get("display"):
            found.add(row["display"])
    return found


def _docstring_lines(text):
    lines = set()
    for node in ast.walk(ast.parse(text)):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            lines.update(range(first.lineno, first.end_lineno + 1))
    return lines


def hits(text, estate_names):
    """(line, needle) for every estate value in live source, comments and docstrings aside."""
    found = []
    skip = _docstring_lines(text)
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type not in (tokenize.NAME, tokenize.STRING) or token.start[0] in skip:
            continue
        for name in sorted(estate_names):
            if re.search(r"\b%s\b" % re.escape(name), token.string, re.I):
                found.append((token.start[0], name))
        if token.type == tokenize.STRING:
            found.extend((token.start[0], root) for root in HOME_ROOTS if root in token.string)
    return found
