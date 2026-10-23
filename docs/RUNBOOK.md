# ApexChainx Backend – Docker Runbook

## Prerequisites

- Docker Engine 24+ and Docker Compose v2 (or `docker compose` plugin)
- An `.env` file in the project root (copy from `.env.example`)

## Build & Run

```bash
# Build and start all services in the background
docker compose up --build -d

# Follow logs
docker compose logs -f

# Stop everything
docker compose down

# Stop and remove volumes (destroys database data)
docker compose down -v
```

### Service URLs

| Service   | URL                                    |
|-----------|----------------------------------------|
| API       | http://localhost:8000                  |
| API docs  | http://localhost:8000/docs             |
| Postgres  | localhost:5432 (via host, if exposed)  |
| Redis     | localhost:6379 (via host, if exposed)  |

## Configuration

### Required environment variables

| Variable         | Description                        | Example                                                       |
|------------------|------------------------------------|---------------------------------------------------------------|
| `DATABASE_URL`   | Postgres connection string         | `postgresql://postgres:password@postgres:5432/apexchainx`     |
| `CELERY_BROKER_URL` | Redis URL for Celery broker     | `redis://redis:6379/0`                                        |
| `CELERY_RESULT_BACKEND` | Redis URL for Celery results | `redis://redis:6379/0`                                        |
| `SECRET_KEY`     | Application secret key             | `openssl rand -hex 32`                                        |
| `JWT_SECRET_KEY` | JWT signing key                    | `openssl rand -hex 32`                                        |

**Important:** Inside Docker Compose, service hostnames (`postgres`, `redis`) must be used instead of `localhost`. The compose file overrides `DATABASE_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND` automatically to point at the correct containers.

### Copy `.env.example` and customise

```bash
cp .env.example .env
# Edit .env with your secrets and settings
```

## Running Migrations

```bash
# Run Alembic migrations against the running database
docker compose exec web alembic upgrade head

# Check current migration status
docker compose exec web alembic current

# Create a new migration (requires write access to alembic/versions)
docker compose exec web alembic revision --autogenerate -m "description"
```

## Health Checks

All endpoints return JSON.

| Endpoint               | Purpose              | Expected response                  |
|------------------------|----------------------|------------------------------------|
| `GET /health`          | Legacy health check  | `{"status": "ok"}`                 |
| `GET /health/liveness` | Container liveness   | `{"status": "ok", "timestamp": …}` |
| `GET /health/readiness`| Readiness + deps     | `{"status": "ok", "dependencies": …}` |

The Docker `HEALTHCHECK` uses `/health/liveness` every 30 s.

## Service Management

### Scale workers

```bash
docker compose up -d --scale worker=3
```

### Run one-off commands

```bash
# Open a Python shell in the web container
docker compose run --rm web python

# Run a specific management command
docker compose run --rm web alembic upgrade head
```

## Troubleshooting

### Container exits immediately

1. Check logs: `docker compose logs web`
2. Verify `.env` exists and has required values
3. Ensure `DATABASE_URL` uses `postgres` as the hostname (not `localhost`)

### Database connection refused

1. Verify Postgres is healthy: `docker compose ps postgres`
2. Check Postgres logs: `docker compose logs postgres`
3. Ensure `POSTGRES_USER` / `POSTGRES_PASSWORD` in `.env` match the values in `services.postgres.environment`

### Read-only filesystem errors

The web, worker, and beat containers run with `read_only: true`. A tmpfs volume is mounted at `/tmp` for temporary writes. If an application component needs to write elsewhere, ensure that path is either:

- Mounted as a tmpfs volume
- Mounted from a persistent volume
- Configured to use `/tmp` via an environment variable

### Permission errors on bind mounts

If you bind-mount the project directory (e.g., for development), the `appuser` (uid 1000) inside the container must have read/write access to the relevant files.

### Celery tasks not executing

1. Verify Redis is healthy: `docker compose ps redis`
2. Check worker logs: `docker compose logs worker`
3. Ensure `beat` is running (schedules periodic tasks): `docker compose ps beat`
4. Verify `CELERY_BROKER_URL` points to `redis://redis:6379/0`

