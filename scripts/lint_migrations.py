"""Lint migration files for raw SQL via op.execute().

Usage:
    python scripts/lint_migrations.py

Exits with code 1 if any migration file contains op.execute() calls
without a "# raw-sql-allowed" marker comment.

Allowed marker:
    # raw-sql-allowed  (placed anywhere in the file, typically at the top)
"""

import re
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"
RAW_SQL_MARKER = "# raw-sql-allowed"
OP_EXECUTE_PATTERN = re.compile(r"\bop\.execute\s*\(")


def _has_marker(content: str) -> bool:
    return RAW_SQL_MARKER in content


def _lint_file(filepath: Path) -> list[str]:
    content = filepath.read_text(encoding="utf-8")
    if _has_marker(content):
        return []

    errors: list[str] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        if OP_EXECUTE_PATTERN.search(line):
            errors.append(f"{filepath.name}:{lineno}: op.execute() without # raw-sql-allowed marker")
    return errors


def main() -> int:
    all_errors: list[str] = []

    for filepath in sorted(MIGRATIONS_DIR.iterdir()):
        if filepath.suffix != ".py" or filepath.name.startswith("."):
            continue
        all_errors.extend(_lint_file(filepath))

    if all_errors:
        print("Migration lint errors found:", file=sys.stderr)
        for err in all_errors:
            print(f"  {err}", file=sys.stderr)
        print(
            "\nIf raw SQL is intentional, add '# raw-sql-allowed' to the top of the migration file.",
            file=sys.stderr,
        )
        return 1

    print("All migrations pass raw-SQL lint check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
