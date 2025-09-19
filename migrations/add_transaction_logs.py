"""add transaction_logs table

Revision ID: 20230919_add_transaction_logs
Revises: 
Create Date: 2025-09-19

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic
revision = "20230919_add_transaction_logs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transaction_logs",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("tx_ref", sa.String, unique=True, nullable=False),
        sa.Column("user_id", sa.Integer, nullable=True),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("raw_response", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("transaction_logs")

