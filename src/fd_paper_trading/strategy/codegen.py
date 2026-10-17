"""Custom-code safety: AST validation (design D6, task 8.3).

Strategies on the code track are arbitrary Python; before they ever run they
are validated by ``validate_source``, which enforces a strict allowlist:

- a top-level entry function is required (``on_bar(ctx)`` on the single-symbol
  track, ``target_weights(ctx)`` on the weight track — the required name is the
  only thing ``entry_point`` changes; every restriction below is identical);
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

#: Entry-point names the sandbox may call, per track.
ENTRY_POINTS = ("on_bar", "target_weights")

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


def validate_source(source: str, entry_point: str = "on_bar") -> list[str]:
    """Return a list of violation messages (empty when the source is valid).

    ``entry_point`` names the top-level function the track requires; passing an
    unknown name is a programming error (the allowlist must not silently accept a
    typo'd entry point and then require nothing).
    """
    if entry_point not in ENTRY_POINTS:
        raise ValueError(
            f"unknown entry_point {entry_point!r} (expected one of {ENTRY_POINTS})"
        )
    errors: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"line {exc.lineno or '?'}: syntax error: {exc.msg}"]

    has_entry = False
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            kind = "import" if isinstance(node, ast.Import) else "from-import"
            errors.append(f"line {node.lineno}: {kind} is forbidden; use ctx for everything")
            continue
        if isinstance(node, ast.FunctionDef):
            if node.name == entry_point:
                has_entry = True
            elif node.name.startswith("_"):
                errors.append(
                    f"line {node.lineno}: function '{node.name}' starts with '_' (forbidden)"
                )
        elif isinstance(node, ast.ClassDef) and node.name.startswith("_"):
            errors.append(
                f"line {node.lineno}: class '{node.name}' starts with '_' (forbidden)"
            )

    if not has_entry:
        errors.append(f"source must define a top-level 'def {entry_point}(ctx):'")

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


def validate_source_strict(source: str, entry_point: str = "on_bar") -> None:
    """Validate; raise ``CodeValidationError`` if any violation is found."""
    errors = validate_source(source, entry_point=entry_point)
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
