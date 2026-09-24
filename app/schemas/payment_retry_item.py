"""Retry queue item with an integer amount, matching the BIGINT column
instead of a float that can lose precision above 2**53.
"""
from pydantic import BaseModel, field_validator


class PaymentRetryQueueItem(BaseModel):
    payment_id: str
    amount: int

    @field_validator("amount")
    @classmethod
    def amount_must_be_whole(cls, value: int) -> int:
        if not isinstance(value, int):
            raise ValueError("amount must be an integer matching the BIGINT column")
        return value
