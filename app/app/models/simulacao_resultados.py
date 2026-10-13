"""18.7D: eventos técnicos, identidade externa, educação e fencing separados."""
from uuid import uuid4
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from app.extensions import db
from .simulacao import fk, agora, estado_check, SnapshotImutavel, AlvoSimulacao


class VinculoCampanhaSimulacao(db.Model):
    __tablename__ = 'vinculos_campanha_simulacao'
    id = db.Column(db.Integer, primary_key=True)
    execucao_id = db.Column(db.Integer, nullable=False, unique=True)
    revisao_id = db.Column(db.Integer, nullable=False)
    instancia_id = db.Column(db.String(64), nullable=False)
    externo_id = db.Column(db.Integer)
    nome_tecnico = db.Column(db.String(150), nullable=False)
    digest = db.Column(db.String(64), nullable=False)
    estado = db.Column(db.String(35), nullable=False, default='pendente')
    reconciliado_em = db.Column(db.DateTime(timezone=True))
    codigo_erro = db.Column(db.String(40))
    descartados = db.Column(db.Integer, nullable=False, default=0, server_default='0')
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)
    __table_args__ = (
        db.ForeignKeyConstraint(['execucao_id','revisao_id','instancia_id'],
            ['execucoes_simulacao.id','execucoes_simulacao.revisao_id','execucoes_simulacao.instancia_id'], ondelete='RESTRICT'),
        db.UniqueConstraint('instancia_id','externo_id', name='uq_vinculo_instancia_campanha'),
        db.UniqueConstraint('instancia_id','nome_tecnico', name='uq_vinculo_nome'),
        db.CheckConstraint('externo_id IS NULL OR externo_id > 0', name='ck_vinculo_externo'),
        db.CheckConstraint('length(digest)=64 AND descartados>=0', name='ck_vinculo_dados'),
        estado_check(['pendente','vinculado','falha','resultado_indeterminado','cancelado'], 'ck_vinculo_estado'),
    )


class EventoSimulacao(db.Model):
    __tablename__ = 'eventos_simulacao'
    id = db.Column(db.Integer, primary_key=True)
    execucao_id = db.Column(db.Integer, nullable=False)
    alvo_id = db.Column(db.Integer, nullable=False)
    instancia_id = db.Column(db.String(64), nullable=False)
    resultado_externo_id = db.Column(db.String(128), nullable=False)
    evento_externo_id = db.Column(db.String(128))
    tipo = db.Column(db.String(30), nullable=False)
    origem_em = db.Column(db.DateTime(timezone=True), nullable=False)
    importado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)
    chave_deduplicacao = db.Column(db.String(64), nullable=False, unique=True)
    discriminador = db.Column(db.Integer)
    metadados_tecnicos = db.Column(db.JSON, nullable=False, default=lambda:{'contrato':1})
    __table_args__ = (
        db.ForeignKeyConstraint(['alvo_id','execucao_id','resultado_externo_id'],
            ['alvos_simulacao.id','alvos_simulacao.execucao_id','alvos_simulacao.identificador_externo'], ondelete='RESTRICT'),
        db.ForeignKeyConstraint(['execucao_id','instancia_id'],
            ['execucoes_simulacao.id','execucoes_simulacao.instancia_id'], ondelete='RESTRICT'),
        db.CheckConstraint("tipo IN ('mensagem_enviada','mensagem_aberta','link_acessado','erro_envio')", name='ck_evento_tipo'),
        db.CheckConstraint('length(chave_deduplicacao)=64 AND (discriminador IS NULL OR discriminador>=0)', name='ck_evento_dados'),
        db.Index('ix_evento_execucao_origem','execucao_id','origem_em'),
        db.Index('ix_evento_alvo','alvo_id'),
    )


class AssociacaoEducacionalSimulacao(db.Model):
    __tablename__ = 'associacoes_educacionais_simulacao'
    id = db.Column(db.Integer, primary_key=True)
    alvo_id = db.Column(db.Integer, fk('alvos_simulacao'), nullable=False, unique=True)
    participacao_id = db.Column(db.Integer, fk('participacoes_treinamento'), nullable=False)
    responsavel_id = db.Column(db.Integer, fk('usuarios'), nullable=False)
    criada_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)


class PonteEducativaSimulacao(db.Model):
    __tablename__ = 'pontes_educativas_simulacao'
    id = db.Column(db.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    alvo_id = db.Column(db.Integer, fk('alvos_simulacao'), nullable=False)
    responsavel_id = db.Column(db.Integer, fk('usuarios'), nullable=False)
    estado = db.Column(db.String(20), nullable=False, default='ativa')
    criada_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)
    expira_em = db.Column(db.DateTime(timezone=True), nullable=False)
    consumida_em = db.Column(db.DateTime(timezone=True))
    revogada_em = db.Column(db.DateTime(timezone=True))
    __table_args__ = (
        estado_check(['ativa','consumida','revogada'], 'ck_ponte_estado'),
        db.CheckConstraint('expira_em>criada_em', name='ck_ponte_expiracao'),
        db.Index('ix_ponte_alvo','alvo_id'),
        db.Index('uq_ponte_disponivel','alvo_id',unique=True,
            postgresql_where=db.text("estado IN ('ativa','consumida')"),sqlite_where=db.text("estado IN ('ativa','consumida')")),
    )


class LeaseOperacaoSimulacao(db.Model):
    __tablename__ = 'leases_operacao_simulacao'
    operacao_id = db.Column(db.Integer, fk('operacoes_simulacao'), primary_key=True)
    geracao = db.Column(db.BigInteger, nullable=False, default=1)
    executor_id = db.Column(db.Uuid(as_uuid=True))
    responsavel_id = db.Column(db.Integer, fk('usuarios'))
    estado = db.Column(db.String(20), nullable=False, default='ativa')
    expira_em = db.Column(db.DateTime(timezone=True))
    renovada_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)
    __table_args__ = (
        db.CheckConstraint('geracao>=0', name='ck_lease_geracao'),
        db.CheckConstraint("(estado='legado' AND geracao=0 AND executor_id IS NULL AND expira_em IS NULL) OR (estado IN ('ativa','encerrada') AND geracao>0 AND executor_id IS NOT NULL AND responsavel_id IS NOT NULL AND expira_em IS NOT NULL)", name='ck_lease_identidade'),
        estado_check(['ativa','legado','encerrada'], 'ck_lease_estado'),
    )


@event.listens_for(Session,'before_flush')
def proteger_resultados(session,flush_context,instances):
    for obj in session.new:
        if isinstance(obj,EventoSimulacao) and obj.metadados_tecnicos not in (None,{'contrato':1}):raise SnapshotImutavel()
    for obj in session.dirty | session.deleted:
        if isinstance(obj,(EventoSimulacao,AssociacaoEducacionalSimulacao)):
            if obj in session.deleted or session.is_modified(obj):raise SnapshotImutavel()
        if isinstance(obj,VinculoCampanhaSimulacao):
            fields=('execucao_id','revisao_id','instancia_id','nome_tecnico','digest','criado_em')
            if obj in session.deleted or any(inspect(obj).attrs[f].history.has_changes() for f in fields):raise SnapshotImutavel()
            h=inspect(obj).attrs.externo_id.history
            if h.has_changes() and h.deleted and h.deleted[0] is not None:raise SnapshotImutavel()
        if isinstance(obj,AlvoSimulacao):
            h=inspect(obj).attrs.identificador_externo.history
            if h.has_changes() and h.deleted and h.deleted[0] is not None:raise SnapshotImutavel()
