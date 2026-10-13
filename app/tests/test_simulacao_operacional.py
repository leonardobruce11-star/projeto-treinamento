"""18.7E: validação operacional integrada; fixtures isoladas, fake e guards de rede."""
import io,json,logging,os,subprocess,sys,unittest
from pathlib import Path
from uuid import uuid4
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier,Event
from unittest.mock import patch
from sqlalchemy import select,text,inspect
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from alembic import command
import test_gophish_seguro as baseline
import test_simulacao_preparacao as anterior
import test_simulacao_resultados as resultados_test
from app.extensions import db
from app.models import Colaborador,GrupoParticipante,ParticipacaoTreinamento as P,ResultadoQuiz,FeedbackTreinamento,TempoTreinamento,Auditoria,Campanha
from app.models.simulacao import RevisaoSimulacao as R,ExecucaoSimulacao as E,AlvoSimulacao as A,RecursoExternoSimulacao as X,OperacaoSimulacao as O
from app.models.simulacao_resultados import EventoSimulacao as F,VinculoCampanhaSimulacao as V,PonteEducativaSimulacao as B,LeaseOperacaoSimulacao as L
from app.simulacoes import resultados,educacao,leases,preparacao
from app.simulacoes.snapshots import aprovar_simulacao,SimulacaoInvalida,agora
from app.integracoes.gophish.config import ConfiguracaoGophish
from app.integracoes.gophish.service import GophishService


