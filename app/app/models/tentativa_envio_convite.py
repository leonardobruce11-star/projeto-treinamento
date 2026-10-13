"""Tentativas de transporte com códigos normalizados, sem resposta bruta."""
from app.extensions import db

CODIGOS_ERRO = ('transporte_falso_falha', 'transporte_falso_indeterminado',
                'excecao_transporte', 'retorno_invalido', 'colaborador_inativo',
                'treinamento_inativo', 'participacao_concluida',
                'destinatario_invalido', 'destinatario_alterado')


class TentativaEnvioConvite(db.Model):
    __tablename__ = 'tentativas_envio_convite'
    __table_args__ = (
        db.UniqueConstraint('envio_id', 'numero_tentativa', name='uq_tentativa_convite_numero'),
        db.CheckConstraint('numero_tentativa >= 1', name='ck_tentativa_convite_numero'),
        db.CheckConstraint("resultado IS NULL OR resultado IN ('enviado','falha','resultado_indeterminado')", name='ck_tentativa_convite_resultado'),
        db.CheckConstraint("(resultado IS NULL AND finalizado_em IS NULL AND codigo_erro IS NULL) OR "
                           "(resultado IS NOT NULL AND finalizado_em IS NOT NULL AND "
                           "((resultado = 'enviado' AND codigo_erro IS NULL) OR "
                           "(resultado IN ('falha','resultado_indeterminado') AND codigo_erro IS NOT NULL)))", name='ck_tentativa_convite_finalizacao'),
        db.CheckConstraint("codigo_erro IS NULL OR codigo_erro IN (" + ','.join("'" + c + "'" for c in CODIGOS_ERRO) + ')', name='ck_tentativa_convite_codigo'),
        db.Index('uq_tentativa_convite_aberta', 'envio_id', unique=True,
                 postgresql_where=db.text('finalizado_em IS NULL'), sqlite_where=db.text('finalizado_em IS NULL')),
    )
    id = db.Column(db.Integer, primary_key=True)
    envio_id = db.Column(db.Integer, db.ForeignKey('envios_convite_treinamento.id', ondelete='RESTRICT'), nullable=False)
    numero_tentativa = db.Column(db.Integer, nullable=False)
    iniciado_em = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.current_timestamp())
    finalizado_em = db.Column(db.DateTime(timezone=True))
    resultado = db.Column(db.String(30))
    codigo_erro = db.Column(db.String(40))
    message_id = db.Column(db.Uuid(as_uuid=True), nullable=False, unique=True)