---

## Incident Response

This section covers on-call triage for the six most common production failure modes.
Each scenario follows the same structure: **Symptoms → Triage → Mitigation → Follow-up**.

> **General principles**
> - Always check `/health/readiness` first — it surfaces dependency failures in a single call.
> - Open an incident channel / war-room ticket before you start changing things.
> - Prefer read-only commands during triage; escalate to mutations only when you understand the blast radius.
> - Record every command run and its output in the incident ticket.

---

### Scenario 1 — PostgreSQL Down

#### Symptoms

- `GET /health/readiness` returns `503` with `"database": "unhealthy"` or connection-refused error.
- API endpoints that touch the DB return `500` or `503`.
- `docker compose logs web` shows repeated `psycopg2.OperationalError` or `sqlalchemy.exc.OperationalError`.
- Alembic migrations fail on startup.

#### Triage

```bash
# 1. Check container / pod status
docker compose ps postgres                          # Docker
kubectl get pods -l app=postgres -n <namespace>     # Kubernetes

# 2. Inspect logs for the Postgres process
docker compose logs --tail=100 postgres
kubectl logs -l app=postgres -n <namespace> --tail=100

# 3. Attempt a direct connection to confirm credentials are correct
docker compose exec postgres psql -U postgres -c "SELECT 1;"

# 4. Check disk space — Postgres stops accepting writes when the volume is full
docker compose exec postgres df -h /var/lib/postgresql/data
kubectl exec -it <postgres-pod> -- df -h /var/lib/postgresql/data

# 5. Check for lock contention or runaway queries
docker compose exec postgres psql -U postgres -c \
  "SELECT pid, now()-query_start AS duration, query, state
   FROM pg_stat_activity
   WHERE state != 'idle'
   ORDER BY duration DESC LIMIT 20;"
```

#### Mitigation

```bash
# Restart the Postgres container (Docker)
docker compose restart postgres

# Force-kill a blocking query (use PID from triage step 5)
docker compose exec postgres psql -U postgres -c "SELECT pg_terminate_backend(<pid>);"

# Kubernetes — rolling restart
kubectl rollout restart deployment/postgres -n <namespace>

# If the volume is full: free space or expand PVC, then restart
kubectl edit pvc postgres-data -n <namespace>   # increase storage request
```

After Postgres recovers, restart the API workers so SQLAlchemy connection pools are re-established:

```bash
docker compose restart web worker beat
kubectl rollout restart deployment/apexchainx-backend -n <namespace>
```

#### Follow-up

- Add or tune a Postgres disk-usage alert (threshold: 80 %).
- Review `pg_stat_bgwriter` for checkpoint pressure indicating the need for tuning.
- Confirm migrations are at head: `alembic current`.

---

### Scenario 2 — Redis Down

#### Symptoms

- Celery tasks queue but never execute; `CELERY_TASK_ALWAYS_EAGER=false`.
- Webhook deliveries stop; no retry attempts in `docker compose logs worker`.
- SLA dispute notifications silently dropped.
- `GET /health/readiness` shows `"broker": "unhealthy"` (if readiness probe checks Redis).
- `docker compose logs worker` shows `kombu.exceptions.OperationalError` or `redis.exceptions.ConnectionError`.

#### Triage

```bash
# 1. Check container status
docker compose ps redis
kubectl get pods -l app=redis -n <namespace>

# 2. Logs
docker compose logs --tail=100 redis
kubectl logs -l app=redis -n <namespace> --tail=100

# 3. Ping Redis directly
docker compose exec redis redis-cli ping          # should return PONG

# 4. Check memory usage — Redis will start evicting keys when maxmemory is hit
docker compose exec redis redis-cli info memory | grep used_memory_human

# 5. Check connected clients and rejected connections
docker compose exec redis redis-cli info clients
docker compose exec redis redis-cli info stats | grep rejected_conn
```

#### Mitigation

