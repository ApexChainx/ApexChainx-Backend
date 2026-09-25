"""Lint test filenames for lookalikes and leftovers (issue #526).

``tests/`` accumulated near-identical file names -- e.g.
``test_be_205_228_236_238.py`` next to ``test_be_205_228_236_238hhh.py`` --
which makes it ambiguous which file is canonical. A truncated leftover is worse
than a duplicate: it looks like coverage, and pytest collects it.

Three checks, all offline and dependency-free:

1. **leftover noise suffix** -- a stem ending in a marker that reads as an edit
   artifact (``hhh``, ``copy``, ``old``, ``bak``, ``tmp``, ``final``, ``v2``...).
2. **lookalike pair** -- two test files whose stems normalise to the same value.
3. **empty module** -- a ``test_*.py`` with no test function and no ``Test*``
   class. A file that cannot fail is not a test.

Usage:
    python scripts/lint_test_filenames.py [tests_dir]

Exits 1 and prints every offender when a check fails.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent / "tests"

# Suffixes that read as "I edited this file and forgot to clean up".
NOISE_SUFFIXES = (
    "hhh",
    "copy",
    "copypasta",
    "old",
    "orig",
    "bak",
    "backup",
    "tmp",
    "temp",
    "final",
    "draft",
    "v2",
    "new",
    "fix",
)

TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+test_\w+", re.MULTILINE)
TEST_CLASS = re.compile(r"^\s*class\s+Test\w+", re.MULTILINE)


def strip_test_prefix(stem: str) -> str:
    return stem[len("test_") :] if stem.startswith("test_") else stem


def split_noise(stem: str) -> tuple[str, str]:
    """Split a stem into (base, trailing noise marker)."""
    for marker in sorted(NOISE_SUFFIXES, key=len, reverse=True):
        if stem.endswith("_" + marker):
            return stem[: -(len(marker) + 1)], marker
    return stem, ""


def collapse_trailing_repeats(stem: str) -> tuple[str, str]:
    """Split off a run of three or more identical trailing characters (hhh)."""
    match = re.search(r"(.)\1{2,}$", stem)
    if match:
        return stem[: match.start()], match.group(0)
    return stem, ""


def normalize(stem: str) -> str:
    base, _ = collapse_trailing_repeats(split_noise(stem)[0])
    return re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")


def collect_test_files(tests_dir: Path) -> list[Path]:
    return sorted(p for p in tests_dir.rglob("test_*.py") if p.is_file() and "__pycache__" not in p.parts)


def check_noise_suffixes(files: list[Path]) -> list[str]:
    errors = []
    for path in files:
        base, marker = split_noise(path.stem)
        repeat_base, repeat = collapse_trailing_repeats(base)
        if marker:
            errors.append(f"{path}: filename ends with noise marker {marker!r} (suggests {repeat_base or base}.py)")
        elif repeat:
            errors.append(f"{path}: filename ends with {repeat!r} (suggests {repeat_base}.py)")
    return errors


def check_lookalikes(files: list[Path]) -> list[str]:
    by_normalized: dict[str, list[Path]] = {}
    for path in files:
        by_normalized.setdefault(normalize(path.stem), []).append(path)
    errors = []
    for key, group in by_normalized.items():
        if len(group) > 1:
            names = ", ".join(p.name for p in group)
            errors.append(f"lookalike test files (same normalized stem {key!r}): {names}")
    return errors


def check_not_empty(files: list[Path]) -> list[str]:
    errors = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if not TEST_DEF.search(text) and not TEST_CLASS.search(text):
            errors.append(f"{path}: no test function and no Test* class; a file that cannot fail is not a test")
    return errors


def main(argv: list[str]) -> int:
    tests_dir = Path(argv[1]) if len(argv) > 1 else TESTS_DIR
    if not tests_dir.is_dir():
        print(f"error: {tests_dir} is not a directory", file=sys.stderr)
        return 1

    files = collect_test_files(tests_dir)
    if not files:
        print(f"No test files found under {tests_dir}.", file=sys.stderr)
        return 1

    errors = check_noise_suffixes(files) + check_lookalikes(files) + check_not_empty(files)
    if errors:
        print("Test filename lint errors found:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        print(
            "\nKeep exactly one canonical file per test subject, name it after what it "
            "tests, and delete truncated leftovers instead of editing them in place.",
            file=sys.stderr,
        )
        return 1

    print(f"All {len(files)} test filenames pass lookalike/leftover lint.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
