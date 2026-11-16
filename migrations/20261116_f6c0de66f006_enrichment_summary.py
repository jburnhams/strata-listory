"""enrichment summary columns on enrichment_run

Revision ID: f6c0de66f006
Revises: e5c0de55e005
Create Date: 2026-11-16 22:12:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6c0de66f006"
down_revision: Union[str, Sequence[str], None] = "e5c0de55e005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("enrichment_run", sa.Column("summary", sa.String(), nullable=True))
    op.add_column("enrichment_run", sa.Column("summary_model", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("enrichment_run", "summary_model")
    op.drop_column("enrichment_run", "summary")
