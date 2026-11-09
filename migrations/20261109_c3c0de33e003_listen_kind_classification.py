"""listen kind classification: the `play` kind, the `reclassify` event and the listen_kind table

Revision ID: c3c0de33e003
Revises: b2c0de22e002
Create Date: 2026-11-09 15:30:00

Written by hand: Alembic does not see a changed CHECK constraint.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'c3c0de33e003'
down_revision: Union[str, Sequence[str], None] = 'b2c0de22e002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KIND_OLD = "kind in ('book', 'radio_series', 'radio_show', 'other')"
KIND_NEW = "kind in ('book', 'radio_series', 'radio_show', 'play', 'other')"
ACTIONS = "'create', 'link', 'unlink', 'relink', 'reorder', 'merge', 'split', 'status_change', 'accept', 'reject', 'revoke', 'choose', 'unchoose', 'retire'"
KIND_CK, ACTION_CK = op.f('ck_listen_listen_kind'), op.f('ck_listen_event_listen_event_action')


def upgrade() -> None:
    with op.batch_alter_table('listen') as b:
        b.drop_constraint(KIND_CK, type_='check'); b.create_check_constraint(KIND_CK, KIND_NEW)
    with op.batch_alter_table('listen_event') as b:
        b.drop_constraint(ACTION_CK, type_='check'); b.create_check_constraint(ACTION_CK, f"action in ({ACTIONS}, 'reclassify')")
    op.create_table('listen_kind',
        sa.Column('listen_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('guess', sa.String(), nullable=False),
        sa.Column('p_broadcast', sa.Float(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('band', sa.String(), nullable=False),
        sa.Column('signals', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('decided_by', sa.String(), server_default='rule', nullable=False),
        sa.Column('computed_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(KIND_NEW, name=op.f('ck_listen_kind_listen_kind_kind')),
        sa.CheckConstraint("band in ('broadcast', 'book', 'review')", name=op.f('ck_listen_kind_listen_kind_band')),
        sa.CheckConstraint("decided_by in ('rule', 'user')", name=op.f('ck_listen_kind_listen_kind_decided_by')),
        sa.ForeignKeyConstraint(['listen_id'], ['listen.id'], name=op.f('fk_listen_kind_listen_id_listen')),
        sa.PrimaryKeyConstraint('listen_id', name=op.f('pk_listen_kind')))


def downgrade() -> None:
    op.drop_table('listen_kind')
    op.execute("update listen set kind = 'radio_show' where kind = 'play'")
    op.execute("delete from listen_event where action = 'reclassify'")
    with op.batch_alter_table('listen_event') as b:
        b.drop_constraint(ACTION_CK, type_='check'); b.create_check_constraint(ACTION_CK, f"action in ({ACTIONS})")
    with op.batch_alter_table('listen') as b:
        b.drop_constraint(KIND_CK, type_='check'); b.create_check_constraint(KIND_CK, KIND_OLD)
