FROM mcr.microsoft.com/devcontainers/python:3.11

# postgresql-client provides `pg_isready`, used by the devcontainer
# postCreateCommand to wait until Postgres is accepting connections.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
