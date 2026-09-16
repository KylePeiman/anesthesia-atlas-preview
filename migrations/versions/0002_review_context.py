"""Persist repair provenance and pending conversational clarifications."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    # 0001 creates current metadata on a fresh installation; existing databases
    # need these additive columns without rebuilding or reseeding their records.
    inspector = sa.inspect(op.get_bind())
    for table in ('jobs', 'conversations'):
        if 'context' not in {column['name'] for column in inspector.get_columns(table)}:
            op.add_column(table, sa.Column('context', sa.JSON(), nullable=False, server_default='{}'))
    if 'revision' not in {column['name'] for column in inspector.get_columns('conversations')}:
        op.add_column('conversations',sa.Column('revision',sa.Integer(),nullable=False,server_default='0'))


def downgrade():
    with op.batch_alter_table('conversations') as batch:
        batch.drop_column('revision')
    for table in ('jobs', 'conversations'):
        with op.batch_alter_table(table) as batch:
            batch.drop_column('context')
