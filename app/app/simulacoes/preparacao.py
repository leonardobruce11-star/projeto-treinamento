"""Recursos auxiliares: reserva/commit, transporte, reconciliação/commit."""
from uuid import UUID, uuid4
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from flask import current_app
from itsdangerous import URLSafeTimedSerializer
from app.extensions import db
from app.models import Campanha
from app.models.simulacao import (RevisaoSimulacao as R, ExecucaoSimulacao as E,
    AlvoSimulacao as A, RecursoExternoSimulacao as X, OperacaoSimulacao as O)
from app.integracoes.gophish.service import servico_gophish
from .snapshots import SimulacaoInvalida, administrador, ident, verificar_revisao, auditar, agora
from .contracts import TIPOS, digest, payload_grupo, normalizar


def uuid_publico(value):
    try:return UUID(str(value))
    except (ValueError,TypeError,AttributeError):raise SimulacaoInvalida('invalid_request') from None


def configuracao():
    service=servico_gophish(); config=service.config
    if config.estado()!='ready':raise SimulacaoInvalida(config.estado())
    if config.backend=='http' and current_app.config.get('APP_ENV') not in ('production','homologation','homologacao'):
        raise SimulacaoInvalida('environment_forbidden')
    if config.send_enabled:raise SimulacaoInvalida('forbidden')
    return service


def contexto(session,eid,uid,atual=True):
    cid=session.execute(select(R.campanha_id).join(E,E.revisao_id==R.id).where(E.id==ident(eid))).scalar_one_or_none()
    if cid is None:raise SimulacaoInvalida('not_found')
    camp=session.execute(select(Campanha).where(Campanha.id==cid).with_for_update()).scalar_one()
    administrador(session,uid)
    e=session.execute(select(E).where(E.id==eid).with_for_update()).scalar_one()
    r=session.execute(select(R).where(R.id==e.revisao_id).with_for_update()).scalar_one()
    snap=verificar_revisao(session,r,camp,atual=atual)
    config=configuracao().config
    if e.instancia_id!=config.instance_id or e.origem_digest!=config.origem_digest():raise SimulacaoInvalida('instance_mismatch')
    if e.estado=='cancelada':raise SimulacaoInvalida('invalid_state')
    return e,r,snap


def criar_execucao(rid,uid,fase,selecionados=None,public_uuid=None):
    config=configuracao().config
    if fase not in ('piloto','ampliada'):raise SimulacaoInvalida('invalid_phase')
    key=uuid_publico(public_uuid) if public_uuid else uuid4()
    try:
        with Session(db.engine) as s,s.begin():
            cid=s.execute(select(R.campanha_id).where(R.id==ident(rid))).scalar_one_or_none()
            if cid is None:raise SimulacaoInvalida('not_found')
            c=s.execute(select(Campanha).where(Campanha.id==cid).with_for_update()).scalar_one()
            administrador(s,uid)
            r=s.execute(select(R).where(R.id==rid).with_for_update()).scalar_one()
            snap=verificar_revisao(s,r,c)
            audience=snap['publico']
            if fase=='piloto':
                if not isinstance(selecionados,(list,tuple)) or not selecionados:raise SimulacaoInvalida('invalid_audience')
                chosen=set(ident(cid) for cid in selecionados)
                if len(chosen)>3 or not chosen.issubset({p['colaborador_id'] for p in audience}):raise SimulacaoInvalida('invalid_audience')
                audience=[p for p in audience if p['colaborador_id'] in chosen]
            elif selecionados:raise SimulacaoInvalida('invalid_audience')
            pd=digest(audience)
            old=s.execute(select(E).where(E.revisao_id==rid,E.fase==fase)).scalar_one_or_none()
            if old:
                if old.publico_digest!=pd or old.instancia_id!=config.instance_id or old.origem_digest!=config.origem_digest():raise SimulacaoInvalida('binding_mismatch')
                return old.id
            if s.execute(select(E.id).where(E.uuid==key)).first():raise SimulacaoInvalida('idempotency_conflict')
            sequence=(s.execute(select(func.max(E.sequencia)).join(R,E.revisao_id==R.id).where(R.campanha_id==cid,E.fase==fase)).scalar_one() or 0)+1
            e=E(uuid=key,revisao_id=rid,fase=fase,sequencia=sequence,estado='nao_preparada',responsavel_id=uid,
                instancia_id=config.instance_id,origem_digest=config.origem_digest(),publico_digest=pd)
            s.add(e);s.flush()
            for p in audience:s.add(A(execucao_id=e.id,colaborador_id=p['colaborador_id'],participacao_id=p['participacao_id'],email_aprovado=p['email'],estado='autorizado'))
            for tipo,content in {'grupo':payload_grupo(audience),**snap['recursos']}.items():
                cd=digest(normalizar(tipo,content))
                s.add(X(execucao_id=e.id,revisao_id=rid,instancia_id=config.instance_id,tipo=tipo,
                    nome_externo=f'ONE-{e.uuid.hex}-{tipo}-{cd}',digest=cd,estado='pendente'))
            auditar(s,uid,'execucao_simulacao_criada','execucao_simulacao',e.id,f'Revisão {rid}; fase {fase}; alvos {len(audience)}.')
            return e.id
    except SQLAlchemyError:raise SimulacaoInvalida('persistence_error') from None


