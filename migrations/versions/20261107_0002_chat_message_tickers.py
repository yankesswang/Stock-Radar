"""Add multi-ticker scope to chat messages.

Revision ID: 20261107_0002
Revises: 20261107_0001
"""

import sqlalchemy as sa
from alembic import op


revision = "20261107_0002"
down_revision = "20261107_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "chat_messages" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("chat_messages")}
    if "tickers" not in columns:
        op.add_column(
            "chat_messages",
            sa.Column("tickers", sa.String(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "chat_messages" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("chat_messages")}
    if "tickers" in columns:
        with op.batch_alter_table("chat_messages") as batch:
            batch.drop_column("tickers")
