"""Fundação da numeração; mantém UNIQUE por participação e leitura singular.

Revision ID: d192c1e4a909
Revises: c199b1e4a908
"""
from alembic import op
import sqlalchemy as sa

revision = 'd192c1e4a909'
down_revision = 'c199b1e4a908'
branch_labels = None
depends_on = None


def upgrade():
    # DEFAULT constante também preenche os registros existentes com 1.
    # Nenhuma nota, classificação ou data é recalculada/atualizada.
    op.add_column('resultados_quiz', sa.Column(
        'numero_tentativa', sa.Integer(), nullable=False,
        server_default=sa.text('1'),
    ))
    op.create_check_constraint('ck_resultados_quiz_numero_tentativa',
                               'resultados_quiz', 'numero_tentativa >= 1')
    op.create_unique_constraint('uq_resultados_quiz_participacao_tentativa',
                                'resultados_quiz', ['participacao_id', 'numero_tentativa'])
    # resultados_quiz_participacao_id_key permanece intocada nesta subetapa.


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # Ordem existente do portal e das migrations: participação -> resultado.
        # Impede escrita concorrente entre a verificação e o DROP.
        op.execute('LOCK TABLE participacoes_treinamento, resultados_quiz '
                   'IN ACCESS EXCLUSIVE MODE')
    possui_outras_tentativas = bind.execute(sa.text(
        'SELECT EXISTS (SELECT 1 FROM resultados_quiz WHERE numero_tentativa <> 1)'
    )).scalar_one()
    if possui_outras_tentativas:
        raise RuntimeError(
            'Downgrade bloqueado: existem resultados com numero_tentativa diferente de 1. '
            'Trate o histórico explicitamente antes de remover a numeração.'
        )
    op.drop_constraint('uq_resultados_quiz_participacao_tentativa',
                       'resultados_quiz', type_='unique')
    op.drop_constraint('ck_resultados_quiz_numero_tentativa',
                       'resultados_quiz', type_='check')
    op.drop_column('resultados_quiz', 'numero_tentativa')
