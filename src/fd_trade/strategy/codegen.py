"""Custom-code safety: AST validation (design D6, task 8.3).

Strategies on the code track are arbitrary Python; before they ever run they
are validated by ``validate_source``, which enforces a strict allowlist:

- a top-level ``def on_bar(ctx):`` is required (the only entry point);
- ``import`` / ``from ... import`` are forbidden entirely (the host injects
  everything via ``ctx`` and the sandbox's restricted builtins);
- names and attributes starting with ``_`` are forbidden (dunder/private
  reach into interpreter internals);
- direct calls to ``eval``/``exec``/``open``/``compile``/``globals``/
  ``locals``/``vars``/``__import__``/``input``/``breakpoint`` are forbidden.

This runs synchronously at create/update (so an LLM gets immediate, per-violation
feedback) and again defensively inside the sandbox worker.
"""

from __future__ import annotations

import ast

# Calls whose mere presence we reject (the no-imports rule already blocks the
# network vectors; these block reflection/IO that needs no import).
_FORBIDDEN_CALLS = frozenset(
    {"eval", "exec", "open", "compile", "globals", "locals", "vars",
     "__import__", "input", "breakpoint"}
)


class CodeValidationError(ValueError):
    """One or more AST violations; ``errors`` lists each per-violation message."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_source(source: str) -> list[str]:
    """Return a list of violation messages (empty when the source is valid)."""
    errors: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"line {exc.lineno or '?'}: syntax error: {exc.msg}"]

    has_on_bar = False
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            kind = "import" if isinstance(node, ast.Import) else "from-import"
            errors.append(f"line {node.lineno}: {kind} is forbidden; use ctx for everything")
            continue
        if isinstance(node, ast.FunctionDef):
            if node.name == "on_bar":
                has_on_bar = True
            elif node.name.startswith("_"):
                errors.append(
                    f"line {node.lineno}: function '{node.name}' starts with '_' (forbidden)"
                )
        elif isinstance(node, ast.ClassDef) and node.name.startswith("_"):
            errors.append(
                f"line {node.lineno}: class '{node.name}' starts with '_' (forbidden)"
            )

    if not has_on_bar:
        errors.append("source must define a top-level 'def on_bar(ctx):'")

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            errors.append(
                f"line {node.lineno}: name '{node.id}' starts with '_' (forbidden)"
            )
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            errors.append(
                f"line {node.lineno}: attribute '{node.attr}' starts with '_' (forbidden)"
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _FORBIDDEN_CALLS
        ):
            errors.append(
                f"line {node.lineno}: call to '{node.func.id}' is forbidden"
            )

    return errors


def validate_source_strict(source: str) -> None:
    """Validate; raise ``CodeValidationError`` if any violation is found."""
    errors = validate_source(source)
    if errors:
        raise CodeValidationError(errors)


# Safe builtins handed to the sandboxed ``on_bar`` (no I/O, no reflection).
SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "sum": sum, "len": len,
    "range": range, "round": round, "int": int, "float": float,
    "str": str, "bool": bool, "sorted": sorted, "reversed": reversed,
    "zip": zip, "enumerate": enumerate, "map": map, "filter": filter,
    "list": list, "dict": dict, "tuple": tuple, "set": set,
    "True": True, "False": False, "None": None,
}
