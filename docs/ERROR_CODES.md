# Error Codes

All API errors are returned as RFC 7807 Problem Details.

| HTTP Status | Error Code                  | Description                                      | Retryable |
|-------------|-----------------------------|--------------------------------------------------|-----------|
| 400         | `validation_error`          | Request body or parameters failed validation     | No        |
| 401         | `unauthorized`              | Missing or invalid authentication                | No        |
| 403         | `forbidden`                 | Insufficient permissions for the resource        | No        |
| 404         | `not_found`                 | The requested resource does not exist            | No        |
| 409         | `conflict`                  | Request conflicts with the current server state  | No        |
| 413         | `payload_too_large`         | Request body exceeds the maximum allowed size    | No        |
| 422         | `unprocessable_entity`      | Semantically invalid request                     | No        |
| 429         | `rate_limited`              | Too many requests                                | Yes       |
| 500         | `transient_error`           | A temporary server-side failure                  | Yes       |
| 500         | `internal_error`            | An unexpected internal error                     | No        |

## Domain Exception Hierarchy

```
ApexException (base)
├── ApexValidationError    HTTP 400
├── ApexNotFoundError      HTTP 404
├── ApexConflictError      HTTP 409
└── ApexTransientError     HTTP 500 (retryable)
```

- Service code raises `ApexException` subclasses.
- Exception handlers in `app/main.py` translate them to JSON Problem Details responses.
- Bare `except Exception` in service code has been replaced with specific typed catches.
