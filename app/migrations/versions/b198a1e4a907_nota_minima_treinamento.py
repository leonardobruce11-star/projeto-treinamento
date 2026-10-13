"""Configuração opcional da aprovação, sem alterar o fluxo do quiz.

Revision ID: b198a1e4a907
Revises: a187d7e4a906
"""
from alembic import op
import sqlalchemy as sa

revision = 'b198a1e4a907'
down_revision = 'a187d7e4a906'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('treinamentos', sa.Column(
        'nota_minima_percentual', sa.Numeric(5, 2), nullable=True,
    ))
    op.create_check_constraint(
        'ck_treinamentos_nota_minima_percentual', 'treinamentos',
        'nota_minima_percentual IS NULL OR '
        '(nota_minima_percentual > 0 AND nota_minima_percentual <= 100)',
    )


def downgrade():
    # Não apagar silenciosamente configuração manual de treinamento.
    if op.get_bind().dialect.name == 'postgresql':
        # Evita uma configuração concorrente entre a verificação e o DROP.
        op.execute('LOCK TABLE treinamentos IN ACCESS EXCLUSIVE MODE')
    configurado = op.get_bind().execute(sa.text(
        'SELECT EXISTS (SELECT 1 FROM treinamentos '
        'WHERE nota_minima_percentual IS NOT NULL)'
    )).scalar_one()
    if configurado:
        raise RuntimeError(
            'Downgrade bloqueado: exporte as notas mínimas manuais e '
            'redefina-as explicitamente para NULL antes de remover a coluna.'
        )
    op.drop_constraint('ck_treinamentos_nota_minima_percentual',
                       'treinamentos', type_='check')
    op.drop_column('treinamentos', 'nota_minima_percentual')
