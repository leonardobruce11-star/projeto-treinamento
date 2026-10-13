"""Solicitação administrativa; não armazena token, URL ou conteúdo da mensagem."""
from app.extensions import db

ESTADOS_ENVIO = ('pendente', 'em_processamento', 'enviado', 'falha',
                 'resultado_indeterminado', 'cancelado')


class EnvioConviteTreinamento(db.Model):
    __tablename__ = 'envios_convite_treinamento'
    __table_args__ = (
        db.UniqueConstraint('participacao_id', 'sequencia', name='uq_convite_participacao_sequencia'),
        db.UniqueConstraint('identificador_confirmacao', name='uq_convite_confirmacao'),
        db.CheckConstraint('sequencia >= 1', name='ck_convite_sequencia'),
        db.CheckConstraint("(tipo = 'primeiro_envio' AND sequencia = 1) OR "
                           "(tipo = 'reenvio' AND sequencia > 1)", name='ck_convite_tipo_sequencia'),
        db.CheckConstraint("estado IN ('pendente','em_processamento','enviado','falha',"
                           "'resultado_indeterminado','cancelado')", name='ck_convite_estado'),
        db.CheckConstraint("(estado = 'pendente' AND processamento_iniciado_em IS NULL AND finalizado_em IS NULL) OR "
                           "(estado = 'em_processamento' AND processamento_iniciado_em IS NOT NULL AND finalizado_em IS NULL) OR "
                           "(estado IN ('enviado','falha','resultado_indeterminado') AND processamento_iniciado_em IS NOT NULL AND finalizado_em IS NOT NULL) OR "
                           "(estado = 'cancelado' AND processamento_iniciado_em IS NULL AND finalizado_em IS NOT NULL)",
                           name='ck_convite_datas_estado'),
        db.Index('ix_convite_fila', 'estado', 'id'),
    )
    id = db.Column(db.Integer, primary_key=True)
    participacao_id = db.Column(db.Integer, db.ForeignKey('participacoes_treinamento.id', ondelete='RESTRICT'), nullable=False)
    sequencia = db.Column(db.Integer, nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    solicitado_por_usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id', ondelete='RESTRICT'), nullable=False)
    destinatario = db.Column(db.String(254), nullable=False)
    identificador_confirmacao = db.Column(db.Uuid(as_uuid=True), nullable=False)
    estado = db.Column(db.String(30), nullable=False, default='pendente', server_default='pendente')
    versao_mensagem = db.Column(db.String(30), nullable=False, default='convite-v1', server_default='convite-v1')
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())
    processamento_iniciado_em = db.Column(db.DateTime(timezone=True))
    finalizado_em = db.Column(db.DateTime(timezone=True))