def resultado_op(o):return {'uuid':str(o.uuid),'estado':o.estado,'codigo_erro':o.codigo_erro,'resultado':o.resultado or {}}


def reservar(eid,uid,acao,key):
    if acao not in ('preparar','reconciliar'):raise SimulacaoInvalida('invalid_request')
    with Session(db.engine) as s,s.begin():
        e,r,snap=contexto(s,eid,uid,atual=acao=='preparar')
        old=s.execute(select(O).where(O.uuid==key).with_for_update()).scalar_one_or_none()
        if old:
            if (old.execucao_id,old.acao,old.responsavel_id)!=(eid,acao,uid):raise SimulacaoInvalida('idempotency_conflict')
            return resultado_op(old),None
        if s.execute(select(O.id).where(O.execucao_id==eid,O.estado=='processando')).first():raise SimulacaoInvalida('busy')
        if acao=='preparar' and e.estado in ('preparada','resultado_indeterminado'):
            raise SimulacaoInvalida('already_prepared' if e.estado=='preparada' else 'reconciliation_required')
        targets=list(s.execute(select(A).where(A.execucao_id==eid).order_by(A.colaborador_id).with_for_update()).scalars())
        audience=[{'colaborador_id':a.colaborador_id,'email':a.email_aprovado,'participacao_id':a.participacao_id} for a in targets]
        if not audience or digest(audience)!=e.publico_digest or any(a.estado!='autorizado' for a in targets):raise SimulacaoInvalida('invalid_audience')
        approved={p['colaborador_id']:p for p in snap['publico']}
        if any(approved.get(p['colaborador_id'])!=p for p in audience):raise SimulacaoInvalida('invalid_audience')
        payloads={'grupo':payload_grupo(audience),**snap['recursos']}
        resources=list(s.execute(select(X).where(X.execucao_id==eid).order_by(X.id).with_for_update()).scalars())
        if {x.tipo for x in resources}!=set(TIPOS):raise SimulacaoInvalida('invalid_resources')
        tasks=[]
        for x in resources:
            content=normalizar(x.tipo,payloads[x.tipo])
            if digest(content)!=x.digest:raise SimulacaoInvalida('binding_mismatch')
            if acao=='preparar' and x.criacao_incerta:raise SimulacaoInvalida('reconciliation_required')
            tasks.append({'id':x.id,'tipo':x.tipo,'nome':x.nome_externo,'digest':x.digest,'externo_id':x.externo_id,
                'estado':x.estado,'incerta':x.criacao_incerta,'payload':{'name':x.nome_externo,**content}})
        attempt=(s.execute(select(func.max(O.tentativa)).where(O.execucao_id==eid,O.acao==acao)).scalar_one() or 0)+1
        o=O(uuid=key,execucao_id=eid,acao=acao,estado='processando',responsavel_id=uid,tentativa=attempt,resultado={})
        s.add(o);s.flush()
        from .leases import criar_lease
        criar_lease(s,o,uid)
        e.estado='preparando';e.preparacao_iniciada_em=agora()
        auditar(s,uid,'preparacao_simulacao_solicitada' if acao=='preparar' else 'reconciliacao_simulacao_solicitada',
            'execucao_simulacao',eid,f'Operação {key}; recursos auxiliares.')
        return None,tasks


