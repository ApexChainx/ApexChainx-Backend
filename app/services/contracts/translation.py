from app.models.sla import SLAResult


def translate_contract_result(raw_result: dict) -> SLAResult:
    return SLAResult(
        outage_id=raw_result["outage_id"],
        status="violated" if raw_result["status"] == "viol" else "met",
        mttr_minutes=raw_result["mttr_minutes"],
        threshold_minutes=raw_result["threshold_minutes"],
        amount=raw_result["amount"],
        payment_type="penalty" if raw_result["payment_type"] == "pen" else "reward",
        rating={
            "top": "exceptional",
            "high": "excellent",
            "good": "good",
            "poor": "poor",
        }[raw_result["rating"]],
        compute_hash=raw_result.get("compute_hash"),
    )


def build_tx_memo_from_result(sla_result: SLAResult) -> str:
    """Build a Stellar transaction memo from an SLA result for on-chain settlement (#38).

    Format: <op>:<agg>:v<hash8>
    - op: SLP (settle-penalty) or SLR (settle-reward)
    - agg: truncated outage_id (max 16 chars)
    - v<hash8>: 8-char hex prefix of SHA-256

    Returns:
        String suitable for Stellar transaction memo field (≤28 bytes).
    """
    import hashlib

    from app.services.tx_memo import TxMemo

    op = "SLP" if sla_result.payment_type == "penalty" else "SLR"
    agg = sla_result.outage_id[:16]

    # Use compute_hash if available, otherwise derive from outage_id + status
    if sla_result.compute_hash:
        content_hash = sla_result.compute_hash[:8]
    else:
        raw = f"{sla_result.outage_id}|{sla_result.status}|{sla_result.amount}"
        content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]

    memo = TxMemo(op=op, agg=agg, content_hash=content_hash)
    if memo.byte_length() > 28:
        # Truncate agg to fit if necessary
        while memo.byte_length() > 28 and len(agg) > 1:
            agg = agg[:-1]
            memo = TxMemo(op=op, agg=agg, content_hash=content_hash)

    encoded = memo.encode()

    # Audit: flag suspicious memos (#38)
    if TxMemo.is_suspicious(encoded):
        from app.services.audit_log import audit_log
        audit_log.log(
            "memo_suspicious",
            {
                "memo": encoded,
                "outage_id": sla_result.outage_id,
                "payment_type": sla_result.payment_type,
            },
        )

    return encoded
