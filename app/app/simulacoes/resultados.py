"""Importação técnica, sem escrita em participação/progresso/quiz/feedback."""
from uuid import uuid4
from sqlalchemy import select,func,or_,literal
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.extensions import db
from app.models import ParticipacaoTreinamento as P
from app.models.simulacao import ExecucaoSimulacao as E, AlvoSimulacao as A, OperacaoSimulacao as O
from app.models.simulacao_resultados import VinculoCampanhaSimulacao as V, EventoSimulacao as F
from .preparacao import contexto, configuracao, uuid_publico, resultado_op
from .snapshots import SimulacaoInvalida, auditar, agora, email_aprovado
from .contracts import digest
from .leases import criar_lease,verificar,encerrar,TICKET,escopo_fence


def garantir_vinculo(s,e,r):
    v=s.execute(select(V).where(V.execucao_id==e.id).with_for_update()).scalar_one_or_none()
    if v is None:
        v=V(execucao_id=e.id,revisao_id=r.id,instancia_id=e.instancia_id,digest=r.digest,
            nome_tecnico=f'ONE-CAMP-{e.uuid.hex}-{r.digest}',estado='pendente')
        s.add(v);s.flush()
    if v.digest!=r.digest or v.instancia_id!=e.instancia_id:raise SimulacaoInvalida('binding_mismatch')
    return v


def reservar(eid,uid,acao,key,external_id=None):
    if acao not in ('vincular','importar'):raise SimulacaoInvalida('invalid_request')
    if acao=='vincular' and (type(external_id) is not int or external_id<=0):raise SimulacaoInvalida('invalid_request')
    with Session(db.engine) as s,s.begin():
        known=s.execute(select(V.externo_id).where(V.execucao_id==eid)).scalar_one_or_none()
        e,r,snap=contexto(s,eid,uid,atual=acao=='vincular' and known is None)
        existed=s.execute(select(V.id).where(V.execucao_id==eid)).first()
        v=garantir_vinculo(s,e,r)
        if not existed:auditar(s,uid,'vinculo_simulacao_criado','vinculo_campanha_simulacao',v.id,'Representação local; nenhuma campanha externa criada.')
        old=s.execute(select(O).where(O.uuid==key).with_for_update()).scalar_one_or_none()
        if old:
            if (old.execucao_id,old.acao,old.responsavel_id)!=(eid,acao,uid):raise SimulacaoInvalida('idempotency_conflict')
            if acao=='vincular' and old.resultado.get('campanha_externa',external_id)!=external_id:raise SimulacaoInvalida('idempotency_conflict')
            return resultado_op(old),None
        if s.execute(select(O.id).where(O.execucao_id==eid,O.estado=='processando')).first():raise SimulacaoInvalida('busy')
        if v.estado=='cancelado':raise SimulacaoInvalida('invalid_state')
        if acao=='importar' and (v.estado!='vinculado' or v.externo_id is None):raise SimulacaoInvalida('external_binding_required')
        if acao=='vincular' and v.externo_id not in (None,external_id):raise SimulacaoInvalida('binding_mismatch')
        attempt=(s.execute(select(func.max(O.tentativa)).where(O.execucao_id==eid,O.acao==acao)).scalar_one() or 0)+1
        o=O(uuid=key,execucao_id=eid,acao=acao,responsavel_id=uid,estado='processando',tentativa=attempt,
            resultado={'campanha_externa':external_id or v.externo_id})
        s.add(o);s.flush();criar_lease(s,o,uid)
        auditar(s,uid,'importacao_simulacao_iniciada' if acao=='importar' else 'vinculo_simulacao_solicitado',
            'execucao_simulacao',eid,'Consulta técnica somente leitura.')
        return None,external_id or v.externo_id


