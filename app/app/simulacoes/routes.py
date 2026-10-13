"""Ações administrativas confirmadas, CSRF global e visualização mínima."""
from uuid import uuid4,UUID
from flask import Blueprint, abort, request, render_template, redirect, url_for, flash, current_app
from flask_login import current_user
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from itsdangerous import URLSafeTimedSerializer, BadData
from app.extensions import db
from app.convites.routes import administrador
from app.models import Campanha
from app.models.simulacao import RevisaoSimulacao as R, ExecucaoSimulacao as E, AlvoSimulacao as A, RecursoExternoSimulacao as X, OperacaoSimulacao as O
from .snapshots import SimulacaoInvalida, reabrir_simulacao
from .preparacao import criar_execucao, executar_operacao
from app.models import ParticipacaoTreinamento as P
from app.models.simulacao_resultados import VinculoCampanhaSimulacao as V,EventoSimulacao as F,LeaseOperacaoSimulacao as L,PonteEducativaSimulacao as B
from .educacao import participacao,criar_ponte,revogar_ponte,associar,criar_participacao
from .resultados import executar as importar_resultados
from .leases import cancelar
from .recuperacao import retomar
ACOES_D=('vincular','importar','recuperar','cancelar','criar_ponte','revogar_ponte','associar','criar_participacao')

simulacoes_bp=Blueprint('simulacoes',__name__,url_prefix='/simulacoes')


def assinador():return URLSafeTimedSerializer(current_app.secret_key,salt='simulacoes-confirmacao-18.7C')


@simulacoes_bp.after_request
def privacidade(response):
    response.headers['Cache-Control']='no-store';response.headers['Referrer-Policy']='same-origin'
    return response


@simulacoes_bp.get('/campanhas/<int:campanha_id>')
@administrador
def detalhe(campanha_id):
    with Session(db.engine) as s:
        c=s.get(Campanha,campanha_id)
        if not c or c.tipo!='simulacao_phishing':abort(404)
        reviews=[]
        for r in s.execute(select(R).where(R.campanha_id==campanha_id).order_by(R.numero.desc())).scalars():
            executions=[]
            for e in s.execute(select(E).where(E.revisao_id==r.id).order_by(E.id)).scalars():
                xs=[{'tipo':x.tipo,'estado':x.estado,'externo_id':x.externo_id,'codigo_erro':x.codigo_erro,'reconciliado_em':x.reconciliado_em} for x in s.execute(select(X).where(X.execucao_id==e.id).order_by(X.id)).scalars()]
                o=s.execute(select(O).where(O.execucao_id==e.id).order_by(O.id.desc()).limit(1)).scalar_one_or_none()
                v=s.execute(select(V).where(V.execucao_id==e.id)).scalar_one_or_none()
                l=s.get(L,o.id) if o else None
                counts=dict(s.execute(select(F.tipo,func.count(F.id)).where(F.execucao_id==e.id).group_by(F.tipo)).all())
                technical=[]
                for a in s.execute(select(A).where(A.execucao_id==e.id).order_by(A.id)).scalars():
                    pid=participacao(s,a,r)
                    progress=s.execute(select(P.iniciado_em,P.concluido_em).where(P.id==pid)).first() if pid else None
                    bridge=s.execute(select(B).where(B.alvo_id==a.id).order_by(B.criada_em.desc()).limit(1)).scalar_one_or_none()
                    technical.append({'id':a.id,'resultado':a.identificador_externo,'participacao_id':pid,
                        'educacional':'concluído' if progress and progress.concluido_em else 'iniciado' if progress and progress.iniciado_em else 'não iniciado' if pid else 'sem participação',
                        'eventos':[{'tipo':f.tipo,'origem':f.origem_em} for f in s.execute(select(F).where(F.alvo_id==a.id).order_by(F.origem_em)).scalars()],
                        'ponte':None if bridge is None else {'estado':bridge.estado,'expira':bridge.expira_em,'url':url_for('educacao_simulacao.landing',identificador=str(bridge.id)) if bridge.estado=='ativa' else None}})
                executions.append({'id':e.id,'fase':e.fase,'sequencia':e.sequencia,'estado':e.estado,'instancia':e.instancia_id,
                    'alvos':s.execute(select(func.count(A.id)).where(A.execucao_id==e.id)).scalar_one(),
                    'recursos':xs,'tecnicos':technical,'contagens':counts,'ultimo_evento':s.execute(select(func.max(F.importado_em)).where(F.execucao_id==e.id)).scalar_one(),
                    'vinculo':None if v is None else {'estado':v.estado,'externo_id':v.externo_id,'reconciliado_em':v.reconciliado_em,'descartados':v.descartados,'codigo_erro':v.codigo_erro},
                    'lease':None if l is None else {'geracao':l.geracao,'estado':l.estado,'expira':l.expira_em},'operacao':None if o is None else {'uuid':str(o.uuid),'estado':o.estado,'codigo_erro':o.codigo_erro}})
            reviews.append({'id':r.id,'numero':r.numero,'digest':r.digest,'alvos':len(r.snapshot['publico']),
                'publico':[p['colaborador_id'] for p in r.snapshot['publico']],'execucoes':executions})
        view={'id':c.id,'nome':c.nome,'status':c.status}
    return render_template('simulacoes/detalhe.html',campanha=view,revisoes=reviews)


