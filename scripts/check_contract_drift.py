#!/usr/bin/env python3
"""Contract vendor drift gate: the vendored TargetWeights contract must stay
byte-identical to its upstream copy in ``../fd-quant``.

Why a copy exists at all: ``fd-quant`` has no distribution channel today — it is
not on PyPI and is not even a git repository, while this repo builds with
``uv sync --frozen`` against a committed ``uv.lock`` and a Docker build that
copies only this repository. A path dependency on ``../fd-quant`` therefore
cannot resolve in CI or in the image. The copy is **byte-identical** (not a
reimplementation of the rules) and pinned by sha256; this gate fails loudly when
upstream moves. The switch to a real package dependency is tracked in
``openspec/changes/strategy-weight-mode/tasks.md`` (1.2).

Modes:
  --check (default)  verify vendored == pin; when an upstream checkout exists,
                     verify upstream == pin as well (the drift check proper).
  --sync             (re)copy from upstream and rewrite the pin. Author action.
  --selfcheck        reverse self-check: corrupt a throwaway copy and assert the
                     gate actually fails (a gate that cannot fail proves nothing).

Exit codes: 0 clean, 1 problems, 2 usage/upstream-missing-when-required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "src" / "fd_paper_trading" / "_vendor"
PIN_FILE = VENDOR_DIR / "VENDOR.json"

#: Vendored path → upstream path, both **relative to the respective repo root**.
#: The mirror reproduces upstream's layout verbatim because the byte-identical
#: ``models.py`` resolves its schemas through ``Path(__file__).parents[2] /
#: "schemas"`` — upstream keeps ``schemas/`` as a sibling of the ``fd_quant``
#: package, so a vendored copy at any other depth silently loses ``load_schema``.
#: Keeping the mapping an identity is the point: it makes that assumption hold by
#: construction, and ``check()`` asserts it rather than trusting it.
VENDORED_FILES = {
    "fd_quant/contracts/models.py": "fd_quant/contracts/models.py",
    "fd_quant/contracts/unsourced.py": "fd_quant/contracts/unsourced.py",
    "schemas/rating.schema.json": "schemas/rating.schema.json",
    "schemas/target-weights.schema.json": "schemas/target-weights.schema.json",
}

#: Hand-written glue under the vendor dir — pinned as a listing so the mirror is
#: never mistaken for "only vendored files live here", but not checksummed
#: against upstream (upstream's own ``__init__`` files use absolute
#: ``fd_quant.…`` imports that cannot resolve from a vendored location).
LOCAL_FILES = ["README.md", "fd_quant/__init__.py", "fd_quant/contracts/__init__.py"]

#: Schema files ``load_schema`` must be able to find, keyed by contract kind.
SCHEMA_FILES = {"rating": "rating.schema.json", "target_weights": "target-weights.schema.json"}

#: Symbols the engine relies on. A rename upstream must break this gate, not
#: surface later as an ImportError at runtime.
REQUIRED_API = (
    "SUPPORTED_SCHEMA_VERSION",
    "WEIGHT_SUM_TOLERANCE",
    "ContractError",
    "TargetWeights",
    "check_target_weights",
    "validate",
    "validate_target_weights",
)

#: The engine spec (strategy-management delta) pins the float tolerance at 1e-9;
#: a change there silently loosens engine-side validation, so gate it here.
REQUIRED_TOLERANCE = 1e-9


def upstream_root() -> Path:
    """Where the fd-quant checkout is expected (env override wins)."""
    env = os.environ.get("FD_QUANT_ROOT")
    return Path(env).resolve() if env else (ROOT.parent / "fd-quant")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _upstream_version(fd_quant: Path) -> str:
    pyproject = fd_quant / "pyproject.toml"
    if not pyproject.is_file():
        return "unknown"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return str(data.get("project", {}).get("version", "unknown"))


def sync(fd_quant: Path) -> int:
    if not (fd_quant / "fd_quant" / "contracts" / "models.py").is_file():
        print(f"FAIL: no fd-quant contract at {fd_quant}", file=sys.stderr)
        return 2
    entries = []
    for rel, upstream_rel in VENDORED_FILES.items():
        src = fd_quant / upstream_rel
        dst = VENDOR_DIR / rel
        if not src.is_file():
            print(f"FAIL: upstream file missing: {src}", file=sys.stderr)
            return 2
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)  # byte-identical, no newline translation
        entries.append(
            {
                "vendored": rel,
                "upstream": upstream_rel,
                "bytes": dst.stat().st_size,
                "sha256": sha256_file(dst),
            }
        )
        print(f"synced  {rel}  <-  {upstream_rel}  ({dst.stat().st_size} B)")

    _write_glue()
    pin = {
        "note": (
            "Byte-identical mirror of the fd_quant contract layer. Do not hand-edit "
            "vendored files: run scripts/check_contract_drift.py --sync."
        ),
        "vendored_import_path": "fd_paper_trading._vendor.fd_quant",
        "upstream_package": "fd-quant",
        "upstream_version": _upstream_version(fd_quant),
        "upstream_paths_hint": "fd-quant/{fd_quant/contracts/models.py,schemas/*.json}",
        "pinned_at": __import__("datetime").date.today().isoformat(),
        "required_api": list(REQUIRED_API),
        "required_weight_sum_tolerance": REQUIRED_TOLERANCE,
        "local_files_not_vendored": LOCAL_FILES,
        "files": entries,
    }
    PIN_FILE.write_text(json.dumps(pin, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"pinned  {PIN_FILE.relative_to(ROOT)}")
    return 0


_GLUE_INITS = {
    "__init__.py": (
        '"""Vendored third-party/upstream code. See README.md and VENDOR.json."""\n'
    ),
    "fd_quant/__init__.py": (
        '"""Vendored fd_quant contract layer (mirror; see ../VENDOR.json).\n'
        "\n"
        "Upstream's ``fd_quant/__init__.py`` re-exports the same names via absolute\n"
        "``fd_quant.…`` imports; this glue uses relative imports so the mirror\n"
        "resolves under ``fd_paper_trading._vendor``. The exported set is upstream's\n"
        "(incl. ``check_rating`` / ``check_target_weights`` / ``load_schema`` and the\n"
        "``SUPPORTED_SCHEMA_VERSION`` / ``WEIGHT_SUM_TOLERANCE`` constants).\n"
        '"""\n'
        "from .contracts.models import (  # noqa: F401\n"
        "    SUPPORTED_SCHEMA_VERSION,\n"
        "    WEIGHT_SUM_TOLERANCE,\n"
        "    ContractError,\n"
        "    Evidence,\n"
        "    Producer,\n"
        "    Rating,\n"
        "    TargetWeights,\n"
        "    check_rating,\n"
        "    check_target_weights,\n"
        "    load_schema,\n"
        "    validate,\n"
        "    validate_rating,\n"
        "    validate_target_weights,\n"
        ")\n"
        "from .contracts.unsourced import unsourced_fields  # noqa: F401\n"
        "\n"
        "__all__ = [\n"
        '    "SUPPORTED_SCHEMA_VERSION",\n'
        '    "WEIGHT_SUM_TOLERANCE",\n'
        '    "ContractError",\n'
        '    "Evidence",\n'
        '    "Producer",\n'
        '    "Rating",\n'
        '    "TargetWeights",\n'
        '    "check_rating",\n'
        '    "check_target_weights",\n'
        '    "load_schema",\n'
        '    "unsourced_fields",\n'
        '    "validate",\n'
        '    "validate_rating",\n'
        '    "validate_target_weights",\n'
        "]\n"
    ),
    "fd_quant/contracts/__init__.py": (
        '"""Vendored fd_quant.contracts — relative imports so the mirror resolves."""\n'
        "from .models import (  # noqa: F401\n"
        "    ContractError,\n"
        "    Evidence,\n"
        "    Producer,\n"
        "    Rating,\n"
        "    TargetWeights,\n"
        "    check_rating,\n"
        "    check_target_weights,\n"
        "    load_schema,\n"
        "    validate,\n"
        "    validate_rating,\n"
        "    validate_target_weights,\n"
        ")\n"
        "from .unsourced import unsourced_fields  # noqa: F401\n"
    ),
}

