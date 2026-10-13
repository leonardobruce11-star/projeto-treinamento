"""18.7C: transportes de simulação exclusivamente fake, com guardas de rede/SMTP."""
import io
import logging
import os
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import patch
from sqlalchemy import select, text, inspect, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from alembic import command
from alembic.config import Config as AlembicConfig
import test_gophish_seguro as baseline
from app import create_app
from app.extensions import db
from app.models import Colaborador, GrupoParticipante, ParticipacaoTreinamento
from app.models.simulacao_resultados import LeaseOperacaoSimulacao as L, PonteEducativaSimulacao as B, AssociacaoEducacionalSimulacao as S, EventoSimulacao as F, VinculoCampanhaSimulacao as V
from app.models.simulacao import (RevisaoSimulacao as R, ExecucaoSimulacao as E, AlvoSimulacao as A,
    RecursoExternoSimulacao as X, OperacaoSimulacao as O, SnapshotImutavel)
from app.simulacoes.snapshots import aprovar_simulacao, reabrir_simulacao, SimulacaoInvalida
from app.simulacoes.preparacao import criar_execucao, executar_operacao
from app.integracoes.gophish.config import ConfiguracaoGophish
from app.integracoes.gophish.service import GophishService


class PreparacaoTest(unittest.TestCase):
    setUp=baseline.FluxoTest.setUp
    login=baseline.FluxoTest.login
    post=baseline.FluxoTest.post
    enable=baseline.FluxoTest.enable

    def preparar_base(self,fase='ampliada'):
        self.cid=self.campaign.id
        self.service=self.enable();self.fake=self.service.backend
        self.post('solicitar-aprovacao');db.session.rollback()
        self.rid=aprovar_simulacao(self.cid,self.admin).revisao_id
        self.eid=criar_execucao(self.rid,self.admin,fase,[1] if fase=='piloto' else None)
        return self.eid

    def acao(self,action='preparar',key=None):
        db.session.rollback()
        result=executar_operacao(self.eid,self.admin,action,key or uuid4())
        db.session.expire_all()
        return result

    def bloqueado(self,code,fn,*args):
        db.session.rollback()
        with self.assertRaises(SimulacaoInvalida) as ctx:fn(*args)
        self.assertEqual(ctx.exception.codigo,code)
        db.session.expire_all()

    def test_normal_and_draft_no_revision_or_transport(self):
        service=self.enable()
        self.post('solicitar-aprovacao',self.normal);self.login(self.admin);self.post('aprovar',self.normal)
        self.assertEqual(R.query.count(),0)
        self.bloqueado('invalid_type',aprovar_simulacao,self.normal.id,self.admin)
        self.bloqueado('invalid_state',aprovar_simulacao,self.campaign.id,self.admin)
        self.bloqueado('not_found',criar_execucao,999,self.admin,'ampliada')
        self.assertEqual(service.backend.chamadas,[])

    def test_approval_double_click_and_new_revision(self):
        self.preparar_base();db.session.rollback()
        again=aprovar_simulacao(self.cid,self.admin)
        self.assertEqual(again.revisao_id,self.rid);self.assertFalse(again.criada)
        first=R.query.one().snapshot_canonico;db.session.rollback()
        reabrir_simulacao(self.cid,self.admin)
        c=db.session.get(baseline.Campanha,self.cid);c.nome='Outra configuração';c.status='aguardando_aprovacao';db.session.commit()
        second=aprovar_simulacao(self.cid,self.admin)
        self.assertNotEqual(second.revisao_id,self.rid)
        self.assertEqual(db.session.get(R,self.rid).snapshot_canonico,first)
        self.assertEqual(R.query.count(),2);self.assertEqual(self.fake.chamadas,[])

    def test_snapshot_immutable_and_canonical(self):
        self.preparar_base();r=db.session.get(R,self.rid)
        from app.simulacoes.contracts import digest
        self.assertEqual(digest(r.snapshot),r.digest)
        copy=r.snapshot;copy['nome']='mutação';self.assertNotEqual(copy,r.snapshot)
        r.snapshot_canonico='{}'
        with self.assertRaises(SnapshotImutavel):db.session.flush()
        db.session.rollback()
        a=A.query.one();a.email_aprovado='outro@example.test'
        with self.assertRaises(SnapshotImutavel):db.session.flush()
        db.session.rollback()

    def test_changed_collaborator_keeps_target_and_blocks_preparation(self):
        self.preparar_base();a=A.query.one();old=a.email_aprovado
        c=db.session.get(Colaborador,a.colaborador_id);c.email='novo@example.test';db.session.commit()
        self.bloqueado('snapshot_changed',executar_operacao,self.eid,self.admin,'preparar',uuid4())
        self.assertEqual(A.query.one().email_aprovado,old);self.assertEqual(self.fake.chamadas,[])

    def test_removed_member_and_inactivated_target_block_preparation(self):
        self.preparar_base();membership=GrupoParticipante.query.one();db.session.delete(membership);db.session.commit()
        self.bloqueado('empty_audience',executar_operacao,self.eid,self.admin,'preparar',uuid4())
        self.assertEqual(A.query.count(),1);self.assertEqual(self.fake.chamadas,[])

    def test_inactive_excluded_and_duplicate_selection_deduplicated(self):
        c=Colaborador(nome='Inativo',email='',ativo=False,departamento_id=1);db.session.add(c);db.session.flush()
        db.session.add(GrupoParticipante(grupo_id=self.campaign.grupo_id,colaborador_id=c.id));db.session.commit()
        self.preparar_base('piloto');self.assertEqual(A.query.count(),1)
        self.assertEqual(R.query.one().snapshot['exclusoes'],[{'colaborador_id':c.id,'motivo':'inativo'}])
        db.session.rollback();self.assertEqual(criar_execucao(self.rid,self.admin,'piloto',[1,1]),self.eid)
        self.assertEqual(E.query.count(),1)

    def test_missing_or_duplicate_email_blocks_approval(self):
        from app.simulacoes.snapshots import email_aprovado
        self.bloqueado('invalid_email',email_aprovado,None)
        for value in ('','invalid-address'):
            c=Colaborador.query.one();c.email=value;self.campaign.status='aguardando_aprovacao';db.session.commit()
            self.bloqueado('invalid_email',aprovar_simulacao,self.campaign.id,self.admin)
        c.email='pessoa@example.test';extra=Colaborador(nome='Outro',email='PESSOA@example.test',departamento_id=1)
        db.session.add(extra);db.session.flush();db.session.add(GrupoParticipante(grupo_id=1,colaborador_id=extra.id));db.session.commit()
        self.bloqueado('duplicate_email',aprovar_simulacao,self.campaign.id,self.admin)
        self.assertEqual(R.query.count(),0)

    def test_disabled_and_unauthorized_and_instance_switch_block(self):
        self.preparar_base()
        for uid in (self.creator,self.inactive):
            self.bloqueado('forbidden',executar_operacao,self.eid,uid,'preparar',uuid4())
        self.app.extensions['gophish_service']=GophishService(ConfiguracaoGophish())
        self.bloqueado('disabled',executar_operacao,self.eid,self.admin,'preparar',uuid4())
        cfg=ConfiguracaoGophish.do_ambiente({'GOPHISH_ENABLED':'true','GOPHISH_INSTANCE_ID':'other'})
        self.app.extensions['gophish_service']=GophishService(cfg)
        self.bloqueado('instance_mismatch',executar_operacao,self.eid,self.admin,'preparar',uuid4())
        self.assertEqual(self.fake.chamadas,[])

    def test_http_instance_explicit_and_environment_fence(self):
        env={'GOPHISH_ENABLED':'true','GOPHISH_BACKEND':'http','GOPHISH_URL':'https://host.test','GOPHISH_API_KEY':baseline.KEY}
        self.assertEqual(ConfiguracaoGophish.do_ambiente(env).estado(),'not_configured')
        self.assertEqual(ConfiguracaoGophish(enabled=True,backend='http',base_url=env['GOPHISH_URL'],api_key=baseline.KEY).estado(),'not_configured')
        env['GOPHISH_INSTANCE_ID']='homologation-one'
        self.assertEqual(ConfiguracaoGophish.do_ambiente(env).estado(),'ready')
        self.preparar_base();self.app.extensions['gophish_service']=GophishService(ConfiguracaoGophish.do_ambiente(env))
        self.bloqueado('environment_forbidden',executar_operacao,self.eid,self.admin,'preparar',uuid4())
        self.guard.assert_not_called()

    def test_prepare_only_auxiliary_and_duplicate_uuid_cached(self):
        self.preparar_base();key=uuid4()
        smtp=patch('smtplib.SMTP',side_effect=AssertionError('SMTP proibido'));smtp_tls=patch('smtplib.SMTP_SSL',side_effect=AssertionError('SMTP proibido'))
        with smtp,smtp_tls:self.assertEqual(self.acao(key=key)['estado'],'concluida')
        before=list(self.fake.chamadas)
        self.assertEqual(self.acao(key=key)['estado'],'concluida');self.assertEqual(self.fake.chamadas,before)
        self.assertEqual(len([m for m,p in before if m=='POST']),3)
        self.assertTrue(all(p.split('/')[2] in ('groups','templates','pages') for m,p in before))
        self.assertEqual(self.fake.recursos['campaigns'],{});self.assertEqual(self.fake.recursos['smtp'],{})
        self.assertEqual(db.session.get(E,self.eid).estado,'preparada');self.assertEqual(X.query.count(),3)
        self.assertEqual(db.session.get(baseline.Campanha,self.cid).status,'aprovada')
        self.guard.assert_not_called();self.network.assert_not_called()

    def test_reconcile_existing_binding_and_digest(self):
        self.preparar_base();self.acao();before=len(self.fake.chamadas)
        self.assertEqual(self.acao('reconciliar')['estado'],'concluida')
        self.assertTrue(all(m=='GET' for m,p in self.fake.chamadas[before:]))
        x=X.query.filter_by(tipo='grupo').one();self.fake.recursos['groups'][x.externo_id]['_one_digest']='0'*64
        self.assertEqual(self.acao('reconciliar')['codigo_erro'],'binding_mismatch')
        self.assertEqual(len([m for m,p in self.fake.chamadas if m=='POST']),3)

    def test_confirmed_404_allows_only_later_explicit_creation(self):
        self.preparar_base();self.acao();x=X.query.filter_by(tipo='grupo').one();old=x.externo_id
        del self.fake.recursos['groups'][old]
        before=len(self.fake.chamadas);self.acao('reconciliar')
        self.assertTrue(all(m=='GET' for m,p in self.fake.chamadas[before:]))
        self.assertEqual(X.query.filter_by(tipo='grupo').one().estado,'ausente')
        self.assertEqual(self.acao()['estado'],'concluida')
        self.assertNotEqual(X.query.filter_by(tipo='grupo').one().externo_id,old)

    def test_post_timeout_after_create_reconcile_without_retry(self):
        self.preparar_base();self.fake.configurar_resposta('POST','/api/groups/',status='timeout',simular_criacao=True)
        key=uuid4();result=self.acao(key=key);self.assertEqual(result['estado'],'resultado_indeterminado')
        before=list(self.fake.chamadas);self.acao(key=key);self.assertEqual(before,self.fake.chamadas)
        self.bloqueado('reconciliation_required',executar_operacao,self.eid,self.admin,'preparar',uuid4())
        self.assertEqual(self.acao('reconciliar')['estado'],'concluida')
        self.fake.respostas.clear();self.assertEqual(self.acao()['estado'],'concluida')
        self.assertEqual(len([m for m,p in self.fake.chamadas if m=='POST' and p=='/api/groups/']),1)

    def test_post_timeout_before_create_remains_indeterminate(self):
        self.preparar_base();self.fake.configurar_resposta('POST','/api/groups/',status='timeout')
        self.assertEqual(self.acao()['estado'],'resultado_indeterminado');self.fake.respostas.clear()
        self.assertEqual(self.acao('reconciliar')['estado'],'resultado_indeterminado')
        self.bloqueado('reconciliation_required',executar_operacao,self.eid,self.admin,'preparar',uuid4())
        self.assertEqual(len([m for m,p in self.fake.chamadas if m=='POST']),1)

    def test_success_without_id_is_not_success(self):
        self.preparar_base();self.fake.configurar_resposta('POST','/api/groups/',http_status=201,data={'name':'ignored'})
        result=self.acao();self.assertEqual(result['codigo_erro'],'invalid_response');self.assertEqual(result['estado'],'resultado_indeterminado')
        self.assertIsNone(X.query.filter_by(tipo='grupo').one().externo_id)

    def test_safe_validator_not_relaxed_and_no_tokens_or_keys(self):
        self.preparar_base();stream=io.StringIO();handler=logging.StreamHandler(stream);self.app.logger.addHandler(handler)
        self.addCleanup(self.app.logger.removeHandler,handler)
        self.fake.configurar_resposta('GET','/api/groups/',http_status=401,data={'detail':baseline.KEY+' '+baseline.TOKEN})
        response=self.acao();self.assertNotIn(baseline.KEY,str(response));self.assertNotIn(baseline.TOKEN,str(response))
        self.assertNotIn(baseline.KEY,stream.getvalue());self.assertNotIn(baseline.TOKEN,stream.getvalue())
        self.assertNotIn(baseline.KEY,R.query.one().snapshot_canonico)
        self.assertNotIn('token',R.query.one().snapshot_canonico.lower())
        self.assertNotIn('token',{c.name for c in A.__table__.columns})
        c=db.session.get(baseline.Campanha,self.cid);c.landing_page.conteudo_html='<form></form>';db.session.commit()
        self.bloqueado('invalid_content',executar_operacao,self.eid,self.admin,'preparar',uuid4())

    def test_admin_ui_confirmation_csrf_and_no_dispatch(self):
        self.preparar_base();self.login(self.admin)
        url=f'/simulacoes/campanhas/{self.cid}'
        response=self.client.get(url);self.assertEqual(response.status_code,200)
        self.assertNotIn(baseline.KEY,response.text);self.assertNotIn(baseline.TOKEN,response.text)
        self.assertNotIn('type="password"',response.text)
        self.assertEqual(self.client.post(url+'/executar',data={}).status_code,400)
        response=self.client.post(url+'/confirmar',data={'acao':'preparar','execucao':self.eid},headers=self.headers)
        self.assertEqual(response.status_code,200)
        from html.parser import HTMLParser
        class Parser(HTMLParser):
            def handle_starttag(self,tag,attrs):
                values=dict(attrs)
                if values.get('name')=='confirmacao':self.value=values['value']
        parser=Parser();parser.feed(response.text)
        self.assertEqual(self.fake.chamadas,[])
        self.assertEqual(self.client.post(url+'/executar',data={'confirmacao':parser.value},headers=self.headers).status_code,400)
        self.assertEqual(self.client.post(url+'/executar',data={'confirmacao':parser.value,'confirmar':'sim'},headers=self.headers).status_code,302)
        count=len(self.fake.chamadas)
        self.client.post(url+'/executar',data={'confirmacao':parser.value,'confirmar':'sim'},headers=self.headers)
        self.assertEqual(len(self.fake.chamadas),count)
        self.login(self.creator);self.assertEqual(self.client.get(url).status_code,403)

    def test_known_ids_api_errors_never_recreate(self):
        self.preparar_base();self.acao();x=X.query.filter_by(tipo='grupo').one();external_id=x.externo_id
        count=len([m for m,p in self.fake.chamadas if m=='POST'])
        for raw,code in [({'http_status':401},'auth_error'),({'http_status':403},'forbidden'),
            ({'http_status':500},'server_error'),({'status':'tls_error'},'tls_error'),
            ({'status':'timeout'},'timeout'),({'http_status':200,'data':None},'invalid_response')]:
            with self.subTest(code=code):
                self.fake.configurar_resposta('GET',f'/api/groups/{external_id}',**raw)
                self.assertEqual(self.acao('reconciliar')['codigo_erro'],code)
                self.assertEqual(X.query.filter_by(tipo='grupo').one().externo_id,external_id)
                self.assertEqual(len([m for m,p in self.fake.chamadas if m=='POST']),count)

    def test_existing_participation_association_without_exporting_token(self):
        token='ONLY_LOCAL_PARTICIPATION_TOKEN_'+uuid4().hex
        cid=self.campaign.id
        p=ParticipacaoTreinamento(token=token,colaborador_id=1,treinamento_id=self.campaign.treinamento_id,campanha_id=cid)
        db.session.add(p);db.session.commit();pid=p.id
        self.preparar_base()
        self.assertEqual(A.query.one().participacao_id,pid)
        captured=[];orig=self.fake.request
        def transport(method,path,payload=None,**kwargs):
            if payload is not None:captured.append(payload)
            return orig(method,path,payload,**kwargs)
        with patch.object(self.fake,'request',side_effect=transport):self.acao()
        self.assertNotIn(token,str(captured));self.assertNotIn(token,R.query.one().snapshot_canonico)
        self.assertNotIn(token,str(self.fake.recursos));self.assertNotIn('departamento',str(captured))
        self.assertTrue(all(t['position']=='' and t['first_name']=='' and t['last_name']=='' for t in captured[0]['targets']))

    def test_participation_token_in_resource_rejected_before_snapshot(self):
        token='TOKEN_LOCAL_'+uuid4().hex
        c=self.campaign
        db.session.add(ParticipacaoTreinamento(token=token,colaborador_id=1,treinamento_id=c.treinamento_id,campanha_id=c.id))
        c.template_email.conteudo_html='<p>'+token+'</p>';c.status='aguardando_aprovacao';db.session.commit()
        self.bloqueado('participation_token_forbidden',aprovar_simulacao,c.id,self.admin)
        self.assertEqual(R.query.count(),0)

    def test_training_and_group_changes_require_reapproval(self):
        self.preparar_base();c=db.session.get(baseline.Campanha,self.cid)
        c.template_email.assunto='Nova orientação';db.session.commit()
        self.bloqueado('snapshot_changed',executar_operacao,self.eid,self.admin,'preparar',uuid4())
        self.assertEqual(self.fake.chamadas,[])

    def test_foreign_external_name_not_reused(self):
        self.preparar_base();self.acao();x=X.query.filter_by(tipo='grupo').one()
        self.fake.recursos['groups'][x.externo_id]['name']='ONE-'+uuid4().hex+'-grupo-'+x.digest
        self.assertEqual(self.acao('reconciliar')['codigo_erro'],'binding_mismatch')
        self.assertEqual(len([m for m,p in self.fake.chamadas if m=='POST']),3)

    def test_interrupted_operation_is_fenced(self):
        from app.simulacoes.preparacao import reservar,uuid_publico
        self.preparar_base();key=uuid4();db.session.rollback()
        cached,tasks=reservar(self.eid,self.admin,'preparar',uuid_publico(key))
        self.assertIsNone(cached);self.assertEqual(self.acao(key=key)['estado'],'processando')
        self.bloqueado('busy',executar_operacao,self.eid,self.admin,'reconciliar',uuid4())
        self.assertEqual(self.fake.chamadas,[])

    def test_permission_revoked_during_lookup_blocks_post(self):
        self.preparar_base();orig=self.fake.request
        def transport(*args,**kwargs):
            with Session(db.engine) as session,session.begin():
                admin=session.get(baseline.Usuario,self.admin);admin.ativo=False
            return orig(*args,**kwargs)
        with patch.object(self.fake,'request',side_effect=transport):
            result=self.acao()
        self.assertEqual(result['codigo_erro'],'forbidden');self.assertEqual(result['estado'],'falha')
        self.assertFalse(any(m=='POST' for m,p in self.fake.chamadas))

    def test_partial_failure_retry_reuses_first_resource(self):
        self.preparar_base();self.fake.configurar_resposta('GET','/api/templates/',http_status=401)
        self.assertEqual(self.acao()['codigo_erro'],'auth_error')
        self.assertEqual(len(self.fake.recursos['groups']),1)
        self.fake.respostas.clear();self.assertEqual(self.acao()['estado'],'concluida')
        self.assertEqual(len([m for m,p in self.fake.chamadas if m=='POST' and p=='/api/groups/']),1)


