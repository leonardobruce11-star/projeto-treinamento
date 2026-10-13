"""Aprovação/snapshot em uma transação local, serializada pela campanha."""
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import select, func, literal, or_
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from email_validator import validate_email, EmailNotValidError
from flask import current_app
from app.extensions import db
from app.models import (Campanha, Usuario, Grupo, GrupoParticipante, Colaborador, Cenario,
    TemplateEmail, LandingPage, Treinamento, ModuloTreinamento, Questao, Alternativa,
    ParticipacaoTreinamento, Auditoria)
from app.models.simulacao import RevisaoSimulacao as R, ExecucaoSimulacao as E, OperacaoSimulacao as O
from app.integracoes.gophish.content import validar_conteudo, ConteudoInseguro
from .contracts import canonico, digest


class SimulacaoInvalida(ValueError):
    def __init__(self, codigo):
        # Chamadores só passam constantes internas, nunca exceções/payloads.
        self.codigo=codigo
        super().__init__(codigo)


def agora():return datetime.now(timezone.utc)


def ident(value):
    if type(value) is not int or value <= 0:raise SimulacaoInvalida('invalid_request')
    return value


def auditar(session, uid, acao, objeto, oid, detalhes=None):
    session.add(Auditoria(usuario_id=uid, acao=acao, objeto=objeto, objeto_id=oid, detalhes=detalhes))


def administrador(session, uid):
    u=session.execute(select(Usuario).where(Usuario.id==ident(uid)).with_for_update(read=True)).scalar_one_or_none()
    if not u or not u.ativo or u.perfil!='administrador':raise SimulacaoInvalida('forbidden')
    return u


def email_aprovado(value):
    if not isinstance(value,str) or any(ord(c)<32 for c in value):raise SimulacaoInvalida('invalid_email')
    try:
        return validate_email(value,check_deliverability=False,allow_smtputf8=False,
            test_environment=current_app.config.get('APP_ENV','development')!='production').normalized.casefold()
    except EmailNotValidError:raise SimulacaoInvalida('invalid_email') from None


def texto(value, limite):
    if not isinstance(value,str) or not value.strip() or len(value)>limite or any(ord(c)<32 for c in value):
        raise SimulacaoInvalida('invalid_content')
    return value