def checkpoint(eid,key,uid,t,state,error=None,external_id=None,uncertain=False):
    with Session(db.engine) as s,s.begin():
        from .leases import verificar
        contexto(s,eid,uid,atual=state=='sincronizando')
        verificar(s,key,renovar=True)
        o=s.execute(select(O).where(O.uuid==key,O.execucao_id==eid).with_for_update()).scalar_one()
        if o.estado!='processando':raise SimulacaoInvalida('invalid_state')
        x=s.execute(select(X).where(X.id==t['id'],X.execucao_id==eid).with_for_update()).scalar_one()
        x.estado=state;x.codigo_erro=error;x.criacao_incerta=uncertain;x.reconciliado_em=agora()
        if state=='ausente' and error=='not_found':x.externo_id=None
        elif external_id is not None:x.externo_id=external_id
        auditar(s,uid,'recurso_simulacao_reconciliado','recurso_externo_simulacao',x.id,f'Tipo {x.tipo}; estado {state}; código {error or "ok"}.')


def finalizar(eid,key,uid,error=None,uncertain=False):
    with Session(db.engine) as s,s.begin():
        e=s.execute(select(E).where(E.id==eid).with_for_update()).scalar_one()
        o=s.execute(select(O).where(O.uuid==key).with_for_update()).scalar_one()
        if o.estado!='processando':raise SimulacaoInvalida('invalid_state')
        from .leases import encerrar
        encerrar(s,key)
        xs=list(s.execute(select(X).where(X.execucao_id==eid).order_by(X.id)).scalars())
        ready=all(x.estado=='pronta' and x.externo_id for x in xs)
        uncertain=uncertain or any(x.criacao_incerta or x.estado=='resultado_indeterminado' for x in xs)
        o.estado='resultado_indeterminado' if uncertain else 'falha' if error else 'concluida'
        o.codigo_erro=error;o.finalizado_em=agora();o.resultado={'recursos':{x.tipo:x.externo_id for x in xs if x.externo_id is not None}}
        e.estado='resultado_indeterminado' if uncertain else 'falha' if error else 'preparada' if ready else 'nao_preparada'
        e.reconciliada_em=agora()
        if e.estado=='preparada':e.preparada_em=agora()
        action='operacao_simulacao_indeterminada' if uncertain else 'operacao_simulacao_falhou' if error else 'operacao_simulacao_concluida'
        auditar(s,uid,action,'operacao_simulacao',o.id,f'Ação {o.acao}; código {error or "ok"}.')
        return resultado_op(o)


def corresponde(data,t):return isinstance(data,dict) and type(data.get('id')) is int and data['id']>0 and data.get('name')==t['nome'] and data.get('digest')==t['digest']


