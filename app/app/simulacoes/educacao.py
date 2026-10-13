"""Ponte educativa: UUID dedicado, revogação e acesso ao portal por sessão."""
from datetime import timedelta
from uuid import UUID,uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from flask import session as browser_session
from app.extensions import db
from app.models import Campanha,Colaborador,ParticipacaoTreinamento as P,Treinamento
from app.models.simulacao import RevisaoSimulacao as R,ExecucaoSimulacao as E,AlvoSimulacao as A
from app.models.simulacao_resultados import PonteEducativaSimulacao as B,AssociacaoEducacionalSimulacao as S
from .snapshots import SimulacaoInvalida,administrador,auditar,agora,verificar_revisao
from .leases import aware

PREFIXO='one-session-'


def contexto_local(s,aid,uid=None,atual=False):
    eid=s.execute(select(A.execucao_id).where(A.id==aid)).scalar_one_or_none()
    if eid is None:raise SimulacaoInvalida('unavailable')
    e=s.execute(select(E).where(E.id==eid).with_for_update()).scalar_one()
    target=s.execute(select(A).where(A.id==aid).with_for_update()).scalar_one()
    r=s.get(R,e.revisao_id);c=s.get(Campanha,r.campanha_id)
    if uid is not None:administrador(s,uid)
    snap=verificar_revisao(s,r,c,atual=atual)
    frozen={p['colaborador_id']:p for p in snap['publico']}.get(target.colaborador_id)
    if not frozen or frozen['email']!=target.email_aprovado or frozen['participacao_id']!=target.participacao_id:
        raise SimulacaoInvalida('unavailable')
    col=s.get(Colaborador,target.colaborador_id)
    if e.estado=='cancelada' or target.estado!='autorizado' or not col or not col.ativo:raise SimulacaoInvalida('unavailable')
    return target,e,r


def participacao(s,target,revision):
    pid=target.participacao_id
    if pid is None:pid=s.execute(select(S.participacao_id).where(S.alvo_id==target.id)).scalar_one_or_none()
    if pid is None:return None
    p=s.execute(select(P.id).join(Treinamento,P.treinamento_id==Treinamento.id).where(P.id==pid,
        P.colaborador_id==target.colaborador_id,P.treinamento_id==revision.treinamento_id,
        P.campanha_id==revision.campanha_id,Treinamento.ativo.is_(True))).scalar_one_or_none()
    return p


def associar(aid,pid,uid,confirmar=False):
    if not confirmar:raise SimulacaoInvalida('confirmation_required')
    with Session(db.engine) as s,s.begin():
        a,e,r=contexto_local(s,aid,uid)
        if a.participacao_id is not None:raise SimulacaoInvalida('snapshot_participation_exists')
        p=s.execute(select(P.id).where(P.id==pid,P.colaborador_id==a.colaborador_id,
            P.treinamento_id==r.treinamento_id,P.campanha_id==r.campanha_id)).scalar_one_or_none()
        if p is None:raise SimulacaoInvalida('invalid_participation')
        old=s.execute(select(S).where(S.alvo_id==aid)).scalar_one_or_none()
        if old:
            if old.participacao_id!=pid:raise SimulacaoInvalida('binding_mismatch')
            return old.id
        row=S(alvo_id=aid,participacao_id=pid,responsavel_id=uid);s.add(row);s.flush()
        auditar(s,uid,'participacao_simulacao_associada','alvo_simulacao',aid,'Associação educacional explícita; snapshot preservado.')
        return row.id


def criar_participacao(aid,uid,confirmar=False):
    """Criação local explícita; não gera convite nem progresso educacional."""
    import secrets
    if not confirmar:raise SimulacaoInvalida('confirmation_required')
    with Session(db.engine) as s,s.begin():
        a,e,r=contexto_local(s,aid,uid,atual=True)
        pid=participacao(s,a,r)
        if pid is not None:return pid
        if a.participacao_id is not None:raise SimulacaoInvalida('invalid_participation')
        ids=s.execute(select(P.id).where(P.colaborador_id==a.colaborador_id,
            P.treinamento_id==r.treinamento_id,P.campanha_id==r.campanha_id)).scalars().all()
        if ids:raise SimulacaoInvalida('explicit_association_required')
        training=s.get(Treinamento,r.treinamento_id)
        if not training or not training.ativo:raise SimulacaoInvalida('unavailable')
        row=P(token=secrets.token_urlsafe(32),colaborador_id=a.colaborador_id,
            treinamento_id=r.treinamento_id,campanha_id=r.campanha_id)
        s.add(row);s.flush()
        s.add(S(alvo_id=aid,participacao_id=row.id,responsavel_id=uid))
        auditar(s,uid,'participacao_simulacao_associada','alvo_simulacao',aid,'Participação criada localmente por decisão explícita; nenhum convite ou progresso.')
        return row.id