```bash
# Restart Redis (Docker)
docker compose restart redis

# Kubernetes rolling restart
kubectl rollout restart deployment/redis -n <namespace>

# After Redis recovers, restart Celery workers so they reconnect
docker compose restart worker beat
kubectl rollout restart deployment/celery-worker -n <namespace>
kubectl rollout restart deployment/celery-beat -n <namespace>

# If maxmemory was hit and wrong eviction policy is in place, flush stale keys
# (only safe for Celery result backend data, NOT broker queue data)
docker compose exec redis redis-cli --scan --pattern "celery-task-meta-*" \
  | xargs docker compose exec -T redis redis-cli del
```

To verify workers reconnected and are processing:

```bash
docker compose exec web python -c "
from app.tasks.celery_app import celery_app
inspect = celery_app.control.inspect()
print('Active workers:', inspect.active())
print('Reserved tasks:', inspect.reserved())
"
```

#### Follow-up

- Set `maxmemory` and `maxmemory-policy allkeys-lru` in Redis config to prevent OOM evictions silently dropping broker messages.
- Add a Redis memory alert at 70 % of `maxmemory`.
- Consider Redis Sentinel or Cluster for HA if Redis is on the critical path.

---

### Scenario 3 — Celery Workers Stuck

#### Symptoms

- Tasks are enqueued (visible in Redis) but not being processed.
- `docker compose logs worker` shows no recent task execution or is stuck on a single long-running task.
- `/api/v1/sla/disputes` resolution notifications are delayed.
- Webhook retries have stopped but the queue is not empty.

#### Triage

```bash
# 1. List active, reserved, and scheduled tasks
docker compose exec web python -c "
from app.tasks.celery_app import celery_app
i = celery_app.control.inspect()
print('Active:', i.active())
print('Reserved:', i.reserved())
print('Scheduled:', i.scheduled())
print('Stats:', i.stats())
"

# 2. Check queue depth directly in Redis
docker compose exec redis redis-cli llen celery          # default queue
docker compose exec redis redis-cli llen webhooks        # webhook queue (if separate)

# 3. Look for stuck / zombie worker processes
docker compose exec worker ps aux | grep celery

# 4. Check worker logs for tracebacks or hung tasks
docker compose logs --tail=200 worker
```

#### Mitigation

```bash
# Soft shutdown — workers finish current tasks then exit
docker compose exec worker celery -A app.tasks.celery_app control shutdown

# Hard restart if soft shutdown hangs (waits 30 s, then kills)
docker compose restart worker beat

# Kubernetes
kubectl rollout restart deployment/celery-worker -n <namespace>
kubectl rollout restart deployment/celery-beat -n <namespace>

# Revoke a specific stuck task (get task ID from inspect.active())
docker compose exec web python -c "
from app.tasks.celery_app import celery_app
celery_app.control.revoke('<task-id>', terminate=True, signal='SIGKILL')
"

# If the queue is poisoned with undeserializable tasks, purge it
# WARNING: this discards all queued tasks
docker compose exec worker celery -A app.tasks.celery_app purge -f
```

#### Follow-up

- Review task timeout settings (`task_soft_time_limit`, `task_time_limit` in `celery_app.py`).
- Add Flower or Prometheus Celery exporter for visibility into task throughput.
- Investigate the root cause of the stuck task (external API timeout, DB deadlock, etc.).

---

### Scenario 4 — Webhook Dead-Letter Queue (DLQ) Spike

#### Symptoms

- A large backlog of failed webhook deliveries accumulates.
- Consumer systems report missing or delayed events.
- `docker compose logs worker` shows repeated `HTTPStatusError` (4xx/5xx from the target endpoint) or `httpx.ConnectError`.
- The webhook retry counter in the database is at or near maximum retries for many records.

#### Triage

