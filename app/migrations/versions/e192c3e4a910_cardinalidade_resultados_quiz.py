"""19.2C3: remove somente a UNIQUE individual; não libera refação.

Revision ID: e192c3e4a910
Revises: d192c1e4a909
"""
from alembic import op
import sqlalchemy as sa

revision = 'e192c3e4a910'
down_revision = 'd192c1e4a909'
branch_labels = None
depends_on = None

OLD_UNIQUE = 'resultados_quiz_participacao_id_key'


def upgrade():
    op.drop_constraint(OLD_UNIQUE, 'resultados_quiz', type_='unique')


def downgrade():
    # Mesma ordem do portal e da C1: participação -> resultado. Os locks são
    # mantidos na transação Alembic até COMMIT/ROLLBACK, incluindo CREATE UNIQUE.
    # A janela de manutenção deve drenar escritores antes de qualquer downgrade.
    op.execute('LOCK TABLE participacoes_treinamento, resultados_quiz '
               'IN ACCESS EXCLUSIVE MODE')
    multiple = op.get_bind().execute(sa.text(
        'SELECT EXISTS (SELECT 1 FROM resultados_quiz '
        'GROUP BY participacao_id HAVING COUNT(*) > 1)'
    )).scalar_one()
    if multiple:
        raise RuntimeError(
            'Downgrade 19.2C3 bloqueado: existem participações com mais de um '
            'resultado de quiz. A UNIQUE individual não pode ser restaurada. '
            'Nenhuma tentativa foi excluída, renumerada ou alterada.'
        )
    op.create_unique_constraint(OLD_UNIQUE, 'resultados_quiz', ['participacao_id'])
