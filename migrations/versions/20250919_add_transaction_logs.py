"""Add transaction_logs table

Revision ID: 20250919_add_transaction_logs
Revises: <previous_revision_id>
Create Date: 2025-09-19 12:40:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250919_add_transaction_logs'
down_revision = '<previous_revision_id>'  # replace with the last migration's revision id
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'transaction_logs',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('tx_ref', sa.String(64), nullable=False, unique=True),
        sa.Column('tg_id', sa.BigInteger, nullable=False),
        sa.Column('amount', sa.Integer, nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table('transaction_logs')
