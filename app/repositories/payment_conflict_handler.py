"""Translates a unique-constraint race on sla_result_id into a clean
409 conflict instead of letting IntegrityError surface as a 500.
"""
from sqlalchemy.exc import IntegrityError


class PaymentAlreadyExists(Exception):
    def __init__(self, sla_result_id: str):
        super().__init__(f"Payment already exists for sla_result_id={sla_result_id}")
        self.sla_result_id = sla_result_id


def create_or_get_existing_payment(session, sla_result_id: str, create_fn):
    try:
        payment = create_fn()
        session.commit()
        return payment
    except IntegrityError:
        session.rollback()
        raise PaymentAlreadyExists(sla_result_id)
