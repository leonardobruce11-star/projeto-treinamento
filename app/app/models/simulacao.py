"""18.7C: governança e recursos auxiliares; sem eventos ou campanhas externas."""
import json
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from app.extensions import db


def agora():
    return datetime.now(timezone.utc)


def fk(table):
    return db.ForeignKey(table + '.id', ondelete='RESTRICT')


def estado_check(values, name):
    return db.CheckConstraint('estado IN (' + ','.join("'" + v + "'" for v in values) + ')', name=name)


class RevisaoSimulacao(db.Model):
    __tablename__ = 'revisoes_simulacao'
    id = db.Column(db.Integer, primary_key=True)
    campanha_id = db.Column(db.Integer, fk('campanhas'), nullable=False)
    numero = db.Column(db.Integer, nullable=False)
    digest = db.Column(db.String(64), nullable=False)
    snapshot_canonico = db.Column(db.Text, nullable=False)
    estado = db.Column(db.String(30), nullable=False, default='aprovada')
    aprovado_por_id = db.Column(db.Integer, fk('usuarios'), nullable=False)
    aprovado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)
    grupo_id = db.Column(db.Integer, fk('grupos'), nullable=False)
    cenario_id = db.Column(db.Integer, fk('cenarios'), nullable=False)
    template_email_id = db.Column(db.Integer, fk('templates_email'), nullable=False)
    landing_page_id = db.Column(db.Integer, fk('landing_pages'), nullable=False)
    treinamento_id = db.Column(db.Integer, fk('treinamentos'), nullable=False)
    __table_args__ = (
        db.UniqueConstraint('campanha_id', 'numero', name='uq_revisao_campanha_numero'),
        db.CheckConstraint('numero > 0', name='ck_revisao_numero'),
        db.CheckConstraint('length(digest) = 64', name='ck_revisao_digest'),
        estado_check(['aprovada'], 'ck_revisao_estado'),
        db.Index('ix_revisao_campanha', 'campanha_id'),
    )

    @property
    def snapshot(self):
        # Sempre retorna uma cópia. Mutações em JSON aninhado não alteram o ORM.
        return json.loads(self.snapshot_canonico)


class ExecucaoSimulacao(db.Model):
    __tablename__ = 'execucoes_simulacao'
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.Uuid(as_uuid=True), nullable=False, unique=True, default=uuid4)
    revisao_id = db.Column(db.Integer, fk('revisoes_simulacao'), nullable=False)
    fase = db.Column(db.String(15), nullable=False)
    sequencia = db.Column(db.Integer, nullable=False)
    estado = db.Column(db.String(35), nullable=False, default='nao_preparada')
    responsavel_id = db.Column(db.Integer, fk('usuarios'), nullable=False)
    instancia_id = db.Column(db.String(64), nullable=False)
    origem_digest = db.Column(db.String(64), nullable=False)
    publico_digest = db.Column(db.String(64), nullable=False)
    criado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)
    preparacao_iniciada_em = db.Column(db.DateTime(timezone=True))
    preparada_em = db.Column(db.DateTime(timezone=True))
    reconciliada_em = db.Column(db.DateTime(timezone=True))
    __table_args__ = (
        db.UniqueConstraint('revisao_id', 'fase', name='uq_execucao_revisao_fase'),
        db.UniqueConstraint('revisao_id', 'fase', 'sequencia', name='uq_execucao_sequencia'),
        db.UniqueConstraint('id', 'revisao_id', 'instancia_id', name='uq_execucao_vinculo'),
        db.UniqueConstraint('id','instancia_id', name='uq_execucao_instancia'),
        db.CheckConstraint("fase IN ('piloto','ampliada')", name='ck_execucao_fase'),
        db.CheckConstraint('sequencia > 0', name='ck_execucao_sequencia'),
        db.CheckConstraint('length(origem_digest) = 64 AND length(publico_digest) = 64', name='ck_execucao_digests'),
        estado_check(['nao_preparada','preparando','preparada','falha','resultado_indeterminado','cancelada'], 'ck_execucao_estado'),
        db.Index('ix_execucao_revisao', 'revisao_id'),
        db.Index('ix_execucao_instancia', 'instancia_id'),
    )


class AlvoSimulacao(db.Model):
    __tablename__ = 'alvos_simulacao'
    id = db.Column(db.Integer, primary_key=True)
    execucao_id = db.Column(db.Integer, fk('execucoes_simulacao'), nullable=False)
    colaborador_id = db.Column(db.Integer, fk('colaboradores'), nullable=False)
    participacao_id = db.Column(db.Integer, fk('participacoes_treinamento'))
    email_aprovado = db.Column(db.String(180), nullable=False)
    nome = db.Column(db.String(150))  # Não utilizado/exportado nesta etapa.
    identificador_externo = db.Column(db.String(128))
    estado = db.Column(db.String(25), nullable=False, default='autorizado')
    __table_args__ = (
        db.UniqueConstraint('execucao_id', 'colaborador_id', name='uq_alvo_execucao_colaborador'),
        db.UniqueConstraint('id','execucao_id','identificador_externo', name='uq_alvo_resultado_vinculo'),
        db.UniqueConstraint('execucao_id', 'email_aprovado', name='uq_alvo_execucao_email'),
        db.UniqueConstraint('execucao_id', 'identificador_externo', name='uq_alvo_externo'),
        estado_check(['autorizado','bloqueado'], 'ck_alvo_estado'),
        db.Index('ix_alvo_colaborador', 'colaborador_id'),
        db.Index('ix_alvo_participacao', 'participacao_id'),
    )


