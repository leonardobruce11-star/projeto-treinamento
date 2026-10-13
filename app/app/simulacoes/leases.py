"""Fencing persistente. Expiração dá elegibilidade, não prova abandono."""
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta,timezone
from uuid import uuid4
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.extensions import db
from app.models.simulacao import OperacaoSimulacao as O, ExecucaoSimulacao as E
from app.models.simulacao_resultados import LeaseOperacaoSimulacao as L
from .snapshots import SimulacaoInvalida, administrador, auditar, agora
from .preparacao import uuid_publico

TICKET=ContextVar('simulacao_fence',default=None)
DURACAO=timedelta(seconds=180)


@dataclass(frozen=True)
class Ticket:
    operacao_uuid:object
    geracao:int
    executor:object
    responsavel:int

    @property
    def marca(self):return f'{self.geracao}:{self.executor}'


def aware(value):return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def instante(session):
    return session.execute(select(func.clock_timestamp())).scalar_one() if session.get_bind().dialect.name=='postgresql' else agora()


def configurar_fence(session,ticket):
    if session.get_bind().dialect.name=='postgresql':
        session.execute(select(func.set_config('one.simulacao_fence',ticket.marca,True)))


def criar_lease(session,operacao,uid):
    ticket=Ticket(operacao.uuid,1,uuid4(),uid)
    TICKET.set(ticket);configurar_fence(session,ticket)
    session.add(L(operacao_id=operacao.id,geracao=1,executor_id=ticket.executor,responsavel_id=uid,
        estado='ativa',expira_em=instante(session)+DURACAO))
    session.flush()
    return ticket


def verificar(session,key,renovar=False):
    ticket=TICKET.get()
    if ticket is None or ticket.operacao_uuid!=key:raise SimulacaoInvalida('fence_lost')
    o=session.execute(select(O).where(O.uuid==key).with_for_update()).scalar_one_or_none()
    if not o or o.estado!='processando':raise SimulacaoInvalida('fence_lost')
    lease=session.execute(select(L).where(L.operacao_id==o.id).with_for_update()).scalar_one_or_none()
    now=instante(session)
    if not lease or lease.estado!='ativa' or (lease.geracao,lease.executor_id,lease.responsavel_id)!=(ticket.geracao,ticket.executor,ticket.responsavel) or aware(lease.expira_em)<=now:
        raise SimulacaoInvalida('fence_lost')
    configurar_fence(session,ticket)
    if renovar:lease.expira_em=now+DURACAO;lease.renovada_em=now
    return o,lease


def encerrar(session,key):
    o,lease=verificar(session,key)
    lease.estado='encerrada'
    return o


def recuperar(key,uid,confirmar=False,legado_encerrado=False):
    if not confirmar:raise SimulacaoInvalida('confirmation_required')
    key=uuid_publico(key)
    with Session(db.engine) as s,s.begin():
        administrador(s,uid)
        eid=s.execute(select(O.execucao_id).where(O.uuid==key)).scalar_one_or_none()
        if eid is None:raise SimulacaoInvalida('not_found')
        s.execute(select(E).where(E.id==eid).with_for_update()).scalar_one()
        o=s.execute(select(O).where(O.uuid==key).with_for_update()).scalar_one()
        if o.estado!='processando':raise SimulacaoInvalida('invalid_state')
        l=s.execute(select(L).where(L.operacao_id==o.id).with_for_update()).scalar_one_or_none()
        if l is None or l.estado=='legado':
            if not legado_encerrado:raise SimulacaoInvalida('legacy_executor_unconfirmed')
            if l is None:l=L(operacao_id=o.id,geracao=0,estado='legado');s.add(l)
        elif l.estado!='ativa' or aware(l.expira_em)>instante(s):raise SimulacaoInvalida('lease_active')
        ticket=Ticket(key,l.geracao+1,uuid4(),uid)
        configurar_fence(s,ticket);TICKET.set(ticket)
        l.geracao=ticket.geracao;l.executor_id=ticket.executor;l.responsavel_id=uid;l.estado='ativa'
        l.renovada_em=instante(s);l.expira_em=l.renovada_em+DURACAO
        auditar(s,uid,'lease_simulacao_recuperado','operacao_simulacao',o.id,f'Geração {l.geracao}; retomada somente leitura.')
        return ticket,eid,o.acao