_GLUE_README = """# Vendored: fd_quant contract layer

**Do not edit anything in this directory by hand**, except `README.md`.

`fd_quant/contracts/models.py` and `schemas/*.json` are **byte-identical copies**
of their upstream counterparts in the `fd-quant` repository root, pinned by
sha256 in `VENDOR.json`.

## Why a copy instead of a dependency

`fd-quant` has no distribution channel today: it is not published to an index and
is not even a git repository, while this repo installs with `uv sync --frozen`
against a committed lockfile and builds an image that copies only this
repository. A path dependency (`../fd-quant`) cannot resolve in CI or in the
image, so a mirror is the only honest option that keeps the engine's weight rules
identical to the contract's.

This is a **distribution workaround, not a re-implementation**. The rules are
not restated here: the identical file is what runs, so the engine cannot drift
from the contract by transcription error. Switching to a real package dependency
is an explicit deliverable in
`openspec/changes/strategy-weight-mode/tasks.md` (task 1.2).

## Layout is load-bearing

Upstream's `models.py` resolves its JSON Schemas as
`Path(__file__).parents[2] / "schemas"`, so the mirror reproduces upstream's
repository layout exactly: `<vendor>/fd_quant/contracts/models.py` with
`<vendor>/schemas/` as a sibling of the `fd_quant` package. Moving these files
to any other depth passes the checksum and silently breaks `load_schema`; the
drift gate asserts reachability to catch that.

## Commands

```bash
python3 scripts/check_contract_drift.py             # verify pin + upstream drift
python3 scripts/check_contract_drift.py --sync      # adopt upstream (author action)
python3 scripts/check_contract_drift.py --selfcheck # prove the gate can fail
```

Import it as `fd_paper_trading._vendor.fd_quant` (never as top-level `fd_quant`,
so a future real install of the package cannot be shadowed).
"""


