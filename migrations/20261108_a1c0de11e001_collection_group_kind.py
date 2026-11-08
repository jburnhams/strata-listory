"""collection group kind

Revision ID: a1c0de11e001
Revises: 57adb3bb6505
Create Date: 2026-11-08 05:25:00

Written by hand: Alembic does not see a changed CHECK constraint.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'a1c0de11e001'
down_revision: Union[str, Sequence[str], None] = '57adb3bb6505'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = "kind in ('root', 'folder', 'disc', 'book', 'series', 'loose')"
NEW = "kind in ('root', 'folder', 'disc', 'book', 'series', 'collection', 'loose')"
NAME = op.f('ck_part_filegroup_filegroup_kind')


def upgrade() -> None:
    with op.batch_alter_table('part_filegroup') as b:
        b.drop_constraint(NAME, type_='check'); b.create_check_constraint(NAME, NEW)


def downgrade() -> None:
    op.execute("update part_filegroup set kind = 'folder' where kind = 'collection'")
    with op.batch_alter_table('part_filegroup') as b:
        b.drop_constraint(NAME, type_='check'); b.create_check_constraint(NAME, OLD)
