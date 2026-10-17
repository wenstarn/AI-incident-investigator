"""Create users table.

Revision ID: 20261017_01
Revises:
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20261017_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    return context.config.attributes["database_schema"]


def upgrade() -> None:
    schema = _schema()
    op.execute(sa.schema.CreateSchema(schema, if_not_exists=True))
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        schema=schema,
    )


def downgrade() -> None:
    op.drop_table("users", schema=_schema())
