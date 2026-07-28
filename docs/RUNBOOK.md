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