```bash
# 1. Check the current size of the webhook retry queue
docker compose exec redis redis-cli llen webhooks   # adjust queue name as configured

# 2. Query the database for failed / max-retry webhooks
docker compose exec web python -c "
from app.db.session import SessionLocal
from app.models.webhook import WebhookDelivery  # adjust import path
db = SessionLocal()
try:
    from sqlalchemy import func
    rows = db.execute(
        'SELECT status, COUNT(*) FROM webhook_deliveries GROUP BY status'
    ).fetchall()
    for r in rows: print(dict(r))
finally:
    db.close()
"

# 3. Inspect the target endpoint health from the worker network
docker compose exec worker curl -sv <target-webhook-url>

# 4. Check for signature mismatch errors in worker logs
docker compose logs worker | grep -i "signature\|401\|403\|webhook"
```

#### Mitigation

```bash
# If the target is temporarily unreachable, pause delivery and let tasks expire gracefully
# Set a short circuit-break by raising max_retries temporarily via env var if supported

# Once the target is healthy, trigger a re-queue of stuck deliveries
# (run in web container where app context is available)
docker compose exec web python -c "
import asyncio
from app.tasks.webhook_tasks import retry_failed_webhooks
asyncio.run(retry_failed_webhooks())    # adjust to actual task name
"

# Or kick it via Celery directly
docker compose exec web python -c "
from app.tasks.webhook_tasks import deliver_webhook
# Re-enqueue specific delivery IDs identified in triage step 2
deliver_webhook.delay(<delivery_id>)
"

# If the consumer's secret rotated and signatures are failing, update the webhook secret
# in the database and notify the consumer of the new secret
```

#### Follow-up

- Add a DLQ alert: alert when `webhook_deliveries` with `status='failed'` exceeds a threshold (e.g., 50 records).
- Review and test exponential back-off and jitter configuration in `webhook_tasks.py`.
- Consider a dead-letter queue in Redis that holds permanently failed deliveries for manual inspection rather than silently discarding them.

---

### Scenario 5 — Contract Adapter / Soroban Integration Down

#### Symptoms

- `POST /api/v1/payments` or SLA settlement calls return `500` or `502`.
- Logs show `stellar_sdk` exceptions: `ConnectionError`, `BadResponseError`, or `TransactionSubmissionFailed`.
- SLA outcomes are computed but the on-chain settlement step fails.
- When `CONTRACT_EXECUTION_MODE=contract`, the entire settlement path is blocked.

#### Triage

```bash
# 1. Verify the configured Stellar network and execution mode
docker compose exec web env | grep -E 'CONTRACT_EXECUTION_MODE|STELLAR_NETWORK'

# 2. Test Stellar Horizon connectivity
docker compose exec web python -c "
from stellar_sdk import Server
s = Server(horizon_url='https://horizon-testnet.stellar.org')  # or mainnet
print(s.root().call())
"

# 3. Check the Stellar network status
# Testnet:  https://dashboard.stellar.org
# Mainnet:  https://dashboard.stellar.org (toggle network)

# 4. Verify the pool account is funded (testnet)
docker compose exec web python -c "
from stellar_sdk import Server
import os
s = Server(horizon_url='https://horizon-testnet.stellar.org')
acct = s.accounts().account_id(os.environ['STELLAR_POOL_SECRET_KEY'][:56]).call()  # uses public key prefix
print('Balances:', acct['balances'])
"

# 5. Look for contract-specific errors
docker compose logs web | grep -i "stellar\|soroban\|contract\|payment"
```

#### Mitigation

```bash
# Immediate: fall back to local adapter to unblock SLA processing
# In .env or environment:
#   CONTRACT_EXECUTION_MODE=local
# Then restart:
docker compose restart web worker

# On Kubernetes:
kubectl set env deployment/apexchainx-backend CONTRACT_EXECUTION_MODE=local -n <namespace>

# If the pool account is unfunded (testnet):
docker compose exec web python -c "
from stellar_sdk import Keypair
from stellar_sdk.exceptions import NotFoundError
import requests, os
kp = Keypair.from_secret(os.environ['STELLAR_POOL_SECRET_KEY'])
requests.get(f'https://friendbot.stellar.org?addr={kp.public_key}')
print('Funded:', kp.public_key)
"

# Once Stellar / Soroban is confirmed healthy, re-enable contract mode:
# CONTRACT_EXECUTION_MODE=contract
# Restart services, then replay any failed settlement tasks.
```

