"""Snapshot nullable da avaliação e primeiro instante de aprovação.

Revision ID: c199b1e4a908
Revises: b198a1e4a907
"""
from alembic import op
import sqlalchemy as sa

revision = 'c199b1e4a908'
down_revision = 'b198a1e4a907'
branch_labels = None
depends_on = None

# Expressões congeladas na revisão; não importar o modelo corrente.
AVALIACAO_COMPLETA = (
    '(percentual_minimo_aplicado IS NULL AND minimo_acertos_aplicado IS NULL AND '
    'aprovado IS NULL) OR (percentual_minimo_aplicado IS NOT NULL AND '
    'minimo_acertos_aplicado IS NOT NULL AND aprovado IS NOT NULL)'
)
AVALIACAO_CRITERIO = (
    'aprovado IS NULL OR (total_questoes > 0 AND total_acertos BETWEEN 0 AND '
    'total_questoes AND percentual BETWEEN 0 AND 100 AND percentual_minimo_aplicado > 0 '
    'AND percentual_minimo_aplicado <= 100 AND minimo_acertos_aplicado BETWEEN 1 AND '
    'total_questoes AND 100 * CAST(minimo_acertos_aplicado AS NUMERIC) >= '
    'total_questoes * percentual_minimo_aplicado AND 100 * '
    '(CAST(minimo_acertos_aplicado AS NUMERIC) - 1) < total_questoes * '
    'percentual_minimo_aplicado AND aprovado = (total_acertos >= '
    'minimo_acertos_aplicado))'
)


def upgrade():
    op.add_column('participacoes_treinamento', sa.Column(
        'aprovado_em', sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column('resultados_quiz', sa.Column(
        'percentual_minimo_aplicado', sa.Numeric(5, 2), nullable=True,
    ))
    op.add_column('resultados_quiz', sa.Column(
        'minimo_acertos_aplicado', sa.Integer(), nullable=True,
    ))
    op.add_column('resultados_quiz', sa.Column(
        'aprovado', sa.Boolean(), nullable=True,
    ))
    op.create_check_constraint('ck_resultados_quiz_avaliacao_completa',
                               'resultados_quiz', AVALIACAO_COMPLETA)
    op.create_check_constraint('ck_resultados_quiz_avaliacao_criterio',
                               'resultados_quiz', AVALIACAO_CRITERIO)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # Mesma ordem participação -> resultado; protege verificação e DROP.
        op.execute('LOCK TABLE participacoes_treinamento, resultados_quiz '
                   'IN ACCESS EXCLUSIVE MODE')
    possui_dados = bind.execute(sa.text(
        'SELECT EXISTS (SELECT 1 FROM participacoes_treinamento '
        'WHERE aprovado_em IS NOT NULL) OR EXISTS '
        '(SELECT 1 FROM resultados_quiz WHERE percentual_minimo_aplicado IS NOT NULL '
        'OR minimo_acertos_aplicado IS NOT NULL OR aprovado IS NOT NULL)'
    )).scalar_one()
    if possui_dados:
        raise RuntimeError(
            'Downgrade bloqueado: existem dados de aprovação ou avaliação. '
            'Exporte-os e trate-os explicitamente antes de remover as colunas.'
        )
    op.drop_constraint('ck_resultados_quiz_avaliacao_criterio',
                       'resultados_quiz', type_='check')
    op.drop_constraint('ck_resultados_quiz_avaliacao_completa',
                       'resultados_quiz', type_='check')
    op.drop_column('resultados_quiz', 'aprovado')
    op.drop_column('resultados_quiz', 'minimo_acertos_aplicado')
    op.drop_column('resultados_quiz', 'percentual_minimo_aplicado')
    op.drop_column('participacoes_treinamento', 'aprovado_em')