def erro_get(code,raw):
    def test(self):
        self.preparar_base();self.fake.configurar_resposta('GET','/api/groups/',**raw)
        result=self.acao();self.assertEqual(result['codigo_erro'],code)
        self.assertFalse(any(m=='POST' for m,p in self.fake.chamadas))
    return test

for label,code,raw in [('404','not_found',{'http_status':404}),('401','auth_error',{'http_status':401}),
    ('403','forbidden',{'http_status':403}),('500','server_error',{'http_status':500}),
    ('timeout','timeout',{'status':'timeout'}),('tls','tls_error',{'status':'tls_error'}),
    ('json','invalid_response',{'http_status':200,'data':'invalid json'})]:
    setattr(PreparacaoTest,'test_get_'+label+'_never_creates',erro_get(code,raw))


class PostgreSQLTest(PreparacaoTest):
    """Mesmo contrato em PostgreSQL, incluindo locks reais e migration real."""
    def setUp(self):
        url=os.environ.get('ONE_PROGRESSO_TEST_DATABASE_URL')
        if not url:self.skipTest('PostgreSQL temporário explicitamente configurado é necessário.')
        self.schema='simulacao_'+uuid4().hex
        self.base_engine=create_engine(url)
        with self.base_engine.begin() as conn:conn.execute(text('CREATE SCHEMA '+self.schema))
        testurl=make_url(url).update_query_dict({'options':'-csearch_path='+self.schema})
        def factory():
            with patch.dict(os.environ,{'DATABASE_URL':testurl.render_as_string(hide_password=False)}):return create_app()
        with patch.object(baseline,'create_app',side_effect=factory):baseline.FluxoTest.setUp(self)
        self.addCleanup(self.cleanup_pg)
        db.session.rollback()
        self.config=AlembicConfig(str(Path('migrations/alembic.ini')))
        self.config.set_main_option('script_location',str(Path('migrations')))
        with db.engine.begin() as conn:
            for model in (L,B,S,F,V,O,X,A,E,R):model.__table__.drop(conn)
        command.stamp(self.config,'e186d7e4a904')
        command.upgrade(self.config,'a187d7e4a906')

    def cleanup_pg(self):
        db.session.remove();db.engine.dispose()
        with self.base_engine.begin() as conn:conn.execute(text('DROP SCHEMA IF EXISTS '+self.schema+' CASCADE'))
        self.base_engine.dispose()

    def test_migration_upgrade_downgrade_reupgrade_and_sql_guards(self):
        db.session.rollback()
        command.downgrade(self.config,'e186d7e4a904')
        self.assertNotIn('revisoes_simulacao',inspect(db.engine).get_table_names())
        command.upgrade(self.config,'a187d7e4a906')
        self.assertIn('operacoes_simulacao',inspect(db.engine).get_table_names())
        self.preparar_base();db.session.rollback()
        for statement in ["UPDATE revisoes_simulacao SET snapshot_canonico='{}'", "UPDATE alvos_simulacao SET email_aprovado='mutated@example.test'",'DELETE FROM revisoes_simulacao']:
            with self.assertRaises(SQLAlchemyError):
                with db.engine.begin() as conn:conn.execute(text(statement))
        self.assertEqual(R.query.count(),1)

    def test_concurrent_approval_and_execution_are_unique(self):
        self.service=self.enable();self.cid=self.campaign.id;self.post('solicitar-aprovacao');db.session.rollback()
        def approval():
            with self.app.app_context():return aprovar_simulacao(self.cid,self.admin).revisao_id
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(lambda _:approval(),range(2)))
        self.assertEqual(ids[0],ids[1]);self.rid=ids[0]
        def execution():
            with self.app.app_context():return criar_execucao(self.rid,self.admin,'ampliada')
        with ThreadPoolExecutor(max_workers=2) as pool:eids=list(pool.map(lambda _:execution(),range(2)))
        self.assertEqual(eids[0],eids[1]);self.assertEqual(E.query.count(),1);self.assertEqual(O.query.count(),0)

    def test_concurrent_operation_and_no_transaction_during_transport(self):
        self.preparar_base();db.session.rollback();entered=Event();release=Event();orig=self.fake.request;key=uuid4()
        def transport(*args,**kwargs):
            # Another connection can lock both execution and resources during every fake HTTP operation.
            with Session(db.engine) as session,session.begin():
                session.execute(select(E).where(E.id==self.eid).with_for_update(nowait=True)).scalar_one()
                session.execute(select(X).where(X.execucao_id==self.eid).with_for_update(nowait=True)).scalars().all()
            if not entered.is_set():entered.set();release.wait(10)
            return orig(*args,**kwargs)
        def operation():
            with self.app.app_context():return executar_operacao(self.eid,self.admin,'preparar',key)
        with patch.object(self.fake,'request',side_effect=transport),ThreadPoolExecutor(max_workers=2) as pool:
            future=pool.submit(operation);self.assertTrue(entered.wait(10))
            self.assertEqual(self.acao(key=key)['estado'],'processando')
            self.bloqueado('busy',executar_operacao,self.eid,self.admin,'preparar',uuid4())
            release.set();self.assertEqual(future.result(timeout=20)['estado'],'concluida')
        self.assertEqual(O.query.count(),1);self.assertEqual(len([m for m,p in self.fake.chamadas if m=='POST']),3)
