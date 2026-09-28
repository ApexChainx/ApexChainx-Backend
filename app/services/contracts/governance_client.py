"""Soroban contract governance client.

Provides typed wrappers around the contract's governance operations:
propose_admin, accept_admin, cancel_admin_proposal, renounce_admin,
propose_operator, accept_operator.

Safety posture (THREAT_MODEL.md F-1): there is no real Soroban execution
path here. Everything is gated behind ``settings.GOVERNANCE_ENABLED``:

- Flag off (the default): every operation raises
  ``GovernanceNotImplementedError`` instead of fabricating a success payload,
  so callers can never mistake a stub for an on-chain transfer.
- Flag on + ``local_adapter`` mode: the deterministic local stub runs and the
  response is explicitly labeled ``"simulated": true`` so it can never be
  presented as on-chain completion.
- Flag on + any other mode: ``GovernanceNotImplementedError`` — only the
  labeled local stub exists.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.core.config import settings


class GovernanceError(Exception):
    """Raised when a governance operation fails."""


class GovernanceNotImplementedError(GovernanceError):
    """Raised when a governance op cannot run (flag off / mode unsupported)."""


def _stub_tx_hash(operation: str, address: str) -> str:
    """Return a deterministic stub transaction hash for local_adapter mode."""
    raw = f"{settings.SLA_CONTRACT_ADDRESS}:{operation}:{address}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def _guard(operation: str) -> bool:
    """Check the governance gate.

    Returns True when the (labeled) local stub may run; raises
    ``GovernanceNotImplementedError`` in every other case so no caller can
    receive an unlabeled fabricated payload.
    """
    if not settings.GOVERNANCE_ENABLED:
        raise GovernanceNotImplementedError(
            f"{operation} is not available: GOVERNANCE_ENABLED is false and there is "
            "no real Soroban execution path. No transaction was submitted."
        )
    if settings.CONTRACT_EXECUTION_MODE != "local_adapter":
        raise GovernanceNotImplementedError(
            f"{operation} is not implemented for CONTRACT_EXECUTION_MODE="
            f"{settings.CONTRACT_EXECUTION_MODE!r}."
        )
    return True


def propose_admin(new_admin_address: str) -> dict[str, Any]:
    """Initiate a two-step admin transfer.

    Returns the transaction hash and the pending admin address.
    """
    if not new_admin_address:
        raise GovernanceError("new_admin_address is required")

    _guard("propose_admin")
    tx_hash = _stub_tx_hash("propose_admin", new_admin_address)
    return {
        "tx_hash": tx_hash,
        "pending_admin": new_admin_address,
        "status": "proposed",
        "contract_address": settings.SLA_CONTRACT_ADDRESS,
        "network": settings.STELLAR_NETWORK,
        "simulated": True,
    }


def accept_admin() -> dict[str, Any]:
    """Complete an admin transfer (called by the proposed new admin).

    Returns the transaction hash.
    """
    _guard("accept_admin")
    tx_hash = _stub_tx_hash("accept_admin", "current")
    return {
        "tx_hash": tx_hash,
        "status": "accepted",
        "contract_address": settings.SLA_CONTRACT_ADDRESS,
        "network": settings.STELLAR_NETWORK,
        "simulated": True,
    }


def cancel_admin_proposal() -> dict[str, Any]:
    """Cancel a pending admin proposal."""
    _guard("cancel_admin_proposal")
    tx_hash = _stub_tx_hash("cancel_admin_proposal", "current")
    return {
        "tx_hash": tx_hash,
        "status": "cancelled",
        "contract_address": settings.SLA_CONTRACT_ADDRESS,
        "network": settings.STELLAR_NETWORK,
        "simulated": True,
    }


def renounce_admin() -> dict[str, Any]:
    """Renounce admin role permanently."""
    _guard("renounce_admin")
    tx_hash = _stub_tx_hash("renounce_admin", "current")
    return {
        "tx_hash": tx_hash,
        "status": "renounced",
        "contract_address": settings.SLA_CONTRACT_ADDRESS,
        "network": settings.STELLAR_NETWORK,
        "simulated": True,
    }


def propose_operator(new_operator_address: str) -> dict[str, Any]:
    """Initiate a two-step operator transfer.

    Returns the transaction hash and the pending operator address.
    """
    if not new_operator_address:
        raise GovernanceError("new_operator_address is required")

    _guard("propose_operator")
    tx_hash = _stub_tx_hash("propose_operator", new_operator_address)
    return {
        "tx_hash": tx_hash,
        "pending_operator": new_operator_address,
        "status": "proposed",
        "contract_address": settings.SLA_CONTRACT_ADDRESS,
        "network": settings.STELLAR_NETWORK,
        "simulated": True,
    }


def accept_operator() -> dict[str, Any]:
    """Complete an operator transfer (called by the proposed new operator)."""
    _guard("accept_operator")
    tx_hash = _stub_tx_hash("accept_operator", "current")
    return {
        "tx_hash": tx_hash,
        "status": "accepted",
        "contract_address": settings.SLA_CONTRACT_ADDRESS,
        "network": settings.STELLAR_NETWORK,
        "simulated": True,
    }