def _write_glue() -> None:
    """Write the hand-written glue files (relative imports; see module docstring)."""
    for rel, text in _GLUE_INITS.items():
        path = VENDOR_DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (VENDOR_DIR / "README.md").write_text(_GLUE_README, encoding="utf-8")


def _module_api(path: Path) -> tuple[set[str], float | None]:
    """Read the vendored module's public surface without importing it (text scan).

    Importing would execute upstream code inside this gate; a text scan keeps the
    gate independent of the thing it checks. The functional comparison lives in
    tests/unit/test_vendor_parity.py.
    """
    text = path.read_text(encoding="utf-8")
    names = set(re.findall(r"^(?:def|class)\s+(\w+)", text, re.M))
    names |= set(re.findall(r"^(\w+)\s*=", text, re.M))
    m = re.search(r"^WEIGHT_SUM_TOLERANCE\s*=\s*([0-9.eE+-]+)", text, re.M)
    return names, (float(m.group(1)) if m else None)


def check(vendor_dir: Path, fd_quant: Path | None, quiet: bool = False) -> list[str]:
    """Return a list of problems (empty = clean). Pure function for --selfcheck."""
    problems: list[str] = []
    pin_file = vendor_dir / "VENDOR.json"
    if not pin_file.is_file():
        return [f"pin file missing: {pin_file} (run --sync)"]
    try:
        pin = json.loads(pin_file.read_text(encoding="utf-8"))
    except ValueError as exc:
        return [f"pin file unreadable: {exc}"]

    entries = pin.get("files") or []
    if not entries:
        problems.append("pin file lists no vendored files")
    for entry in entries:
        vendored = vendor_dir / entry["vendored"]
        if not vendored.is_file():
            problems.append(f"vendored file missing: {entry['vendored']}")
            continue
        got = sha256_file(vendored)
        if got != entry["sha256"]:
            problems.append(
                f"vendored copy was edited by hand: {entry['vendored']} "
                f"sha256 {got} != pinned {entry['sha256']} "
                f"(restore it with --sync, never edit it in place)"
            )

    models = vendor_dir / "fd_quant" / "contracts" / "models.py"
    if models.is_file():
        names, tolerance = _module_api(models)
        missing = [s for s in REQUIRED_API if s not in names]
        if missing:
            problems.append(
                "vendored contract lost required API: " + ", ".join(missing)
            )
        if tolerance is None:
            problems.append("WEIGHT_SUM_TOLERANCE not found in vendored contract")
        elif abs(tolerance - REQUIRED_TOLERANCE) > 0:
            problems.append(
                f"WEIGHT_SUM_TOLERANCE is {tolerance:g} but the engine spec pins "
                f"{REQUIRED_TOLERANCE:g}"
            )
        # The byte-identical module resolves schemas as parents[2]/"schemas".
        # Assert that from the vendored depth it lands on the vendored schemas —
        # a mirror at the wrong depth would pass sha256 and still lose load_schema.
        resolved = models.resolve().parents[2] / "schemas"
        for kind, name in SCHEMA_FILES.items():
            if not (resolved / name).is_file():
                problems.append(
                    f"vendored layout broken: {kind} schema not reachable at "
                    f"{resolved / name} (the copy must live at "
                    f"<vendor>/fd_quant/contracts/ with <vendor>/schemas/ beside it)"
                )
        for rel in LOCAL_FILES:
            if not (vendor_dir / rel).is_file():
                problems.append(f"vendor glue file missing: {rel} (run --sync)")

    if fd_quant is None or not (fd_quant / "fd_quant").is_dir():
        if not quiet:
            print(
                "WARN: upstream fd-quant checkout not found — byte drift against upstream "
                "was NOT checked (the pinned copy's integrity was). Set FD_QUANT_ROOT or "
                "check out fd-quant next to this repo for the full gate.",
                file=sys.stderr,
            )
        return problems

    for entry in entries:
        upstream = fd_quant / entry["upstream"]
        if not upstream.is_file():
            problems.append(f"upstream file missing: {entry['upstream']}")
            continue
        up_sha = sha256_file(upstream)
        if up_sha != entry["sha256"]:
            problems.append(
                f"DRIFT: upstream {entry['upstream']} changed "
                f"(upstream sha256 {up_sha} != pin {entry['sha256']}) — review the "
                f"upstream change, then run --sync to adopt it"
            )
    return problems


