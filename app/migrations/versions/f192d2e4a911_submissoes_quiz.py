"""19.2D2: identidade persistida de submissão, sem habilitar refação no portal.

Revision ID: f192d2e4a911
Revises: e192c3e4a910
"""
from alembic import op
import sqlalchemy as sa

revision = 'f192d2e4a911'
down_revision = 'e192c3e4a910'
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint('uq_resultados_quiz_participacao_id', 'resultados_quiz',
                                ['participacao_id', 'id'])
    op.create_table('submissoes_quiz',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('participacao_id', sa.Integer(), nullable=False),
        sa.Column('resultado_base_id', sa.Integer(), nullable=True),
        sa.Column('estado', sa.String(12), nullable=False),
        sa.Column('criada_em', sa.DateTime(timezone=True), nullable=False),
        sa.Column('resultado_id', sa.Integer(), nullable=True),
        sa.Column('processada_em', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('participacao_id', 'resultado_base_id', name='uq_submissoes_quiz_base'),
        sa.UniqueConstraint('resultado_id', name='uq_submissoes_quiz_resultado'),
        sa.ForeignKeyConstraint(['participacao_id'], ['participacoes_treinamento.id'],
            name='fk_submissoes_quiz_participacao', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['participacao_id', 'resultado_base_id'],
            ['resultados_quiz.participacao_id', 'resultados_quiz.id'],
            name='fk_submissoes_quiz_base', ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['participacao_id', 'resultado_id'],
            ['resultados_quiz.participacao_id', 'resultados_quiz.id'],
            name='fk_submissoes_quiz_resultado', ondelete='RESTRICT'),
        sa.CheckConstraint(
            "(estado = 'pendente' AND resultado_id IS NULL AND processada_em IS NULL) OR "
            "(estado = 'processada' AND resultado_id IS NOT NULL AND processada_em IS NOT NULL)",
            name='ck_submissoes_quiz_estado'),
        sa.CheckConstraint('processada_em IS NULL OR processada_em >= criada_em',
            name='ck_submissoes_quiz_datas'),
        sa.CheckConstraint('resultado_base_id IS NULL OR resultado_id IS NULL OR resultado_base_id <> resultado_id',
            name='ck_submissoes_quiz_resultado_distinto'))
    op.create_index('uq_submissoes_quiz_inicial', 'submissoes_quiz', ['participacao_id'],
                    unique=True, postgresql_where=sa.text('resultado_base_id IS NULL'))
    op.create_index('uq_submissoes_quiz_pendente', 'submissoes_quiz', ['participacao_id'],
                    unique=True, postgresql_where=sa.text("estado = 'pendente'"))


def downgrade():
    # Mesma ordem dos escritores: participação antes das tabelas de avaliação.
    # ACCESS EXCLUSIVE mantém a verificação e o DROP indivisíveis no PostgreSQL.
    op.execute('LOCK TABLE participacoes_treinamento, resultados_quiz, submissoes_quiz '
               'IN ACCESS EXCLUSIVE MODE')
    if op.get_bind().execute(sa.text('SELECT EXISTS (SELECT 1 FROM submissoes_quiz)')).scalar_one():
        raise RuntimeError('Downgrade 19.2D2 bloqueado: existem submissões pendentes ou '
                           'processadas; nenhum histórico pode ser descartado.')
    op.drop_table('submissoes_quiz')
    op.drop_constraint('uq_resultados_quiz_participacao_id', 'resultados_quiz', type_='unique')
