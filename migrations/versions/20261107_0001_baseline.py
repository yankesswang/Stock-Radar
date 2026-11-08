"""Establish the ORM schema baseline.

Revision ID: 20261107_0001
Revises:
"""

from alembic import op

from backend.app.database import Base
from backend.app import models  # noqa: F401 - registers ORM metadata


revision = "20261107_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Safe for both fresh and existing local databases: SQLAlchemy creates only
    # missing tables, then Alembic records this baseline revision.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    # The baseline may be applied to a database containing user portfolio data;
    # an automatic destructive downgrade is intentionally forbidden.
    raise RuntimeError("The StockRadar baseline migration cannot be downgraded safely")
