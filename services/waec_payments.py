# ====================================================
# services/waec_payments.py
# ==================================================
from sqlalchemy import text


async def create_pending_waec_payment(
    session,
    payment_reference: str,
    user_id: int,
    amount_paid: int,
    question_credits_added: int = 0,
    mock_sessions_added: int = 0,
):
    await session.execute(
        text("""
            insert into waec_payments (
                payment_reference,
                user_id,
                amount_paid,
                question_credits_added,
                mock_sessions_added,
                payment_status
            )
            values (
                :payment_reference,
                :user_id,
                :amount_paid,
                :question_credits_added,
                :mock_sessions_added,
                'pending'
            )
        """),
        {
            "payment_reference": payment_reference,
            "user_id": int(user_id),
            "amount_paid": int(amount_paid),
            "question_credits_added": int(question_credits_added),
            "mock_sessions_added": int(mock_sessions_added),
        },
    )
