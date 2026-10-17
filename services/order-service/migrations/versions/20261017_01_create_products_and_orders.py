"""Create products and orders tables.

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
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False),
        sa.CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),
        sa.CheckConstraint("stock >= 0", name="ck_products_stock_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), server_default="created", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("quantity > 0", name="ck_orders_quantity_positive"),
        sa.ForeignKeyConstraint(["product_id"], [f"{schema}.products.id"]),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
    )
    op.create_index(
        "ix_orders_user_id_created_at",
        "orders",
        ["user_id", "created_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("ix_orders_user_id_created_at", table_name="orders", schema=schema)
    op.drop_table("orders", schema=schema)
    op.drop_table("products", schema=schema)