def executar_operacao(eid,uid,acao,public_uuid,recuperada=None):
    key=uuid_publico(public_uuid)
    owned=False
    try:
        if recuperada is None:cached,tasks=reservar(eid,uid,acao,key)
        else:
            if acao!='reconciliar':raise SimulacaoInvalida('forbidden')
            cached,tasks=None,tarefas_recuperadas(eid,uid,key)
        if cached is not None:return cached
        owned=True
        service=configuracao()
        for t in tasks:
            if t['externo_id'] is not None and t['estado']!='ausente':
                response=service.consultar_auxiliar(t['tipo'],t['externo_id'])
                if response['status']=='not_found':
                    checkpoint(eid,key,uid,t,'ausente','not_found')
                    if acao=='preparar':return finalizar(eid,key,uid,'not_found')
                    continue
                if not response['ok']:
                    code=response['status'];unknown=code in ('timeout','result_indeterminate','connection_error')
                    checkpoint(eid,key,uid,t,'resultado_indeterminado' if unknown else 'falha',code,uncertain=t['incerta'])
                    return finalizar(eid,key,uid,code,unknown)
                if not corresponde(response['data'],t):
                    checkpoint(eid,key,uid,t,'falha','binding_mismatch',uncertain=t['incerta'])
                    return finalizar(eid,key,uid,'binding_mismatch',t['incerta'])
                checkpoint(eid,key,uid,t,'pronta',external_id=response['data']['id']);continue
            response=service.localizar_auxiliar(t['tipo'],t['nome'])
            if not response['ok']:
                code=response['status'];unknown=t['incerta'] or code in ('timeout','result_indeterminate','connection_error')
                checkpoint(eid,key,uid,t,'resultado_indeterminado' if unknown else 'falha',code,uncertain=t['incerta'])
                return finalizar(eid,key,uid,code,unknown)
            found=response['data']
            if len(found)>1 or (found and not corresponde(found[0],t)):
                checkpoint(eid,key,uid,t,'falha','binding_mismatch',uncertain=t['incerta'])
                return finalizar(eid,key,uid,'binding_mismatch',t['incerta'])
            if found:
                checkpoint(eid,key,uid,t,'pronta',external_id=found[0]['id']);continue
            if t['incerta']:
                checkpoint(eid,key,uid,t,'resultado_indeterminado','reconciliation_required',uncertain=True)
                return finalizar(eid,key,uid,'reconciliation_required',True)
            if acao=='reconciliar':
                # Lista vazia de recurso nunca criado não é um 404 confirmado.
                absent=t['estado']=='ausente'
                checkpoint(eid,key,uid,t,'ausente' if absent else 'pendente','not_found' if absent else None)
                continue
            checkpoint(eid,key,uid,t,'sincronizando',uncertain=True)
            from .leases import TICKET
            ticket=TICKET.get()
            token=URLSafeTimedSerializer(current_app.secret_key,salt='gophish-aux-18.7C').dumps({
                'operacao':str(key),'geracao':ticket.geracao,'executor':str(ticket.executor),'tipo':t['tipo'],'nome':t['nome'],'digest':t['digest'],
                'instancia':service.config.instance_id,'origem':service.config.origem_digest()})
            response=service.criar_auxiliar_aprovado(t['tipo'],t['payload'],token)
            if not response['ok'] or not corresponde(response.get('data'),t):
                code=response['status'] if not response['ok'] else 'binding_mismatch'
                unknown=code not in ('auth_error','forbidden','not_found','tls_error','invalid_content','invalid_request','disabled','not_configured')
                checkpoint(eid,key,uid,t,'resultado_indeterminado' if unknown else 'falha',code,uncertain=unknown)
                return finalizar(eid,key,uid,code,unknown)
            checkpoint(eid,key,uid,t,'pronta',external_id=response['data']['id'])
        return finalizar(eid,key,uid)
    except SimulacaoInvalida as erro:
        if not owned:raise
        try:return finalizar(eid,key,uid,erro.codigo)
        except SQLAlchemyError:raise SimulacaoInvalida('persistence_error') from None
    except SQLAlchemyError:raise SimulacaoInvalida('persistence_error') from None


def tarefas_recuperadas(eid,uid,key):
    from .leases import verificar
    with Session(db.engine) as s,s.begin():
        e,r,snap=contexto(s,eid,uid,atual=False)
        o,l=verificar(s,key,renovar=True)
        if o.execucao_id!=eid or o.acao not in ('preparar','reconciliar'):raise SimulacaoInvalida('forbidden')
        xs=s.execute(select(X).where(X.execucao_id==eid).order_by(X.id).with_for_update()).scalars().all()
        return [{'id':x.id,'tipo':x.tipo,'nome':x.nome_externo,'digest':x.digest,'externo_id':x.externo_id,
            'estado':x.estado,'incerta':x.criacao_incerta,'payload':None} for x in xs]


from .leases import escopo_fence
executar_operacao=escopo_fence(executar_operacao)