class OperacionalTest(unittest.TestCase):
    setUp=baseline.FluxoTest.setUp
    login=baseline.FluxoTest.login
    post=baseline.FluxoTest.post
    enable=baseline.FluxoTest.enable
    preparar_base=anterior.PreparacaoTest.preparar_base
    bloqueado=anterior.PreparacaoTest.bloqueado
    base=resultados_test.ResultadosTest.base
    window=resultados_test.ResultadosTest.window
    run_action=resultados_test.ResultadosTest.run_action
    evt=resultados_test.ResultadosTest.evt
    expired_operation=resultados_test.ResultadosTest.expired_operation

    def educacional(self):
        db.session.expire_all()
        return {'participacoes':[(p.id,p.iniciado_em,p.ultimo_modulo_id,p.concluido_em) for p in P.query.order_by(P.id)],
            'quiz':[(q.id,q.participacao_id,q.total_acertos,q.concluido_em) for q in ResultadoQuiz.query.order_by(ResultadoQuiz.id)],
            'feedback':[(f.id,f.participacao_id,f.comentario,f.criado_em) for f in FeedbackTreinamento.query.order_by(FeedbackTreinamento.id)],
            'tempo':[(t.id,t.participacao_id,t.segundos_ativos,t.ultima_atividade_em,t.atualizado_em) for t in TempoTreinamento.query.order_by(TempoTreinamento.id)]}

    def prepared(self):
        self.preparar_base();db.session.rollback()
        self.assertEqual(preparacao.executar_operacao(self.eid,self.admin,'preparar',uuid4())['estado'],'concluida')
        self.aid=A.query.one().id;e=db.session.get(E,self.eid);r=db.session.get(R,self.rid)
        self.nome=f'ONE-CAMP-{e.uuid.hex}-{r.digest}';self.sha=r.digest
        self.window([]);self.assertEqual(self.run_action('vincular')['estado'],'concluida')
        self.fake.chamadas.clear()

    def test_end_to_end_21_steps_and_explicit_training_start(self):
        other=Colaborador(nome='Outra pessoa privada',email='second@example.test',departamento_id=1)
        db.session.add(other);db.session.flush();db.session.add(GrupoParticipante(grupo_id=1,colaborador_id=other.id))
        tokens=['SYNTRAINING-'+uuid4().hex,'SYNTRAINING-'+uuid4().hex]
        for cid,token in zip([1,other.id],tokens):db.session.add(P(token=token,colaborador_id=cid,treinamento_id=1,campanha_id=self.campaign.id))
        db.session.commit();self.cid=self.campaign.id;self.service=self.enable();self.fake=self.service.backend
        self.assertEqual(self.post('solicitar-aprovacao').status_code,302);self.assertFalse(self.fake.chamadas)
        self.login(self.admin);self.assertEqual(self.post('aprovar').status_code,302)
        r=R.query.one();self.rid=r.id;self.assertEqual(r.aprovado_por_id,self.admin);self.assertNotEqual(self.admin,self.creator)
        self.assertEqual(len(r.snapshot['publico']),2);snapshot=r.snapshot_canonico;db.session.rollback()
        self.eid=preparacao.criar_execucao(self.rid,self.admin,'ampliada');self.assertEqual(A.query.count(),2)
        public=[(a.colaborador_id,a.email_aprovado,a.participacao_id) for a in A.query.order_by(A.colaborador_id)]
        payloads=[];orig=self.fake.request
        def transport(method,endpoint,payload=None,**kwargs):
            if payload is not None:payloads.append(json.dumps(payload))
            return orig(method,endpoint,payload,**kwargs)
        db.session.rollback()
        with patch.object(self.fake,'request',side_effect=transport):
            self.assertEqual(preparacao.executar_operacao(self.eid,self.admin,'preparar',uuid4())['estado'],'concluida')
        self.assertEqual(len(self.fake.recursos['groups']),1);self.assertEqual(len(self.fake.recursos['templates']),1);self.assertEqual(len(self.fake.recursos['pages']),1)
        self.assertEqual(self.fake.recursos['campaigns'],{});self.assertEqual(self.fake.recursos['smtp'],{})
        for token in tokens:self.assertNotIn(token,str(payloads))
        self.assertNotIn(baseline.KEY,str(payloads));self.assertNotIn('Outra pessoa privada',str(payloads))
        self.assertEqual(public,[(a.colaborador_id,a.email_aprovado,a.participacao_id) for a in A.query.order_by(A.colaborador_id)])
        e=db.session.get(E,self.eid);self.nome=f'ONE-CAMP-{e.uuid.hex}-{r.digest}';self.sha=r.digest
        self.window([],results=[{'rid':'rid-one','email':public[0][1]},{'rid':'rid-two','email':public[1][1]}])
        self.assertEqual(self.run_action('vincular')['estado'],'concluida');self.assertEqual(V.query.one().externo_id,101)
        before=self.educacional();self.window([self.evt('Email Sent','sent'),self.evt('Email Opened','open'),self.evt('Clicked Link','click'),self.evt('Clicked Link','click'),{'message':'Submitted Data','password':'DO-NOT-PERSIST','details':tokens[0]}],results=[{'rid':'rid-one'},{'rid':'rid-two'}])
        out=self.run_action();self.assertEqual(out['resultado'],{'campanha_externa':101,'eventos_novos':3,'descartados':1})
        self.assertEqual(self.run_action()['resultado']['eventos_novos'],0);self.assertEqual(F.query.count(),3);self.assertEqual(before,self.educacional())
        target=A.query.filter_by(identificador_externo='rid-one').one();aid=target.id;pid=target.participacao_id;db.session.rollback()
        bid=educacao.criar_ponte(aid,self.admin,confirmar=True);self.login(None);path='/simulacao/educacao/'+str(bid)
        response=self.client.get(path);html=response.get_data(as_text=True);self.assertEqual(response.status_code,200)
        for bad in ('password','<script','<iframe','https://','http://','Outra pessoa privada','second@example.test',*tokens):self.assertNotIn(bad,html)
        self.assertIn('simulação interna autorizada',html);self.assertIn('Acessar treinamento',html)
        for header,value in [('Cache-Control','no-store'),('Referrer-Policy','no-referrer'),('X-Content-Type-Options','nosniff')]:self.assertEqual(response.headers[header],value)
        self.assertIn("default-src 'none'",response.headers['Content-Security-Policy']);self.assertIn("script-src 'none'",response.headers['Content-Security-Policy'])
        self.assertEqual(before,self.educacional())
        response=self.client.post(path+'/treinamento',headers=self.headers);self.assertEqual(response.status_code,303);aliaspath=response.location
        self.assertIn('one-session-',aliaspath)
        portal=self.client.get(aliaspath);self.assertEqual(portal.status_code,200)
        for token in tokens:self.assertNotIn(token,portal.get_data(as_text=True));self.assertNotIn(token,aliaspath)
        self.assertEqual(before,self.educacional());self.assertEqual(self.app.test_client().get(aliaspath).status_code,404)
        self.assertEqual(self.client.post(aliaspath+'/iniciar',headers=self.headers).status_code,302)
        db.session.expire_all();self.assertIsNotNone(db.session.get(P,pid).iniciado_em);self.assertIsNone(db.session.get(P,pid).concluido_em)
        self.assertEqual(R.query.one().snapshot_canonico,snapshot)
        audit=str([row.__dict__ for row in Auditoria.query.all()])
        for forbidden in (*tokens,str(bid),'DO-NOT-PERSIST',baseline.KEY,'second@example.test'):self.assertNotIn(forbidden,audit)
        self.assertTrue(all(not endpoint.startswith('/api/campaigns/') or method=='GET' for method,endpoint in self.fake.chamadas))
        self.guard.assert_not_called();self.network.assert_not_called()

    def test_import_preserves_existing_quiz_feedback_and_time(self):
        self.base(True);p=db.session.get(P,self.pid);p.iniciado_em=agora()-timedelta(days=2);p.concluido_em=agora()-timedelta(days=1);p.ultimo_modulo_id=1
        db.session.add_all([ResultadoQuiz(participacao_id=self.pid,total_questoes=1,total_acertos=1,percentual=100),FeedbackTreinamento(participacao_id=self.pid,avaliacao_geral=5,avaliacao_conteudo=5,avaliacao_clareza=5,avaliacao_utilidade=5,comentario='Preservar'),TempoTreinamento(participacao_id=self.pid,modulo_id=1,segundos_ativos=42)])
        db.session.commit();before=self.educacional();self.window([self.evt()]);self.run_action();self.assertEqual(before,self.educacional())

    def test_health_optional_failure_and_normal_invites_portal_are_independent(self):
        self.base(True);self.fake.configurar_resposta('GET','/api/campaigns/',status='timeout')
        with patch.object(self.service,'_request',side_effect=AssertionError('HTTP proibido no health')):
            self.assertEqual(self.client.get('/health').status_code,200)
            self.login(self.admin);self.assertEqual(self.client.get('/integracoes/gophish/').status_code,200)
            self.assertEqual(self.client.get('/convites/').status_code,200)
        self.assertEqual(self.client.get('/aprender/t/synthetic-training-secret').status_code,200)
        calls=list(self.fake.chamadas);self.login(self.creator);self.post('solicitar-aprovacao',self.normal);self.login(self.admin);self.post('aprovar',self.normal);self.post('preparar',self.normal)
        self.assertEqual(self.fake.chamadas,calls);self.assertEqual(self.normal.status,'preparada')

    def test_configuration_incomplete_fails_before_any_transport(self):
        self.preparar_base();calls=list(self.fake.chamadas)
        cfg=ConfiguracaoGophish(enabled=True,backend='http',instance_id='configured-instance')
        self.app.extensions['gophish_service']=GophishService(cfg)
        self.bloqueado('not_configured',preparacao.executar_operacao,self.eid,self.admin,'preparar',uuid4())
        self.assertEqual(self.fake.chamadas,calls)

    def test_late_and_same_timestamp_events_after_preparation(self):
        self.prepared();self.window([self.evt(ident='one'),self.evt(ident='two'),self.evt('Email Opened','open')]);self.run_action()
        self.window([self.evt(ident='late',time='2026-01-01T00:00:00Z'),self.evt(ident='one')]);self.run_action()
        self.assertEqual(F.query.count(),4)

    def test_bridge_expiration_revocation_and_replay_after_preparation(self):
        self.prepared();db.session.rollback();educacao.criar_participacao(self.aid,self.admin,True)
        bid=educacao.criar_ponte(self.aid,self.admin,confirmar=True);self.login(None);path='/simulacao/educacao/'+str(bid)
        response=self.client.post(path+'/treinamento',headers=self.headers);self.assertEqual(response.status_code,303)
        self.assertEqual(self.app.test_client().get(response.location).status_code,404)
        db.session.rollback();educacao.revogar_ponte(self.aid,self.admin,True);self.assertEqual(self.client.get(response.location).status_code,404)
        db.session.rollback();bid=educacao.criar_ponte(self.aid,self.admin,confirmar=True);b=db.session.get(B,bid);b.criada_em=agora()-timedelta(hours=2);b.expira_em=agora()-timedelta(hours=1);db.session.commit()
        self.assertEqual(self.client.get('/simulacao/educacao/'+str(bid)).status_code,404)

    def test_current_log_pipeline_all_synthetic_markers(self):
        from log_privacidade import sanitizar,configurar_logs,proteger_logger
        token='SYN-TOKEN-'+uuid4().hex;bearer=str(uuid4());key='SYNKEY-'+uuid4().hex;email=uuid4().hex+'@synthetic.invalid';query='SYNQUERY-'+uuid4().hex
        raw=f'GET /simulacao/educacao/{bearer}?key={key}&email={email}&q={query} Referer /aprender/t/{token}?key={key} HTTP/1.1'
        cleaned=sanitizar(raw)
        for marker in (token,bearer,key,email,query):self.assertNotIn(marker,cleaned)
        for name in ('flask-e187-synthetic','gunicorn.error','gunicorn.access'):
            logger=logging.getLogger(name);stream=io.StringIO();handler=logging.StreamHandler(stream);logger.addHandler(handler);proteger_logger(logger)
            logger.warning(raw);logger.removeHandler(handler)
            for marker in (token,bearer,key,email,query):self.assertNotIn(marker,stream.getvalue())
        for kind in ('apache-access','apache-error'):
            path=Path('/tmp/one-18.7E-review')/(kind+'-'+uuid4().hex+'.log')
            proc=subprocess.run([sys.executable,'-I',str(Path('log_privacidade.py').resolve()),'--arquivo',str(path)],input=raw+'\n',text=True,capture_output=True)
            self.assertEqual(proc.returncode,0)
            for marker in (token,bearer,key,email,query):self.assertNotIn(marker,path.read_text())


