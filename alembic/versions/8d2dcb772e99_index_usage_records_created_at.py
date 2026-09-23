"""index usage_records created_at

Revision ID: 8d2dcb772e99
Revises: 88c2522ef01f
Create Date: 2026-09-11 01:11:28.295580

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8d2dcb772e99'
down_revision: Union[str, Sequence[str], None] = '88c2522ef01f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(op.f('ix_usage_records_created_at'), 'usage_records', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_usage_records_created_at'), table_name='usage_records')
