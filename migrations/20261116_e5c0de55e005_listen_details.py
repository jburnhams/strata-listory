"""enrichment: enrichment_run and enrichment_result

Revision ID: e5c0de55e005
Revises: d4c0de44e004
Create Date: 2026-11-16 17:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5c0de55e005"
down_revision: Union[str, Sequence[str], None] = "d4c0de44e004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "enrichment_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("listen_id", sa.Integer(), nullable=False),
        sa.Column("listen_changed_at", sa.DateTime(), nullable=True),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["listen_id"], ["listen.id"], name=op.f("fk_enrichment_run_listen_id_listen")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enrichment_run")),
    )
    op.create_index(op.f("ix_enrichment_run_listen_id"), "enrichment_run", ["listen_id"])
    op.create_table(
        "enrichment_result",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("ref", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("ratings_count", sa.Integer(), nullable=True),
        sa.Column("reviews_count", sa.Integer(), nullable=True),
        sa.Column("synopsis", sa.String(), nullable=True),
        sa.Column("reviews", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["enrichment_run.id"], name=op.f("fk_enrichment_result_run_id_enrichment_run"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enrichment_result")),
    )
    op.create_index(op.f("ix_enrichment_result_run_id"), "enrichment_result", ["run_id"])


def downgrade() -> None:
    op.drop_table("enrichment_result")
    op.drop_table("enrichment_run")