def data_planejada(value):
    if value is None:return None
    # Datas da configuração aprovada; não datas voláteis de auditoria.
    if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def snapshot(session, campanha, participacoes=None):
    if campanha.tipo!='simulacao_phishing':raise SimulacaoInvalida('invalid_type')
    refs={}; recursos={}
    for nome, model, field in [('grupo',Grupo,'grupo_id'),('cenario',Cenario,'cenario_id'),
                              ('template',TemplateEmail,'template_email_id'),('landing_page',LandingPage,'landing_page_id'),
                              ('treinamento',Treinamento,'treinamento_id')]:
        obj=session.execute(select(model).where(model.id==getattr(campanha,field)).with_for_update()).scalar_one_or_none()
        if not obj or not obj.ativo:raise SimulacaoInvalida('invalid_local_resource')
        refs[field]=obj.id;recursos[nome]=obj
    template=recursos['template'];page=recursos['landing_page'];train=recursos['treinamento']
    if template.cenario_id!=refs['cenario_id']:raise SimulacaoInvalida('scenario_mismatch')
    try:
        validar_conteudo(template.conteudo_html);validar_conteudo(page.conteudo_html)
    except ConteudoInseguro:raise SimulacaoInvalida('invalid_content') from None
    subject=texto(template.assunto,200)
    strings=[texto(campanha.nome,150),subject,template.conteudo_html,page.conteudo_html]
    key=current_app.extensions['gophish_config'].api_key
    if key and any(key in value for value in strings):raise SimulacaoInvalida('secret_forbidden')
    if any('/aprender/t/' in s or '{{token' in s.casefold() or 'participacao.token' in s for s in strings):
        raise SimulacaoInvalida('participation_token_forbidden')
    # Não carregar tokens: somente testar presença de tokens longos existentes.
    if session.execute(select(ParticipacaoTreinamento.id).where(
        func.length(ParticipacaoTreinamento.token)>=32,
        or_(*(literal(s).contains(ParticipacaoTreinamento.token) for s in strings))).limit(1)).first():
        raise SimulacaoInvalida('participation_token_forbidden')
    members=list(session.execute(select(GrupoParticipante.colaborador_id).where(
        GrupoParticipante.grupo_id==refs['grupo_id']).order_by(GrupoParticipante.colaborador_id).with_for_update()).scalars())
    public=[];excluded=[];emails=set()
    for cid in sorted(set(members)):
        c=session.execute(select(Colaborador).where(Colaborador.id==cid).with_for_update()).scalar_one()
        if not c.ativo:
            excluded.append({'colaborador_id':cid,'motivo':'inativo'});continue
        address=email_aprovado(c.email)
        if address in emails:raise SimulacaoInvalida('duplicate_email')
        emails.add(address)
        if participacoes is None:
            ids=list(session.execute(select(ParticipacaoTreinamento.id).where(
                ParticipacaoTreinamento.campanha_id==campanha.id,
                ParticipacaoTreinamento.colaborador_id==cid,
                ParticipacaoTreinamento.treinamento_id==train.id).order_by(ParticipacaoTreinamento.id)).scalars())
            if len(ids)>1:raise SimulacaoInvalida('ambiguous_participation')
            pid=ids[0] if ids else None
        else:
            pid=participacoes.get(cid)
            if pid is not None:
                if not session.execute(select(ParticipacaoTreinamento.id).where(
                    ParticipacaoTreinamento.id==pid,ParticipacaoTreinamento.colaborador_id==cid,
                    ParticipacaoTreinamento.campanha_id==campanha.id,
                    ParticipacaoTreinamento.treinamento_id==train.id)).first():raise SimulacaoInvalida('invalid_participation')
        public.append({'colaborador_id':cid,'email':address,'participacao_id':pid})
    if not public:raise SimulacaoInvalida('empty_audience')
    addresses=[p['email'] for p in public]
    if key and any(key in value for value in addresses):raise SimulacaoInvalida('secret_forbidden')
    if session.execute(select(ParticipacaoTreinamento.id).where(
        func.length(ParticipacaoTreinamento.token)>=32,
        or_(*(literal(value).contains(ParticipacaoTreinamento.token) for value in addresses))).limit(1)).first():
        raise SimulacaoInvalida('participation_token_forbidden')
    mods=list(session.execute(select(ModuloTreinamento).where(ModuloTreinamento.treinamento_id==train.id)
        .order_by(ModuloTreinamento.id).with_for_update()).scalars())
    questions=list(session.execute(select(Questao).where(Questao.treinamento_id==train.id)
        .order_by(Questao.id).with_for_update()).scalars())
    training={'id':train.id,'titulo':train.titulo,'descricao_digest':digest(train.descricao or ''),'categoria_id':train.categoria_id,
        'modulos':[{'id':m.id,'ordem':m.ordem,'digest':digest({'titulo':m.titulo,'conteudo':m.conteudo})} for m in mods],
        'questoes':[{'id':q.id,'ordem':q.ordem,'digest':digest(q.enunciado)} for q in questions]}
    # Respostas educativas não são copiadas; somente digest da configuração do quiz.
    qids=[q.id for q in questions]
    alternatives=list(session.execute(select(Alternativa).where(Alternativa.questao_id.in_(qids))
        .order_by(Alternativa.id).with_for_update()).scalars()) if qids else []
    training['alternativas_digest']=digest([{'id':a.id,'questao_id':a.questao_id,'texto':a.texto,'correta':a.correta} for a in alternatives])
    resources={'template':{'subject':subject,'html':template.conteudo_html,'text':'','attachments':[]},
               'landing_page':{'html':page.conteudo_html,'capture_credentials':False,'capture_passwords':False,'redirect_url':''}}
    return {'versao':1,'campanha_id':campanha.id,'tipo':'simulacao_phishing','nome':texto(campanha.nome,150),
            'configuracao':{'criador_id':campanha.criado_por_id,'descricao_digest':digest(campanha.descricao or ''),
                'data_inicio':data_planejada(campanha.data_inicio),'data_fim':data_planejada(campanha.data_fim)},
            'referencias':refs,'publico':public,'exclusoes':excluded,'recursos':resources,
            'cenario':{'id':recursos['cenario'].id,'nivel':recursos['cenario'].nivel},
            'treinamento_digest':digest(training),
            'digests':{**{k:digest(v) for k,v in resources.items()},'publico':digest(public)}}


