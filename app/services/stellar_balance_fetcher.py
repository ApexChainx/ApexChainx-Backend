"""Fetches real wallet balances from the configured Stellar network
instead of deriving fabricated values from boolean flags.
"""
from typing import Any, Dict


def fetch_wallet_balances(public_key: str, horizon_client, simulated_fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Return live balances when reachable, else an explicitly-flagged simulation."""
    try:
        account = horizon_client.accounts().account_id(public_key).call()
        balances = {b["asset_type"]: b["balance"] for b in account["balances"]}
        return {"balances": balances, "simulated": False}
    except Exception:
        return {"balances": simulated_fallback, "simulated": True}