class RecursoExternoSimulacao(db.Model):
    __tablename__ = 'recursos_externos_simulacao'
    id = db.Column(db.Integer, primary_key=True)
    execucao_id = db.Column(db.Integer, nullable=False)
    revisao_id = db.Column(db.Integer, nullable=False)
    instancia_id = db.Column(db.String(64), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    nome_externo = db.Column(db.String(150), nullable=False)
    externo_id = db.Column(db.Integer)
    digest = db.Column(db.String(64), nullable=False)
    estado = db.Column(db.String(35), nullable=False, default='pendente')
    criacao_incerta = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    reconciliado_em = db.Column(db.DateTime(timezone=True))
    codigo_erro = db.Column(db.String(40))
    __table_args__ = (
        db.ForeignKeyConstraint(['execucao_id','revisao_id','instancia_id'],
            ['execucoes_simulacao.id','execucoes_simulacao.revisao_id','execucoes_simulacao.instancia_id'],
            name='fk_recurso_execucao_vinculo', ondelete='RESTRICT'),
        db.UniqueConstraint('execucao_id','tipo', name='uq_recurso_execucao_tipo'),
        db.UniqueConstraint('instancia_id','tipo','externo_id', name='uq_recurso_instancia_externo'),
        db.UniqueConstraint('instancia_id','tipo','nome_externo', name='uq_recurso_nome'),
        db.CheckConstraint("tipo IN ('grupo','template','landing_page')", name='ck_recurso_tipo'),
        db.CheckConstraint('externo_id IS NULL OR externo_id > 0', name='ck_recurso_externo'),
        db.CheckConstraint('length(digest) = 64', name='ck_recurso_digest'),
        estado_check(['pendente','sincronizando','pronta','ausente','falha','resultado_indeterminado'], 'ck_recurso_estado'),
        db.Index('ix_recurso_execucao', 'execucao_id'),
    )


class OperacaoSimulacao(db.Model):
    __tablename__ = 'operacoes_simulacao'
    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.Uuid(as_uuid=True), nullable=False, unique=True)
    execucao_id = db.Column(db.Integer, fk('execucoes_simulacao'), nullable=False)
    acao = db.Column(db.String(20), nullable=False)
    estado = db.Column(db.String(35), nullable=False, default='pendente')
    responsavel_id = db.Column(db.Integer, fk('usuarios'), nullable=False)
    tentativa = db.Column(db.Integer, nullable=False, default=1)
    iniciado_em = db.Column(db.DateTime(timezone=True), nullable=False, default=agora)
    finalizado_em = db.Column(db.DateTime(timezone=True))
    codigo_erro = db.Column(db.String(40))
    resultado = db.Column(db.JSON, nullable=False, default=dict)
    __table_args__ = (
        db.CheckConstraint("acao IN ('preparar','reconciliar','vincular','importar')", name='ck_operacao_acao'),
        db.CheckConstraint('tentativa > 0', name='ck_operacao_tentativa'),
        estado_check(['pendente','processando','concluida','falha','resultado_indeterminado','cancelada'], 'ck_operacao_estado'),
        db.Index('ix_operacao_execucao', 'execucao_id'),
        db.Index('uq_operacao_processando', 'execucao_id', unique=True,
                 postgresql_where=db.text("estado = 'processando'"), sqlite_where=db.text("estado = 'processando'")),
    )


class SnapshotImutavel(ValueError):
    def __init__(self):
        super().__init__('Snapshot aprovado não pode ser alterado.')


IMUTAVEIS = {
    OperacaoSimulacao: ('uuid','execucao_id','acao','responsavel_id','tentativa','iniciado_em'),
    ExecucaoSimulacao: ('uuid','revisao_id','fase','sequencia','responsavel_id','instancia_id','origem_digest','publico_digest','criado_em'),
    AlvoSimulacao: ('execucao_id','colaborador_id','participacao_id','email_aprovado','nome'),
    RecursoExternoSimulacao: ('execucao_id','revisao_id','instancia_id','tipo','nome_externo','digest'),
}


@event.listens_for(Session, 'before_flush')
def proteger_snapshot(session, flush_context, instances):
    for obj in session.dirty | session.deleted:
        if isinstance(obj, RevisaoSimulacao):
            if obj in session.deleted or session.is_modified(obj, include_collections=False):
                raise SnapshotImutavel()
        for model, fields in IMUTAVEIS.items():
            if isinstance(obj, model) and (obj in session.deleted or any(inspect(obj).attrs[f].history.has_changes() for f in fields)):
                raise SnapshotImutavel()
