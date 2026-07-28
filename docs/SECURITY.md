# Security Overview

## Audit Log Immutability

The `audit_logs` table is enforced as **append-only** at the database level
to prevent tampering with audit records.

- A `BEFORE DELETE` trigger (`trg_audit_logs_append_only`) raises
  `EXCEPTION 'audit_log is append-only'` for any DELETE attempt.
- Writes should be performed via a dedicated `audit_writer` database role
  with `INSERT`-only permissions on `audit_logs`, configured through the
  `DATABASE_AUDIT_URL` environment variable.
- When `DATABASE_AUDIT_URL` is set, the `AuditLogService.log()` method
  automatically routes writes through the audit-specific connection;
  falling back to the primary `DATABASE_URL` otherwise.
- No `UPDATE` or `DELETE` privileges should be granted to any application
  role on this table. Schema migrations (ALTER TABLE) must use a separate
  privileged role.
