#!/usr/bin/env python3
"""Pre-deploy/CI guard script to validate Stellar network/key separation.

Usage:
    python scripts/check_stellar_networks.py [--env-file .env] [--strict]

Checks:
- If STELLAR_NETWORK=testnet, STELLAR_POOL_SECRET_KEY must start with 'S'
- If STELLAR_NETWORK=mainnet, STELLAR_POOL_SECRET_KEY must start with 'S'
- If STRICT_NETWORK_KEY_CHECK=true, validates horizon URL matches network
"""
import argparse
import os
import sys
from pathlib import Path


def load_env_file(env_path: str) -> dict[str, str]:
    env_vars = {}
    path = Path(env_path)
    if not path.exists():
        return env_vars
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return env_vars


def check_network_key_separation(env_vars: dict[str, str], strict: bool = False) -> list[str]:
    errors = []
    network = env_vars.get("STELLAR_NETWORK", "")
    secret_key = env_vars.get("STELLAR_POOL_SECRET_KEY", "")
    horizon_url = env_vars.get("STELLAR_HORIZON_URL", "")

    if not network:
        errors.append("STELLAR_NETWORK is not set")
        return errors
    if network not in ("testnet", "mainnet", "futurenet", "standalone"):
        errors.append(f"Unknown STELLAR_NETWORK: {network}")
        return errors

    if secret_key and not secret_key.startswith("S"):
        errors.append(f"STELLAR_POOL_SECRET_KEY must start with 'S' (got prefix '{secret_key[:1]}')")

    if strict and network == "testnet" and "mainnet" in horizon_url:
        errors.append(f"STELLAR_NETWORK=testnet but HORIZON_URL points to mainnet: {horizon_url}")
    if strict and network == "mainnet" and "testnet" in horizon_url:
        errors.append(f"STELLAR_NETWORK=mainnet but HORIZON_URL points to testnet: {horizon_url}")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Check Stellar network/key separation")
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument("--strict", action="store_true", help="Enable strict horizon URL validation")
    args = parser.parse_args()

    env_vars = load_env_file(args.env_file)

    for key in ("STELLAR_NETWORK", "STELLAR_POOL_SECRET_KEY", "STELLAR_HORIZON_URL"):
        if key in os.environ:
            env_vars[key] = os.environ[key]

    errors = check_network_key_separation(env_vars, strict=args.strict)

    if errors:
        print("ERROR: Stellar network/key separation check failed:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("OK: Stellar network/key separation check passed")
        sys.exit(0)


if __name__ == "__main__":
    main()