def proibir_token(s,values):
    values=sorted({v for v in values if v is not None})
    secret=configuracao().config.api_key
    if secret and any(secret in value for value in values):raise SimulacaoInvalida('secret_identifier_forbidden')
    if values and s.execute(select(P.id).where(P.token.in_(values)).limit(1)).first():raise SimulacaoInvalida('participation_token_forbidden')
    # Defesa também contra token real embutido em um identificador; nunca lê seu valor.
    for offset in range(0,len(values),64):
        batch=values[offset:offset+64]
        if s.execute(select(P.id).where(func.length(P.token)>=32,
            or_(*(literal(value).contains(P.token) for value in batch))).limit(1)).first():
            raise SimulacaoInvalida('participation_token_forbidden')


def identidade(s,e,snap,results):
    targets=list(s.execute(select(A).where(A.execucao_id==e.id).order_by(A.colaborador_id).with_for_update()).scalars())
    approved={p['colaborador_id']:p for p in snap['publico']}
    public=[{'colaborador_id':a.colaborador_id,'email':a.email_aprovado,'participacao_id':a.participacao_id} for a in targets]
    if digest(public)!=e.publico_digest or any(approved.get(p['colaborador_id'])!=p for p in public):raise SimulacaoInvalida('invalid_audience')
    by_rid={a.identificador_externo:a for a in targets if a.identificador_externo}
    by_mail={}
    for a in targets:by_mail.setdefault(a.email_aprovado,[]).append(a)
    assigned={};seen=set()
    for row in results:
        rid=row['rid'];email=row['email']
        if rid in seen:raise SimulacaoInvalida('ambiguous_result')
        seen.add(rid);target=by_rid.get(rid)
        normalized=email_aprovado(email) if email is not None else None
        if target:
            if normalized is not None and normalized!=target.email_aprovado:raise SimulacaoInvalida('outside_audience')
        else:
            matches=by_mail.get(normalized,[])
            if len(matches)!=1:raise SimulacaoInvalida('ambiguous_result' if len(matches)>1 else 'outside_audience')
            target=matches[0]
            if target.identificador_externo not in (None,rid):raise SimulacaoInvalida('ambiguous_result')
        if target.estado!='autorizado':raise SimulacaoInvalida('outside_audience')
        if target.id in assigned and assigned[target.id]!=rid:raise SimulacaoInvalida('ambiguous_result')
        assigned[target.id]=rid;target.identificador_externo=rid;by_rid[rid]=target
    return by_rid


def inserir_evento(s,e,target,event):
    identity={'instancia':e.instancia_id,'execucao':str(e.uuid)}
    if event['event_id'] is not None:identity.update(evento=event['event_id'])
    else:identity.update(resultado=event['rid'],tipo=event['tipo'],origem=event['origem'].isoformat(),discriminador=event['sequencia'])
    key=digest(identity)
    old=s.execute(select(F).where(F.chave_deduplicacao==key)).scalar_one_or_none()
    if old:
        from .leases import aware
        if (old.alvo_id,old.tipo,aware(old.origem_em),old.resultado_externo_id)!=(target.id,event['tipo'],event['origem'],event['rid']):raise SimulacaoInvalida('event_identity_conflict')
        return False
    values=dict(execucao_id=e.id,alvo_id=target.id,instancia_id=e.instancia_id,resultado_externo_id=event['rid'],
        evento_externo_id=event['event_id'],tipo=event['tipo'],origem_em=event['origem'],importado_em=agora(),
        chave_deduplicacao=key,discriminador=event['sequencia'],metadados_tecnicos={'contrato':1})
    dialect=s.bind.dialect.name
    if dialect=='postgresql':from sqlalchemy.dialects.postgresql import insert
    elif dialect=='sqlite':from sqlalchemy.dialects.sqlite import insert
    else:raise SimulacaoInvalida('unsupported_database')
    return s.execute(insert(F).values(**values).on_conflict_do_nothing(index_elements=['chave_deduplicacao']).returning(F.id)).first() is not None