def criar_ponte(aid,uid,key=None,horas=24,confirmar=False):
    if not confirmar:raise SimulacaoInvalida('confirmation_required')
    if type(horas) is not int or not 1<=horas<=168:raise SimulacaoInvalida('invalid_request')
    bid=UUID(str(key)) if key else uuid4()
    with Session(db.engine) as s,s.begin():
        a,e,r=contexto_local(s,aid,uid,atual=True)
        old=s.get(B,bid)
        if old:
            if old.alvo_id!=aid or old.responsavel_id!=uid:raise SimulacaoInvalida('idempotency_conflict')
            return old.id
        if s.execute(select(B.id).where(B.alvo_id==aid,B.estado.in_(['ativa','consumida']))).first():raise SimulacaoInvalida('bridge_exists')
        b=B(id=bid,alvo_id=aid,responsavel_id=uid,estado='ativa',criada_em=agora(),expira_em=agora()+timedelta(hours=horas))
        s.add(b);s.flush();auditar(s,uid,'ponte_educativa_criada','alvo_simulacao',aid,f'Validade {horas} horas; identificador omitido.')
        return b.id


def revogar_ponte(aid,uid,confirmar=False):
    if not confirmar:raise SimulacaoInvalida('confirmation_required')
    with Session(db.engine) as s,s.begin():
        administrador(s,uid)
        eid=s.execute(select(A.execucao_id).where(A.id==aid)).scalar_one()
        s.execute(select(E).where(E.id==eid).with_for_update()).scalar_one()
        s.execute(select(A).where(A.id==aid).with_for_update()).scalar_one()
        rows=s.execute(select(B).where(B.alvo_id==aid,B.estado.in_(['ativa','consumida'])).with_for_update()).scalars().all()
        for b in rows:b.estado='revogada';b.revogada_em=agora()
        auditar(s,uid,'ponte_educativa_revogada','alvo_simulacao',aid,'Identificadores omitidos.')


def resolver(s,identifier,consumida=False):
    try:bid=UUID(str(identifier))
    except (ValueError,TypeError,AttributeError):raise SimulacaoInvalida('unavailable') from None
    aid=s.execute(select(B.alvo_id).where(B.id==bid)).scalar_one_or_none()
    if aid is None:raise SimulacaoInvalida('unavailable')
    a,e,r=contexto_local(s,aid)
    b=s.execute(select(B).where(B.id==bid).with_for_update()).scalar_one_or_none()
    if not b or b.estado not in (('ativa','consumida') if consumida else ('ativa',)) or aware(b.expira_em)<=agora():raise SimulacaoInvalida('unavailable')
    return b,a,r


def visao_landing(identifier):
    with Session(db.engine) as s,s.begin():
        b,a,r=resolver(s,identifier)
        return {'treinamento_disponivel':participacao(s,a,r) is not None}


def acessar(identifier):
    with Session(db.engine) as s,s.begin():
        b,a,r=resolver(s,identifier,consumida=True)
        pid=participacao(s,a,r)
        if pid is None:raise SimulacaoInvalida('unavailable')
        current=browser_session.get('simulacao_acesso',{})
        if b.estado=='consumida':
            if current.get('ponte')!=str(b.id) or current.get('participacao')!=pid or current.get('expira',0)<=agora().timestamp():
                raise SimulacaoInvalida('unavailable')
            return current['alias']
        b.estado='consumida';b.consumida_em=agora()
        alias=PREFIXO+uuid4().hex
        expiration=min(aware(b.expira_em),agora()+timedelta(minutes=30)).timestamp()
        browser_session['simulacao_acesso']={'ponte':str(b.id),'participacao':pid,'alias':alias,'expira':expiration}
        return alias


def filtro_portal(token):
    if not isinstance(token,str) or not token.startswith(PREFIXO):return P.token==token
    grant=browser_session.get('simulacao_acesso',{})
    try:
        if grant.get('alias')!=token or grant.get('expira',0)<=agora().timestamp():raise SimulacaoInvalida('unavailable')
        with Session(db.engine) as s,s.begin():
            b,a,r=resolver(s,grant.get('ponte'),consumida=True)
            if b.estado!='consumida' or participacao(s,a,r)!=grant.get('participacao'):raise SimulacaoInvalida('unavailable')
        return P.id==grant['participacao']
    except (SimulacaoInvalida,ValueError,TypeError):return P.id==-1
