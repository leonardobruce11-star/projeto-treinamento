"""adiciona tipo de campanha

Revision ID: 75449238a72e
Revises: 36fffaee8611
Create Date: 2026-09-30 11:03:44.366084

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '75449238a72e'
down_revision = '36fffaee8611'
branch_labels = None
depends_on = None


def upgrade():

    with op.batch_alter_table(
        'campanhas',
        schema=None
    ) as batch_op:

        batch_op.add_column(
            sa.Column(
                'tipo',
                sa.String(length=40),
                nullable=True
            )
        )

    # Campanhas já existentes pertencem ao fluxo antigo,
    # baseado em simulação de phishing.
    op.execute(
        """
        UPDATE campanhas
        SET tipo = 'simulacao_phishing'
        WHERE tipo IS NULL
        """
    )


def downgrade():

    with op.batch_alter_table(
        'campanhas',
        schema=None
    ) as batch_op:

        batch_op.drop_column(
            'tipo'
        )
