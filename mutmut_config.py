"""mutmut configuration for SLA calculator mutation testing.

Usage:
    mutmut run --paths-to-mutate=app/services/sla/
    mutmut results
    mutmut show <id>

Target: >70% killed mutation rate.
"""

# Paths to mutate - focus on SLA calculator domain logic
paths_to_mutate = [
    "app/services/sla/",
]

# Exclude test infrastructure from mutation
paths_to_exclude = [
    "*/test_*.py",
    "*/conftest.py",
    "*/__init__.py",
    "*/types.py",
]

# Pre-mutation test command - runs tests to establish baseline
pre_mutation = "pytest --tb=short -x"

# Post-mutation test command - individual test for targeted validation
runner = "pytest --tb=short -x"

# Timeout per mutant (seconds)
tests_timeout = 30

# Number of parallel mutation workers
num_workers = 4

# Only show surviving mutants
no_progress = False

# Surviving mutants output format
show_diffs = True

# Backend to use (pytest is default)
backend = "pytest"