def verificar_revisao(session, revisao, campanha, atual=True):
    if not revisao or revisao.estado!='aprovada' or campanha.tipo!='simulacao_phishing':
        raise SimulacaoInvalida('invalid_revision')
    snap=revisao.snapshot
    if digest(snap)!=revisao.digest or canonico(snap)!=revisao.snapshot_canonico:
        raise SimulacaoInvalida('invalid_revision')
    if atual:
        latest=session.execute(select(R.id).where(R.campanha_id==campanha.id).order_by(R.numero.desc()).limit(1)).scalar_one_or_none()
        if campanha.status!='aprovada' or latest!=revisao.id:raise SimulacaoInvalida('stale_revision')
        live=snapshot(session,campanha,{p['colaborador_id']:p['participacao_id'] for p in snap['publico']})
        if digest(live)!=revisao.digest:raise SimulacaoInvalida('snapshot_changed')
    return snap


@dataclass(frozen=True)
class Aprovacao:
    revisao_id:int
    criada:bool


def aprovar_simulacao(cid,uid):
    try:
        with Session(db.engine) as session,session.begin():
            campanha=session.execute(select(Campanha).where(Campanha.id==ident(cid)).with_for_update()).scalar_one_or_none()
            if not campanha:raise SimulacaoInvalida('not_found')
            administrador(session,uid)
            if campanha.tipo!='simulacao_phishing':raise SimulacaoInvalida('invalid_type')
            if campanha.criado_por_id==uid:raise SimulacaoInvalida('self_approval')
            latest=session.execute(select(R).where(R.campanha_id==cid).order_by(R.numero.desc()).limit(1)).scalar_one_or_none()
            if campanha.status=='aprovada' and latest:return Aprovacao(latest.id,False)
            if campanha.status!='aguardando_aprovacao':raise SimulacaoInvalida('invalid_state')
            snap=snapshot(session,campanha)
            campanha.status='aprovada';campanha.aprovado_por_id=uid;campanha.aprovado_em=agora();campanha.motivo_reprovacao=None
            session.flush()
            rev=R(campanha_id=cid,numero=1 if latest is None else latest.numero+1,digest=digest(snap),
                snapshot_canonico=canonico(snap),estado='aprovada',aprovado_por_id=uid,aprovado_em=campanha.aprovado_em,
                **snap['referencias'])
            session.add(rev);session.flush()
            auditar(session,uid,'campanha_aprovada','campanha',cid,'Aprovação local com revisão imutável.')
            auditar(session,uid,'revisao_simulacao_criada','revisao_simulacao',rev.id,f'Campanha {cid}; revisão {rev.numero}.')
            return Aprovacao(rev.id,True)
    except SQLAlchemyError:raise SimulacaoInvalida('persistence_error') from None


def reabrir_simulacao(cid,uid):
    with Session(db.engine) as session,session.begin():
        c=session.execute(select(Campanha).where(Campanha.id==ident(cid)).with_for_update()).scalar_one_or_none()
        administrador(session,uid)
        if not c or c.tipo!='simulacao_phishing' or c.status!='aprovada':raise SimulacaoInvalida('invalid_state')
        active=session.execute(select(O.id).join(E,O.execucao_id==E.id).join(R,E.revisao_id==R.id).where(
            R.campanha_id==cid,O.estado=='processando').limit(1)).first()
        if active:raise SimulacaoInvalida('busy')
        c.status='rascunho';c.aprovado_por_id=None;c.aprovado_em=None
        auditar(session,uid,'simulacao_reaberta','campanha',cid,'Nova aprovação necessária; revisões anteriores preservadas.')