def falha(code,kwargs):
    def test(self):
        self.preparar_base();self.fake.configurar_resposta('GET','/api/groups/',**kwargs);db.session.rollback()
        out=preparacao.executar_operacao(self.eid,self.admin,'preparar',uuid4());self.assertEqual(out['codigo_erro'],code)
        self.assertFalse(any(m=='POST' for m,p in self.fake.chamadas));self.assertEqual(self.fake.recursos['campaigns'],{})
    return test
for label,code,args in [('404','not_found',{'http_status':404}),('401','auth_error',{'http_status':401}),('403','forbidden',{'http_status':403}),('timeout','timeout',{'status':'timeout'}),('tls','tls_error',{'status':'tls_error'}),('500','server_error',{'http_status':500}),('json','invalid_response',{'http_status':200,'data':'NOT-JSON'})]:setattr(OperacionalTest,'test_integrated_failure_'+label,falha(code,args))


class PostgreSQLOperacionalTest(OperacionalTest):
    setUp=anterior.PostgreSQLTest.setUp
    cleanup_pg=anterior.PostgreSQLTest.cleanup_pg

    def test_concurrent_distinct_preparations_and_imports(self):
        self.preparar_base();db.session.rollback();entered=Event();release=Event();orig=self.fake.request
        def transport(*args,**kwargs):
            with Session(db.engine) as s,s.begin():s.execute(select(E).where(E.id==self.eid).with_for_update(nowait=True)).scalar_one()
            if not entered.is_set():entered.set();release.wait(10)
            return orig(*args,**kwargs)
        def run():
            with self.app.app_context():return preparacao.executar_operacao(self.eid,self.admin,'preparar',uuid4())
        with patch.object(self.fake,'request',side_effect=transport),ThreadPoolExecutor(max_workers=2) as pool:
            future=pool.submit(run);self.assertTrue(entered.wait(10));self.bloqueado('busy',preparacao.executar_operacao,self.eid,self.admin,'preparar',uuid4());release.set();self.assertEqual(future.result(timeout=20)['estado'],'concluida')
        self.assertEqual(len([m for m,p in self.fake.chamadas if m=='POST']),3)
        e=db.session.get(E,self.eid);r=db.session.get(R,self.rid);self.nome=f'ONE-CAMP-{e.uuid.hex}-{r.digest}';self.sha=r.digest;self.window([]);self.run_action('vincular')
        self.window([self.evt()]);entered.clear();release.clear();db.session.rollback()
        def run_import():
            with self.app.app_context():return resultados.executar(self.eid,self.admin,'importar',uuid4())
        with patch.object(self.fake,'request',side_effect=transport),ThreadPoolExecutor(max_workers=2) as pool:
            future=pool.submit(run_import);self.assertTrue(entered.wait(10));self.bloqueado('busy',resultados.executar,self.eid,self.admin,'importar',uuid4());release.set();self.assertEqual(future.result(timeout=20)['estado'],'concluida')
        self.run_action();self.assertEqual(F.query.count(),1)

    def test_concurrent_bridge_creation_is_unique(self):
        self.base(True);db.session.rollback();barrier=Barrier(2)
        def create():
            with self.app.app_context():
                barrier.wait(10)
                try:return ('ok',str(educacao.criar_ponte(self.aid,self.admin,confirmar=True)))
                except SimulacaoInvalida as error:return ('error',error.codigo)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:create(),range(2)))
        self.assertEqual(sum(r[0]=='ok' for r in results),1);self.assertIn(('error','bridge_exists'),results);self.assertEqual(B.query.count(),1)

    def test_concurrent_lease_recovery_and_old_executor_rejected(self):
        self.base();key,old=self.expired_operation();db.session.rollback();barrier=Barrier(2)
        def recover():
            with self.app.app_context():
                barrier.wait(10)
                try:
                    ticket,eid,action=leases.recuperar(key,self.admin,True);return ('ok',ticket)
                except SimulacaoInvalida as error:return ('error',error.codigo)
                finally:leases.TICKET.set(None)
        with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:recover(),range(2)))
        self.assertEqual(sum(r[0]=='ok' for r in responses),1);self.assertIn(('error','lease_active'),responses)
        ticket=next(value for status,value in responses if status=='ok');self.assertEqual(ticket.geracao,2)
        leases.TICKET.set(old);self.bloqueado('fence_lost',resultados.persistir,self.eid,self.admin,key,self.service.ler_campanha_tecnica(101),101)
        resultados.executar(self.eid,self.admin,'importar',key,recuperada=ticket);leases.TICKET.set(None)

    def test_constraints_fk_unique_snapshot_and_lease_guards(self):
        self.base(True);self.window([self.evt()]);self.run_action();db.session.rollback()
        statements=["UPDATE revisoes_simulacao SET snapshot_canonico='{}'","UPDATE eventos_simulacao SET tipo='mensagem_aberta'","UPDATE alvos_simulacao SET identificador_externo='another'",
            "INSERT INTO vinculos_campanha_simulacao (execucao_id,revisao_id,instancia_id,externo_id,nome_tecnico,digest,estado,criado_em) SELECT execucao_id,revisao_id,instancia_id,externo_id,nome_tecnico,digest,estado,criado_em FROM vinculos_campanha_simulacao",
            "INSERT INTO alvos_simulacao (execucao_id,colaborador_id,email_aprovado,estado) VALUES (999999,999999,'nobody@synthetic.invalid','autorizado')",
            "UPDATE leases_operacao_simulacao SET geracao=geracao+2"]
        for stmt in statements:
            with self.subTest(stmt=stmt),self.assertRaises(SQLAlchemyError):
                with db.engine.begin() as conn:conn.execute(text(stmt))
        db.session.rollback();educacao.criar_ponte(self.aid,self.admin,confirmar=True)
        with self.assertRaises(SQLAlchemyError):
            with db.engine.begin() as conn:conn.execute(text("INSERT INTO pontes_educativas_simulacao (id,alvo_id,responsavel_id,estado,criada_em,expira_em) SELECT :id,alvo_id,responsavel_id,estado,criada_em,expira_em FROM pontes_educativas_simulacao"),{'id':uuid4()})

    def test_upgrade_downgrade_upgrade_c_and_d(self):
        db.session.rollback();command.downgrade(self.config,'e186d7e4a904');self.assertNotIn('revisoes_simulacao',inspect(db.engine).get_table_names())
        command.upgrade(self.config,'a187d7e4a906');self.assertIn('eventos_simulacao',inspect(db.engine).get_table_names())
