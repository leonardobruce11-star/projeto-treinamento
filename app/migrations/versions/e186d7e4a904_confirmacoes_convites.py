"""Recibo de confirmação humana de convites (18.6D)."""
from alembic import op
import sqlalchemy as sa

revision = 'e186d7e4a904'
down_revision = 'd186b7e4a903'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('confirmacoes_convites',
        sa.Column('id', sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('assinatura', sa.String(64), nullable=False),
        sa.Column('resultado', sa.JSON(), nullable=False),
        sa.Column('criado_em', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ondelete='RESTRICT'))


def downgrade():
    op.drop_table('confirmacoes_convites')