@simulacoes_bp.post('/campanhas/<int:campanha_id>/confirmar')
@administrador
def confirmar(campanha_id):
    action=request.form.get('acao')
    if action not in ('criar_execucao','preparar','reconciliar','reabrir')+ACOES_D:abort(400)
    with Session(db.engine) as s:
        c=s.get(Campanha,campanha_id)
        if not c or c.tipo!='simulacao_phishing':abort(404)
        data={'campanha':c.id,'usuario':current_user.id,'acao':action,'uuid':str(uuid4())}
        scope={}
        if action=='criar_execucao':
            try:rid=int(request.form['revisao']);selected=[int(v) for v in request.form.getlist('selecionados')]
            except (ValueError,KeyError):abort(400)
            r=s.get(R,rid)
            if not r or r.campanha_id!=c.id:abort(400)
            data.update(revisao=rid,digest=r.digest,fase=request.form.get('fase'),selecionados=selected)
            scope={'revisao':r.numero,'digest':r.digest,'fase':data['fase'],
                'alvos':len(set(selected)) if data['fase']=='piloto' else len(r.snapshot['publico']),
                'instancia':current_app.extensions['gophish_config'].instance_id}
        elif action in ('preparar','reconciliar')+ACOES_D:
            try:e=s.get(E,int(request.form['execucao']))
            except (ValueError,KeyError):abort(400)
            r=s.get(R,e.revisao_id) if e else None
            if not r or r.campanha_id!=c.id:abort(400)
            data.update(execucao=e.id,revisao=r.id,digest=r.digest)
            try:
                if action=='vincular':data['externo_id']=int(request.form['externo_id'])
                if action in ('recuperar','cancelar'):
                    op=s.execute(select(O).where(O.uuid==UUID(request.form['operacao']),O.execucao_id==e.id)).scalar_one_or_none()
                    if not op:abort(400)
                    data['operacao']=str(op.uuid);data['legado_encerrado']=request.form.get('legado_encerrado')=='sim'
                if action in ('criar_ponte','revogar_ponte','associar','criar_participacao'):
                    a=s.get(A,int(request.form['alvo']))
                    if not a or a.execucao_id!=e.id:abort(400)
                    data['alvo']=a.id
                    if action=='associar':data['participacao']=int(request.form['participacao'])
            except (ValueError,KeyError,TypeError):abort(400)
            scope={'revisao':r.numero,'digest':r.digest,'fase':e.fase,
                'alvos':s.execute(select(func.count(A.id)).where(A.execucao_id==e.id)).scalar_one(),'instancia':e.instancia_id,'alvo':data.get('alvo'),'externo_id':data.get('externo_id'),'operacao':data.get('operacao'),'legado_encerrado':data.get('legado_encerrado')}
        name=c.nome
    return render_template('simulacoes/confirmar.html',campanha_id=campanha_id,nome=name,acao=action,escopo=scope,confirmacao=assinador().dumps(data))


@simulacoes_bp.post('/campanhas/<int:campanha_id>/executar')
@administrador
def executar(campanha_id):
    try:
        data=assinador().loads(request.form.get('confirmacao',''),max_age=900)
        uid=current_user.id
        if request.form.get('confirmar')!='sim' or data['campanha']!=campanha_id or data['usuario']!=uid:raise ValueError()
    except (BadData,ValueError,KeyError,TypeError):abort(400)
    # Encerra a transação da autenticação antes de qualquer transporte.
    db.session.rollback()
    try:
        if data['acao']=='criar_execucao':criar_execucao(data['revisao'],uid,data['fase'],data['selecionados'],data['uuid'])
        elif data['acao'] in ('preparar','reconciliar'):executar_operacao(data['execucao'],uid,data['acao'],data['uuid'])
        elif data['acao'] in ('vincular','importar'):
            importar_resultados(data['execucao'],uid,data['acao'],data['uuid'],data.get('externo_id'))
        elif data['acao']=='recuperar':
            retomar(data['operacao'],uid,confirmar=True,legado_encerrado=data['legado_encerrado'])
        elif data['acao']=='cancelar':cancelar(data['operacao'],uid,confirmar=True,legado_encerrado=data['legado_encerrado'])
        elif data['acao']=='criar_ponte':criar_ponte(data['alvo'],uid,key=data['uuid'],confirmar=True)
        elif data['acao']=='revogar_ponte':revogar_ponte(data['alvo'],uid,confirmar=True)
        elif data['acao']=='associar':associar(data['alvo'],data['participacao'],uid,confirmar=True)
        elif data['acao']=='criar_participacao':criar_participacao(data['alvo'],uid,confirmar=True)
        elif data['acao']=='reabrir':reabrir_simulacao(campanha_id,uid)
        else:abort(400)
        flash('Ação registrada. Consulte o estado e o código da operação.','success')
    except SimulacaoInvalida as erro:flash('Ação bloqueada: '+erro.codigo+'.','warning')
    return redirect(url_for('simulacoes.detalhe',campanha_id=campanha_id))
