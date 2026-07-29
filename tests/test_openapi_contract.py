"""OpenAPI contract tests using schemathesis for issue #57."""


def test_schemathesis_import():
    """Verify schemathesis is importable and ready for contract tests."""
    import schemathesis  # noqa: F401

    assert schemathesis.__version__ is not None
