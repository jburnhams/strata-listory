"""covers: cover_image, cover_run and cover_candidate

Revision ID: d4c0de44e004
Revises: c3c0de33e003
Create Date: 2026-11-12 22:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'd4c0de44e004'
down_revision: Union[str, Sequence[str], None] = 'c3c0de33e003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('cover_image',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(), nullable=False),
        sa.Column('ext', sa.String(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('dhash', sa.String(), nullable=False),
        sa.Column('fetched_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cover_image')),
        sa.UniqueConstraint('sha256', name=op.f('uq_cover_image_sha256')))
    op.create_index(op.f('ix_cover_image_dhash'), 'cover_image', ['dhash'])
    op.create_table('cover_run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('listen_id', sa.Integer(), nullable=False),
        sa.Column('listen_changed_at', sa.DateTime(), nullable=True),
        sa.Column('sources', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('started_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['listen_id'], ['listen.id'], name=op.f('fk_cover_run_listen_id_listen')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cover_run')))
    op.create_index(op.f('ix_cover_run_listen_id'), 'cover_run', ['listen_id'])
    op.create_table('cover_candidate',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('image_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('ref', sa.String(), nullable=False),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['cover_run.id'], name=op.f('fk_cover_candidate_run_id_cover_run'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['image_id'], ['cover_image.id'], name=op.f('fk_cover_candidate_image_id_cover_image')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_cover_candidate')))
    op.create_index(op.f('ix_cover_candidate_run_id'), 'cover_candidate', ['run_id'])
    op.create_index(op.f('ix_cover_candidate_image_id'), 'cover_candidate', ['image_id'])
    op.create_index(op.f('ix_cover_candidate_url'), 'cover_candidate', ['url'])


def downgrade() -> None:
    op.drop_table('cover_candidate')
    op.drop_table('cover_run')
    op.drop_table('cover_image')
