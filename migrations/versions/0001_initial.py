"""Initial persistent scheduling, session, approval and job records."""
revision='0001'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    from backend.db import Base
    from alembic import op
    Base.metadata.create_all(op.get_bind())

def downgrade():
    from backend.db import Base
    from alembic import op
    Base.metadata.drop_all(op.get_bind())
