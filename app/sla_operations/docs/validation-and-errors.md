# Validation and Error Handling Guide

This guide documents the validation patterns and error/result handling conventions used by modules in the SLA operations workspace (`app/sla_operations/`). It is intended for contributors implementing isolated workspace modules and workflows.

> **Scope note:** This document is additive and describes conventions only. Workspace modules must **not** modify core runtime components (routers, services, repositories, ORM models, migrations, or core exception handling). All references to core modules below are **read-only integration context**.

---

## 1. Workspace Layout Overview

The workspace package is organized as follows:

```
app/sla_operations/
├── api/
│   ├── error_mapping.py      # Maps workspace errors to HTTP responses
│   ├── request_parsing.py    # Request payload parsing helpers
│   └── responses.py          # Response envelope shapes
├── schemas/
│   ├── pagination.py         # Pagination request/response schemas
│   └── ...                   # Domain-specific Pydantic schemas
└── docs/
    └── validation-and-errors.md
```

Contributors should keep validation logic in `schemas/`, request parsing in `api/request_parsing.py`, error translation in `api/error_mapping.py`, and response construction in `api/responses.py`. This separation keeps modules testable and consistent with the rest of the repository.

---

## 2. Schema Validation (Pydantic)

All workspace input and output models live under `app/sla_operations/schemas/` and are built with Pydantic. Follow these conventions:

### 2.1 Field constraints

Prefer declarative field constraints over ad-hoc checks in route handlers:

```python
from pydantic import BaseModel, Field

class SLARuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    threshold_minutes: int = Field(..., ge=1, le=60 * 24 * 365)
    description: str | None = Field(default=None, max_length=1024)
```

- Use `Field(...)` for required fields and `Field(default=...)` for optional ones.
- Apply `min_length`/`max_length` to strings and `ge`/`le`/`gt`/`lt` to numerics.
- Keep bounds explicit and documented in the field description where non-obvious.

### 2.2 Validators

Use `@field_validator` for single-field normalization and `@model_validator` for cross-field rules. Validators should raise `ValueError` with a clear, user-safe message; Pydantic converts these into structured validation errors.

```python
from pydantic import BaseModel, field_validator, model_validator

class SLARuleCreate(BaseModel):
    threshold_minutes: int
    warning_minutes: int

    @field_validator("threshold_minutes")
    @classmethod
    def _positive_threshold(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("threshold_minutes must be positive")
        return value

    @model_validator(mode="after")
    def _warning_before_threshold(self) -> "SLARuleCreate":
        if self.warning_minutes >= self.threshold_minutes:
            raise ValueError("warning_minutes must be less than threshold_minutes")
        return self
```

Guidelines:

- Validators must be **pure** and side-effect free (no I/O, no DB access).
- Do not leak secrets or internal identifiers in validation messages.
- Normalize values (e.g., strip whitespace, lowercase enums) in validators rather than downstream.

### 2.3 Pagination schemas

`app/sla_operations/schemas/pagination.py` defines the shared pagination request/response models. Reuse these instead of redefining pagination fields per module:

- Request models carry `limit` and `offset` (or equivalent) with bounded constraints.
- Response models wrap a list of items plus pagination metadata.
- When adding a new paginated endpoint, import the shared pagination schemas rather than duplicating fields.

---

## 3. Request Parsing

`app/sla_operations/api/request_parsing.py` centralizes parsing of incoming request payloads for workspace endpoints. Use these helpers so that parsing behavior (type coercion, defaults, error shaping) stays consistent across modules.

Conventions:

- Parse into the appropriate Pydantic schema from `app/sla_operations/schemas/` before any business logic runs.
- Let validation errors propagate as structured errors; do not swallow them or convert them into generic `500` responses.
- Never log raw request bodies that may contain secrets or credentials. Log only field names or sanitized summaries when diagnostics are needed.

---

## 4. Error Mapping

`app/sla_operations/api/error_mapping.py` translates workspace-level errors into HTTP responses. This is the single place where workspace errors become status codes and payloads.

Conventions:

- Map each workspace error type to a **stable, documented error code** and an appropriate HTTP status.
- Keep error codes consistent across modules; reuse existing codes before introducing new ones.
- Error payloads must be safe to return to clients: no stack traces, no internal paths, no secrets.
- Do not alter core exception handling. Workspace mapping operates on workspace-defined errors only.

### 4.1 Integration with core exceptions (read-only)

The core application defines its exception hierarchy in `app/core/exceptions.py` and coded error definitions in `app/api/coded_errors.py`. Workspace modules may **reference** these for consistency (e.g., aligning error code naming or reusing base exception types), but must not modify them or change how the core app handles exceptions.

If a workspace module needs to surface an error through the core pipeline, do so by raising an appropriate existing core exception type — never by patching core handlers.

---

## 5. Response Shapes

`app/sla_operations/api/responses.py` defines the response envelopes used by workspace endpoints. Use these constructors to build responses so that clients see a consistent structure.

Conventions:

- Success responses wrap the domain payload in the standard envelope.
- Error responses use the mapped error code and a human-readable message.
- Keep response models typed (Pydantic) so that serialization is validated.
- Do not include secrets, tokens, or internal identifiers in response payloads.

---

## 6. Cross-Cutting Rules

1. **Consistent error codes.** Every error surfaced by a workspace module must use a documented code. Reuse before inventing.
2. **No secrets in logs.** Never log credentials, tokens, full request bodies, or PII. Log sanitized identifiers only.
3. **No core changes.** Workspace modules must not modify core endpoints, routers, services, repositories, ORM models, migrations, or exception handling. Integration with `app/core/exceptions.py` and `app/api/coded_errors.py` is read-only.
4. **Validate early.** Parse and validate inputs at the boundary (schemas + request parsing) before business logic.
5. **Test the boundaries.** Add tests covering validation failures, error mapping, and response shapes for each contributed module.

---

## 7. Related Files

- `app/sla_operations/schemas/pagination.py` — shared pagination schemas
- `app/sla_operations/api/request_parsing.py` — request parsing helpers
- `app/sla_operations/api/error_mapping.py` — workspace error → HTTP mapping
- `app/sla_operations/api/responses.py` — response envelopes
- `app/core/exceptions.py` — core exception hierarchy (read-only)
- `app/api/coded_errors.py` — core coded error definitions (read-only)