def selfcheck() -> int:
    """Corrupt a throwaway copy of the vendor dir and assert the gate fires."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_vendor = Path(tmp) / "_vendor"
        shutil.copytree(VENDOR_DIR, tmp_vendor)
        target = tmp_vendor / "fd_quant" / "contracts" / "models.py"
        text = target.read_text(encoding="utf-8")
        if "WEIGHT_SUM_TOLERANCE = 1e-9" not in text:
            print("FAIL: selfcheck fixture assumption broken (tolerance line not found)")
            return 1
        # (a) hand-edit detection
        target.write_text(text.replace("1e-9", "1e-6", 1), encoding="utf-8")
        edited = check(tmp_vendor, None, quiet=True)
        if not any("edited by hand" in p or "TOLERANCE" in p for p in edited):
            print(f"FAIL: selfcheck — a hand-edited copy was not caught: {edited}")
            return 1
        # (b) layout detection: the same bytes at the wrong depth must be caught
        #     (checksum alone would pass; load_schema would break at runtime).
        target.write_text(text, encoding="utf-8")
        shutil.move(str(tmp_vendor / "schemas"), str(tmp_vendor / "fd_quant" / "schemas"))
        misplaced = check(tmp_vendor, None, quiet=True)
        if not any("layout broken" in p for p in misplaced):
            print(f"FAIL: selfcheck — a misplaced mirror was not caught: {misplaced}")
            return 1
        shutil.move(str(tmp_vendor / "fd_quant" / "schemas"), str(tmp_vendor / "schemas"))
        # (c) drift detection: a fake upstream that mirrors the pin except for one
        #     file must be reported as DRIFT.
        pin = json.loads((tmp_vendor / "VENDOR.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as up_tmp:
            up = Path(up_tmp)
            (up / "fd_quant").mkdir()
            for entry in pin["files"]:
                f = up / entry["upstream"]
                f.parent.mkdir(parents=True, exist_ok=True)
                if entry["vendored"].endswith("models.py"):
                    f.write_text("# upstream moved on\n", encoding="utf-8")
                else:
                    shutil.copyfile(tmp_vendor / entry["vendored"], f)
            drifted = check(tmp_vendor, up, quiet=True)
            if not any("DRIFT" in p for p in drifted):
                print(f"FAIL: selfcheck — upstream drift was not caught: {drifted}")
                return 1

    print("OK: selfcheck — hand-edits, misplaced mirrors and upstream drift are all caught")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sync", action="store_true", help="re-copy from upstream and rewrite the pin")
    parser.add_argument("--check", action="store_true", help="verify pin + upstream drift (default)")
    parser.add_argument("--selfcheck", action="store_true", help="reverse self-check of this gate")
    parser.add_argument(
        "--require-upstream",
        action="store_true",
        help="exit 2 when no fd-quant checkout is available (for release gates)",
    )
    args = parser.parse_args(argv)

    fd_quant = upstream_root()
    if args.sync:
        return sync(fd_quant)
    if args.selfcheck:
        return selfcheck()

    problems = check(VENDOR_DIR, fd_quant)
    if problems:
        for p in problems:
            print(f"FAIL: {p}")
        return 1
    if args.require_upstream and not (fd_quant / "fd_quant").is_dir():
        print(f"FAIL: --require-upstream but no checkout at {fd_quant}")
        return 2
    pin = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    upstream_state = "checked" if (fd_quant / "fd_quant").is_dir() else "NOT checked (no checkout)"
    print(
        f"OK: vendored contract matches pin ({len(pin['files'])} files, "
        f"fd-quant {pin['upstream_version']}); upstream drift: {upstream_state}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
