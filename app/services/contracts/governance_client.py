"""Soroban contract governance client.

Provides typed wrappers around the contract's governance operations:
propose_admin, accept_admin, cancel_admin_proposal, renounce_admin,
propose_operator, accept_operator.

In ``local_adapter`` mode the client returns deterministic stubs; in
``soroban_rpc`` mode it would submit real Soroban transactions (not yet
implemented).
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from app.core.config import settings


class GovernanceError(Exception):
    """Raised when a governance operation fails."""


class GovernanceNotImplementedError(GovernanceError):
    """Compatibility alias for callers expecting a more specific error."""


def _stub_tx_hash(operation: str, address: str) -> str:
    """Return a deterministic stub transaction hash for local_adapter mode."""
    raw = f"{settings.SLA_CONTRACT_ADDRESS}:{operation}:{address}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def propose_admin(new_admin_address: str) -> dict[str, Any]:
    """Initiate a two-step admin transfer.

    Returns the transaction hash and the pending admin address.
    """
    if not new_admin_address:
        raise GovernanceError("new_admin_address is required")

    if settings.CONTRACT_EXECUTION_MODE == "local_adapter":
        tx_hash = _stub_tx_hash("propose_admin", new_admin_address)
        return {
            "tx_hash": tx_hash,
            "pending_admin": new_admin_address,
            "status": "proposed",
            "contract_address": settings.SLA_CONTRACT_ADDRESS,
            "network": settings.STELLAR_NETWORK,
        }

    raise GovernanceError(
        f"soroban_rpc mode not yet implemented for propose_admin"
    )


def accept_admin() -> dict[str, Any]:
    """Complete an admin transfer (called by the proposed new admin).

    Returns the transaction hash.
    """
    if settings.CONTRACT_EXECUTION_MODE == "local_adapter":
        tx_hash = _stub_tx_hash("accept_admin", "current")
        return {
            "tx_hash": tx_hash,
            "status": "accepted",
            "contract_address": settings.SLA_CONTRACT_ADDRESS,
            "network": settings.STELLAR_NETWORK,
        }

    raise GovernanceError(
        f"soroban_rpc mode not yet implemented for accept_admin"
    )


def cancel_admin_proposal() -> dict[str, Any]:
    """Cancel a pending admin proposal."""
    if settings.CONTRACT_EXECUTION_MODE == "local_adapter":
        tx_hash = _stub_tx_hash("cancel_admin_proposal", "current")
        return {
            "tx_hash": tx_hash,
            "status": "cancelled",
            "contract_address": settings.SLA_CONTRACT_ADDRESS,
            "network": settings.STELLAR_NETWORK,
        }

    raise GovernanceError(
        f"soroban_rpc mode not yet implemented for cancel_admin_proposal"
    )


def renounce_admin() -> dict[str, Any]:
    """Renounce admin role permanently."""
    if settings.CONTRACT_EXECUTION_MODE == "local_adapter":
        tx_hash = _stub_tx_hash("renounce_admin", "current")
        return {
            "tx_hash": tx_hash,
            "status": "renounced",
            "contract_address": settings.SLA_CONTRACT_ADDRESS,
            "network": settings.STELLAR_NETWORK,
        }

    raise GovernanceError(
        f"soroban_rpc mode not yet implemented for renounce_admin"
    )


def propose_operator(new_operator_address: str) -> dict[str, Any]:
    """Initiate a two-step operator transfer.

    Returns the transaction hash and the pending operator address.
    """
    if not new_operator_address:
        raise GovernanceError("new_operator_address is required")

    if settings.CONTRACT_EXECUTION_MODE == "local_adapter":
        tx_hash = _stub_tx_hash("propose_operator", new_operator_address)
        return {
            "tx_hash": tx_hash,
            "pending_operator": new_operator_address,
            "status": "proposed",
            "contract_address": settings.SLA_CONTRACT_ADDRESS,
            "network": settings.STELLAR_NETWORK,
        }

    raise GovernanceError(
        f"soroban_rpc mode not yet implemented for propose_operator"
    )


def accept_operator() -> dict[str, Any]:
    """Complete an operator transfer (called by the proposed new operator)."""
    if settings.CONTRACT_EXECUTION_MODE == "local_adapter":
        tx_hash = _stub_tx_hash("accept_operator", "current")
        return {
            "tx_hash": tx_hash,
            "status": "accepted",
            "contract_address": settings.SLA_CONTRACT_ADDRESS,
            "network": settings.STELLAR_NETWORK,
        }

    raise GovernanceError(
        f"soroban_rpc mode not yet implemented for accept_operator"
    )