#### Follow-up

- Add a synthetic monitor that pings Horizon and alerts if latency exceeds 5 s or response is non-200.
- Set up an automated fallback so the app automatically degrades to `local` mode when Horizon is unreachable.
- Review error handling in the Soroban adapter to ensure partial failures are retried rather than silently dropped.

---

### Scenario 6 — Secret Leak

#### Symptoms

- A secret (JWT key, Stellar private key, database password, API token) has been committed to the repository, logged in plaintext, or exposed via an API response.
- A git-secrets, truffleHog, or Semgrep scan flags a secret in the codebase.
- An external party reports a credential exposed in a public channel.

#### Triage

```bash
# 1. Identify the exact secret(s) and where they appear
git log --all --full-history -- '*.env' '*.key' '*.pem'
git log -p --all -S '<partial-secret-string>' | head -100

# 2. Determine whether the secret is currently live / in use
# Check if the committed value matches the value currently in .env / secrets manager

# 3. Check if the repo is public and for how long the secret was exposed
git log --oneline | head -20   # estimate exposure window

# 4. Scan for the secret in logs and exported data
docker compose logs web worker | grep -i "secret\|token\|password\|key"
```

#### Mitigation — Immediate (within minutes)

> These steps must be completed **before** any other work. Speed matters.

```bash
# Step 1: Rotate the leaked secret immediately
# --- JWT secret ---
# Generate a new secret and update .env / secrets manager
openssl rand -hex 32   # new JWT_SECRET_KEY

# --- Stellar private key ---
# Generate a new keypair and fund it; revoke the old key by removing trust
# from any contracts or operations that referenced it

# --- Database password ---
# Change the password in Postgres and update all services
docker compose exec postgres psql -U postgres -c \
  "ALTER USER apexchainx PASSWORD '<new-strong-password>';"

# --- Third-party API token ---
# Revoke in the provider's dashboard immediately; re-issue a new token

# Step 2: Restart all services with the new secret
docker compose restart web worker beat

# Step 3: Invalidate all outstanding JWT sessions (force re-login)
# If using token families, increment the family version or flush the token store
docker compose exec web python -c "
# Adjust to actual token invalidation mechanism
from app.core.settings import get_settings
print('Rotate token_family version or flush token store here')
"
```

#### Mitigation — Repository Cleanup

> Only do this **after** rotating the secret. Rewriting history does not un-expose a secret that has already been seen.

```bash
# Remove the secret from git history using git filter-repo (preferred over BFG)
pip install git-filter-repo
git filter-repo --path-glob '*.env' --invert-paths   # if it was a full env file

# Or redact specific string patterns
git filter-repo --replace-text <(echo '<leaked-secret>==>REDACTED')

# Force-push to all branches (coordinate with team first)
git push origin --force --all
git push origin --force --tags
```

#### Follow-up

- File a private security advisory in the GitHub Security tab.
- Add the secret pattern to `.gitignore`, `pre-commit` hooks, and Semgrep / git-secrets rules.
- Notify any downstream systems that may have cached the old credentials.
- Conduct a post-incident review: how did the secret enter the codebase, and what process change prevents recurrence?
- Consider adopting [GitHub Secret Scanning](https://docs.github.com/en/code-security/secret-scanning) push protection to block commits containing secrets.

---

## Quick-Reference Severity Matrix

| Scenario | Severity | Typical MTTR | Auto-recovers? |
|----------|----------|--------------|----------------|
| PostgreSQL down | P1 – Critical | 5–15 min | No |
| Redis down | P2 – High | 5–10 min | Partial (eager mode) |
| Celery workers stuck | P2 – High | 10–20 min | No |
| Webhook DLQ spike | P3 – Medium | 30–60 min | Partial (retries) |
| Contract adapter down | P2 – High | 5–30 min | Yes (local fallback) |
| Secret leak | P1 – Critical | Immediate rotation + 1–2 hrs cleanup | No |