def persistir(eid,uid,key,response,external_id):
    with Session(db.engine) as s,s.begin():
        e,r,snap=contexto(s,eid,uid,atual=False)
        o,l=verificar(s,key)
        v=garantir_vinculo(s,e,r)
        error=None;new=0;discarded=0
        if not response['ok']:
            error=response['status'];v.codigo_erro=error
            v.estado='resultado_indeterminado' if error in ('timeout','connection_error','result_indeterminate') else 'falha'
        else:
            data=response['data']
            if data['id']!=external_id or data['name']!=v.nome_tecnico or data['digest']!=v.digest:raise SimulacaoInvalida('binding_mismatch')
            conflict=s.execute(select(V.id).where(V.instancia_id==e.instancia_id,V.externo_id==external_id,V.execucao_id!=eid)).first()
            if conflict:raise SimulacaoInvalida('binding_mismatch')
            proibir_token(s,[row['rid'] for row in data['results']]+[row['rid'] for row in data['events']]+[row['event_id'] for row in data['events']])
            targets=identidade(s,e,snap,data['results']);s.flush()
            v.externo_id=external_id;v.estado='vinculado';v.codigo_erro=None
            if o.acao=='importar':
                discarded=data['descartados']
                for event in data['events']:
                    target=targets.get(event['rid'])
                    if target is None:raise SimulacaoInvalida('outside_audience')
                    new+=int(inserir_evento(s,e,target,event))
                v.descartados=discarded
                if discarded:auditar(s,uid,'eventos_simulacao_descartados','execucao_simulacao',eid,f'Descartados {discarded}; tipos/conteúdos omitidos.')
                auditar(s,uid,'eventos_simulacao_importados','execucao_simulacao',eid,f'Novos {new}; descartados {discarded}.')
            auditar(s,uid,'vinculo_simulacao_reconciliado','vinculo_campanha_simulacao',v.id,'Vínculo técnico validado.')
        v.reconciliado_em=agora()
        encerrar(s,key)
        o.estado='resultado_indeterminado' if error in ('timeout','connection_error','result_indeterminate') else 'falha' if error else 'concluida'
        o.codigo_erro=error;o.finalizado_em=agora();o.resultado={'campanha_externa':external_id,'eventos_novos':new,'descartados':discarded}
        auditar(s,uid,'operacao_simulacao_falhou' if error else 'operacao_simulacao_concluida','operacao_simulacao',o.id,f'Código {error or "ok"}.')
        return resultado_op(o)


def falhar(eid,uid,key,error):
    with Session(db.engine) as s,s.begin():
        s.execute(select(E).where(E.id==eid).with_for_update()).scalar_one()
        o,l=verificar(s,key);encerrar(s,key)
        o.estado='falha';o.codigo_erro=error;o.finalizado_em=agora()
        auditar(s,uid,'resultado_simulacao_ambiguo' if error in ('ambiguous_result','event_identity_conflict') else 'eventos_simulacao_descartados',
            'execucao_simulacao',eid,f'Janela rejeitada; código {error}.')
        return resultado_op(o)


@escopo_fence
def executar(eid,uid,acao,key,external_id=None,recuperada=None):
    key=uuid_publico(key);owned=False
    try:
        if recuperada is None:
            cached,campaign_id=reservar(eid,uid,acao,key,external_id)
            if cached is not None:return cached
        else:
            with Session(db.engine) as s,s.begin():
                e,r,snap=contexto(s,eid,uid,atual=False);o,l=verificar(s,key,renovar=True)
                if o.execucao_id!=eid or o.acao not in ('vincular','importar'):raise SimulacaoInvalida('forbidden')
                campaign_id=o.resultado.get('campanha_externa');acao=o.acao
        owned=True
        response=configuracao().ler_campanha_tecnica(campaign_id)
        return persistir(eid,uid,key,response,campaign_id)
    except SimulacaoInvalida as error:
        if owned and error.codigo!='fence_lost':return falhar(eid,uid,key,error.codigo)
        raise
    except SQLAlchemyError:raise SimulacaoInvalida('persistence_error') from None