def escopo_fence(fn):
    from functools import wraps
    @wraps(fn)
    def wrapped(*args,**kwargs):
        token=TICKET.set(kwargs.get('recuperada'))
        try:return fn(*args,**kwargs)
        finally:TICKET.reset(token)
    return wrapped


from sqlalchemy import event
@event.listens_for(Session,'before_flush')
def marcar_transacao(session,flush_context,instances):
    ticket=TICKET.get()
    simulated=any(obj.__class__.__module__ in ('app.models.simulacao','app.models.simulacao_resultados') for obj in session.new | session.dirty | session.deleted)
    if ticket is not None and simulated:configurar_fence(session,ticket)


def validar_capability(claim):
    ticket=TICKET.get()
    if ticket is None or claim.get('operacao')!=str(ticket.operacao_uuid) or claim.get('geracao')!=ticket.geracao or claim.get('executor')!=str(ticket.executor):raise ValueError()
    try:
        with Session(db.engine) as s,s.begin():
            administrador(s,ticket.responsavel)
            verificar(s,ticket.operacao_uuid)
    except SimulacaoInvalida:raise ValueError() from None


def cancelar(key,uid,confirmar=False,legado_encerrado=False):
    """Encerra uso local; não prova ausência nem cancela recurso externo."""
    from app.models.simulacao_resultados import VinculoCampanhaSimulacao as V,PonteEducativaSimulacao as B
    from app.models.simulacao import AlvoSimulacao as A
    if not confirmar:raise SimulacaoInvalida('confirmation_required')
    key=uuid_publico(key)
    token=TICKET.set(None)
    try:
        with Session(db.engine) as s,s.begin():
            administrador(s,uid)
            eid=s.execute(select(O.execucao_id).where(O.uuid==key)).scalar_one_or_none()
            if eid is None:raise SimulacaoInvalida('not_found')
            e=s.execute(select(E).where(E.id==eid).with_for_update()).scalar_one()
            o=s.execute(select(O).where(O.uuid==key).with_for_update()).scalar_one()
            if o.estado not in ('processando','resultado_indeterminado','falha'):raise SimulacaoInvalida('invalid_state')
            if s.execute(select(O.id).where(O.execucao_id==eid,O.estado=='processando',O.id!=o.id)).first():raise SimulacaoInvalida('busy')
            l=s.execute(select(L).where(L.operacao_id==o.id).with_for_update()).scalar_one_or_none()
            now=instante(s)
            if l is None or l.estado=='legado':
                if not legado_encerrado:raise SimulacaoInvalida('legacy_executor_unconfirmed')
                if l is None:l=L(operacao_id=o.id,geracao=0,estado='legado');s.add(l)
            elif l.estado=='ativa' and aware(l.expira_em)>now:raise SimulacaoInvalida('lease_active')
            ticket=Ticket(key,l.geracao+1,uuid4(),uid);TICKET.set(ticket);configurar_fence(s,ticket)
            l.geracao=ticket.geracao;l.executor_id=ticket.executor;l.responsavel_id=uid;l.estado='encerrada'
            l.renovada_em=now;l.expira_em=now+DURACAO;s.flush()
            e.estado='cancelada';o.estado='cancelada';o.finalizado_em=agora();o.codigo_erro='administratively_cancelled'
            v=s.execute(select(V).where(V.execucao_id==eid)).scalar_one_or_none()
            if v:v.estado='cancelado'
            bridges=s.execute(select(B).join(A,B.alvo_id==A.id).where(A.execucao_id==eid,B.estado.in_(['ativa','consumida']))).scalars().all()
            for b in bridges:
                b.estado='revogada';b.revogada_em=agora()
            if bridges:auditar(s,uid,'ponte_educativa_revogada','execucao_simulacao',eid,f'Quantidade {len(bridges)}; identificadores omitidos.')
            auditar(s,uid,'operacao_simulacao_cancelada','operacao_simulacao',o.id,'Encerramento local explícito; ausência externa não presumida; pontes revogadas.')
    finally:TICKET.reset(token)
