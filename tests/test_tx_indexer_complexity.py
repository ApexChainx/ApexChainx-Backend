"""Cyclomatic-complexity guard for TxIndexer — Issue #712.

The issue asked for the indexer to be refactored to reduce cyclomatic
complexity. Refactoring without a guard drifts back within a couple of PRs, so
this test measures the modules with the standard definition and fails when a
function grows past the budget.

Metric
------
``1 +`` the number of decision points in the function body:

* ``if`` / ``elif``, ``for``, ``while``, ``except``, conditional expression
  (``a if b else c``), ``assert``, and each ``case`` of a ``match``;
* each extra operand of a boolean operator (``a and b and c`` counts 2);
* each comprehension (plus one per ``if`` clause inside it).

This is the McCabe count used by ``radon``/``mccabe``. The repo does not depend
on either tool, so the measurement is a small AST walk — no new dependency, no
network, and it runs in milliseconds.

Before this refactor the worst offenders were ``IndexedTransaction.
from_horizon_record`` at 11, ``HorizonClient.fetch_payments`` at 9 and
``TxIndexerRepository.build_query`` at 8. After splitting each into
single-purpose helpers the whole indexer sits at 6 or below, and the budget
here is 8 so there is room for a normal change without inviting the old
nested-decision style back.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

MODULES = {
    "service": REPO_ROOT / "app" / "services" / "tx_indexer.py",
    "input": REPO_ROOT / "app" / "services" / "tx_indexer_input.py",
    "repository": REPO_ROOT / "app" / "repositories" / "tx_indexer_repository.py",
    "api": REPO_ROOT / "app" / "api" / "v1" / "endpoints" / "indexer.py",
}

#: Budget for any single function in the indexer.
MAX_COMPLEXITY = 8

_DECISION_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.IfExp,
    ast.Assert,
)


def cyclomatic_complexity(node: ast.AST) -> int:
    """Return the McCabe complexity of one function node."""
    score = 1
    for child in ast.walk(node):
        if isinstance(child, _DECISION_NODES):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            score += 1 + len(child.ifs)
        elif isinstance(child, ast.Match):
            score += len(child.cases)
    return score


def functions(path: Path) -> dict[str, int]:
    """Map every function/method in *path* to its complexity."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name: cyclomatic_complexity(node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


@pytest.mark.parametrize("module", sorted(MODULES))
def test_no_function_exceeds_the_complexity_budget(module: str):
    measured = functions(MODULES[module])
    offenders = {name: score for name, score in measured.items() if score > MAX_COMPLEXITY}

    assert not offenders, f"{module} functions over budget ({MAX_COMPLEXITY}): {offenders}"


@pytest.mark.parametrize("module", sorted(MODULES))
def test_every_module_is_measurable(module: str):
    """A rename must not silently disable the guard."""
    assert functions(MODULES[module]), f"{module} has no functions to measure"


class TestRefactoredHotspots:
    """The three functions the refactor targeted stay decomposed."""

    def test_record_normalization_is_flat(self):
        assert functions(MODULES["service"])["from_horizon_record"] <= 4

    def test_horizon_fetch_is_flat(self):
        assert functions(MODULES["service"])["fetch_payments"] <= 5

    def test_query_builder_is_flat(self):
        assert functions(MODULES["repository"])["build_query"] <= 4

    def test_list_handler_stays_thin(self):
        assert functions(MODULES["api"])["list_indexed_transactions"] <= 3

    def test_normalization_helpers_exist(self):
        """The split helpers are named, testable units rather than inlined."""
        measured = functions(MODULES["service"])
        assert {"_parse_amount", "_parse_accounts", "_parse_asset_code", "_parse_operation_type"} <= set(measured)

    def test_query_helpers_exist(self):
        measured = functions(MODULES["repository"])
        assert {"_apply_equalities", "_apply_account", "_apply_bounds"} <= set(measured)
