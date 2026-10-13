"""Solicitações e tentativas de convite institucional (sem SMTP).

Revision ID: d186b7e4a903
Revises: c185b7e4a902
"""
from alembic import op
import sqlalchemy as sa

revision = 'd186b7e4a903'
down_revision = 'c185b7e4a902'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('envios_convite_treinamento',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('participacao_id', sa.Integer(), nullable=False),
        sa.Column('sequencia', sa.Integer(), nullable=False),
        sa.Column('tipo', sa.String(20), nullable=False),
        sa.Column('solicitado_por_usuario_id', sa.Integer(), nullable=False),
        sa.Column('destinatario', sa.String(254), nullable=False),
        sa.Column('identificador_confirmacao', sa.Uuid(as_uuid=True), nullable=False),
        sa.Column('estado', sa.String(30), nullable=False, server_default='pendente'),
        sa.Column('versao_mensagem', sa.String(30), nullable=False, server_default='convite-v1'),
        sa.Column('criado_em', sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column('processamento_iniciado_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finalizado_em', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['participacao_id'], ['participacoes_treinamento.id'], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(['solicitado_por_usuario_id'], ['usuarios.id'], ondelete="RESTRICT"),
        sa.CheckConstraint("(estado = 'pendente' AND processamento_iniciado_em IS NULL AND finalizado_em IS NULL) OR (estado = 'em_processamento' AND processamento_iniciado_em IS NOT NULL AND finalizado_em IS NULL) OR (estado IN ('enviado','falha','resultado_indeterminado') AND processamento_iniciado_em IS NOT NULL AND finalizado_em IS NOT NULL) OR (estado = 'cancelado' AND processamento_iniciado_em IS NULL AND finalizado_em IS NOT NULL)", name='ck_convite_datas_estado'),
        sa.CheckConstraint("estado IN ('pendente','em_processamento','enviado','falha','resultado_indeterminado','cancelado')", name='ck_convite_estado'),
        sa.CheckConstraint('sequencia >= 1', name='ck_convite_sequencia'),
        sa.CheckConstraint("(tipo = 'primeiro_envio' AND sequencia = 1) OR (tipo = 'reenvio' AND sequencia > 1)", name='ck_convite_tipo_sequencia'),
        sa.UniqueConstraint(*['identificador_confirmacao'], name='uq_convite_confirmacao'),
        sa.UniqueConstraint(*['participacao_id', 'sequencia'], name='uq_convite_participacao_sequencia'),
    )
    op.create_index('ix_convite_fila', 'envios_convite_treinamento', ['estado', 'id'], unique=False)
    op.create_table('tentativas_envio_convite',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('envio_id', sa.Integer(), nullable=False),
        sa.Column('numero_tentativa', sa.Integer(), nullable=False),
        sa.Column('iniciado_em', sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column('finalizado_em', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resultado', sa.String(30), nullable=True),
        sa.Column('codigo_erro', sa.String(40), nullable=True),
        sa.Column('message_id', sa.Uuid(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(['envio_id'], ['envios_convite_treinamento.id'], ondelete="RESTRICT"),
        sa.UniqueConstraint(*['message_id'], name=None),
        sa.CheckConstraint("codigo_erro IS NULL OR codigo_erro IN ('transporte_falso_falha','transporte_falso_indeterminado','excecao_transporte','retorno_invalido','colaborador_inativo','treinamento_inativo','participacao_concluida','destinatario_invalido','destinatario_alterado')", name='ck_tentativa_convite_codigo'),
        sa.CheckConstraint("(resultado IS NULL AND finalizado_em IS NULL AND codigo_erro IS NULL) OR (resultado IS NOT NULL AND finalizado_em IS NOT NULL AND ((resultado = 'enviado' AND codigo_erro IS NULL) OR (resultado IN ('falha','resultado_indeterminado') AND codigo_erro IS NOT NULL)))", name='ck_tentativa_convite_finalizacao'),
        sa.CheckConstraint('numero_tentativa >= 1', name='ck_tentativa_convite_numero'),
        sa.CheckConstraint("resultado IS NULL OR resultado IN ('enviado','falha','resultado_indeterminado')", name='ck_tentativa_convite_resultado'),
        sa.UniqueConstraint(*['envio_id', 'numero_tentativa'], name='uq_tentativa_convite_numero'),
    )
    op.create_index('uq_tentativa_convite_aberta', 'tentativas_envio_convite', ['envio_id'], unique=True, postgresql_where=sa.text('finalizado_em IS NULL'), sqlite_where=sa.text('finalizado_em IS NULL'))


def downgrade():
    op.drop_index('uq_tentativa_convite_aberta', table_name='tentativas_envio_convite')
    op.drop_table('tentativas_envio_convite')
    op.drop_index('ix_convite_fila', table_name='envios_convite_treinamento')
    op.drop_table('envios_convite_treinamento')
