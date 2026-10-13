"""18.7D: fake, separação educacional, ponte, fencing e PostgreSQL isolado."""
import json,os,unittest,logging,io
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from sqlalchemy import select,text,inspect
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from alembic import command
import test_gophish_seguro as baseline
import test_simulacao_preparacao as anterior
from app.extensions import db
from app.models import ParticipacaoTreinamento as P,Colaborador,Campanha,Auditoria
from app.models.simulacao import RevisaoSimulacao as R,ExecucaoSimulacao as E,AlvoSimulacao as A,OperacaoSimulacao as O,SnapshotImutavel
from app.models.simulacao_resultados import EventoSimulacao as F,VinculoCampanhaSimulacao as V,LeaseOperacaoSimulacao as L,PonteEducativaSimulacao as B,AssociacaoEducacionalSimulacao as S
from app.simulacoes.snapshots import SimulacaoInvalida,agora
from app.simulacoes import resultados,educacao,leases
from app.simulacoes.preparacao import executar_operacao


class ResultadosTest(unittest.TestCase):
    setUp=baseline.FluxoTest.setUp
    login=baseline.FluxoTest.login
    post=baseline.FluxoTest.post
    enable=baseline.FluxoTest.enable
    preparar_base=anterior.PreparacaoTest.preparar_base
    bloqueado=anterior.PreparacaoTest.bloqueado

    def base(self,participacao=False):
        if participacao:
            p=P(token='synthetic-training-secret',colaborador_id=1,treinamento_id=1,campanha_id=self.campaign.id)
            db.session.add(p);db.session.commit();self.pid=p.id
        self.preparar_base();self.aid=A.query.one().id
        e=db.session.get(E,self.eid);r=db.session.get(R,self.rid)
        self.nome=f'ONE-CAMP-{e.uuid.hex}-{r.digest}';self.sha=r.digest
        self.window([]);self.assertEqual(self.run_action('vincular')['estado'],'concluida')
        self.fake.chamadas.clear();leases.TICKET.set(None)

    def window(self,events,results=None):
        self.fake.configurar_campanha_tecnica(101,self.nome,self.sha,results if results is not None else [{'rid':'rid-one','email':'pessoa@example.test'}],events)

    def evt(self,kind='Clicked Link',ident='event-one',time='2026-10-05T10:00:00Z',rid='rid-one',seq=None):
        data={'message':kind,'rid':rid,'time':time}
        if ident is not None:data['event_id']=ident
        if seq is not None:data['sequence']=seq
        return data

    def run_action(self,action='importar',key=None):
        db.session.rollback()
        output=resultados.executar(self.eid,self.admin,action,key or uuid4(),101 if action=='vincular' else None)
        db.session.expire_all();return output

    def test_permitted_events_and_no_transport_mutation(self):
        self.base();self.window([self.evt(k,str(i)) for i,k in enumerate(['Email Sent','Email Opened','Clicked Link','Error Sending Email'])])
        self.assertEqual(self.run_action()['resultado']['eventos_novos'],4)
        self.assertEqual({f.tipo for f in F.query.all()},{'mensagem_enviada','mensagem_aberta','link_acessado','erro_envio'})
        self.assertEqual(A.query.one().identificador_externo,'rid-one')
        self.assertTrue(all(method=='GET' for method,path in self.fake.chamadas));self.assertFalse(self.fake.recursos['smtp'])
        self.guard.assert_not_called();self.network.assert_not_called();self.assertFalse(self.service.config.send_enabled)

    def test_repeated_event_window_and_operation_uuid(self):
        self.base();self.window([self.evt(),self.evt()]);key=uuid4()
        self.assertEqual(self.run_action(key=key)['resultado']['eventos_novos'],1)
        before=list(self.fake.chamadas);self.run_action(key=key);self.assertEqual(before,self.fake.chamadas)
        self.assertEqual(self.run_action()['resultado']['eventos_novos'],0);self.assertEqual(F.query.count(),1)

    def test_out_of_order_late_and_same_timestamp(self):
        self.base();self.window([self.evt(ident='new',time='2026-10-05T12:00:00Z'),self.evt(ident='old')]);self.run_action()
        self.window([self.evt(ident='late',time='2026-10-04T01:00:00Z'),self.evt(ident='old'),self.evt(ident='another')]);self.run_action()
        self.assertEqual(F.query.count(),4)

    def test_fallback_discriminator_and_types(self):
        self.base();self.window([self.evt(ident=None,seq=1),self.evt(ident=None,seq=2),self.evt('Email Opened',ident=None,seq=1)])
        self.run_action();self.run_action();self.assertEqual(F.query.count(),3)

    def test_unknown_and_credentials_never_persisted(self):
        self.base();secret='NEVER-PERSIST-CREDENTIAL-PAYLOAD'
        self.window([{'message':'Submitted Data','details':{'password':secret},'cookies':secret,'body':secret}, {'message':'Unknown','anything':secret}, dict(self.evt(),details=secret,password=secret)])
        out=self.run_action();self.assertEqual(out['resultado']['descartados'],2);self.assertEqual(F.query.count(),1)
        self.assertEqual(F.query.one().metadados_tecnicos,{'contrato':1})
        stored=str([(f.metadados_tecnicos,f.evento_externo_id) for f in F.query.all()])+str([o.resultado for o in O.query.all()])+str([a.__dict__ for a in Auditoria.query.all()])
        self.assertNotIn(secret,stored);self.assertNotIn(baseline.KEY,stored)

    def test_progress_is_completely_unchanged(self):
        self.base(True);p=db.session.get(P,self.pid);before=(p.iniciado_em,p.concluido_em,p.ultimo_modulo_id,p.token)
        self.window([self.evt()]);self.run_action();p=db.session.get(P,self.pid)
        self.assertEqual(before,(p.iniciado_em,p.concluido_em,p.ultimo_modulo_id,p.token))
        self.assertEqual(A.query.one().participacao_id,self.pid);self.assertEqual(S.query.count(),0)
        for name in ('resultados_quiz','feedbacks_treinamento','tempos_treinamento'):
            if name in inspect(db.engine).get_table_names():self.assertEqual(db.session.execute(text('SELECT count(*) FROM '+name)).scalar_one(),0)
        self.assertNotIn(p.token,str(self.fake.recursos['campaigns']))

    def test_no_silent_participation_assignment(self):
        self.base();self.window([self.evt()]);self.run_action()
        self.assertIsNone(A.query.one().participacao_id);self.assertEqual(P.query.count(),0);self.assertEqual(S.query.count(),0)
        db.session.rollback();pid=educacao.criar_participacao(self.aid,self.admin,confirmar=True)
        self.assertEqual(educacao.criar_participacao(self.aid,self.admin,confirmar=True),pid)
        self.assertIsNone(A.query.one().participacao_id);self.assertEqual(S.query.count(),1)
        self.assertIsNone(db.session.get(P,pid).iniciado_em);self.assertIsNone(db.session.get(P,pid).concluido_em)

    def test_existing_participation_requires_explicit_binding(self):
        self.base();p=P(token='late-secret',colaborador_id=1,treinamento_id=1,campanha_id=self.cid)
        db.session.add(p);db.session.commit();pid=p.id
        self.bloqueado('explicit_association_required',educacao.criar_participacao,self.aid,self.admin,True)
        self.assertEqual(S.query.count(),0);db.session.rollback()
        educacao.associar(self.aid,pid,self.admin,True);self.assertEqual(S.query.one().participacao_id,pid)
        self.bloqueado('forbidden',educacao.associar,self.aid,pid,self.creator,True)

    def test_outside_snapshot_fails_closed(self):
        self.base();self.window([self.evt()],results=[{'rid':'unknown','email':'outside@example.test'}])
        out=self.run_action();self.assertEqual(out['codigo_erro'],'outside_audience');self.assertEqual(F.query.count(),0)

    def test_ambiguous_result_rejects_whole_window(self):
        self.base();self.window([self.evt()],results=[{'rid':'rid-one','email':'pessoa@example.test'},{'rid':'rid-second','email':'pessoa@example.test'}])
        self.assertEqual(self.run_action()['codigo_erro'],'ambiguous_result');self.assertEqual(F.query.count(),0)
        self.assertEqual(A.query.one().identificador_externo,'rid-one')

    def test_email_not_global_and_frozen_identity(self):
        self.base();c=Colaborador.query.one();c.email='changed@example.test';db.session.commit()
        self.window([self.evt()],results=[{'rid':'rid-one'}]);self.assertEqual(self.run_action()['estado'],'concluida')
        self.window([],results=[{'rid':'rid-new','email':'changed@example.test'}]);self.assertEqual(self.run_action()['codigo_erro'],'outside_audience')

    def test_unknown_event_target_rejects_window(self):
        self.base();self.window([self.evt(),self.evt(ident='unknown',rid='outside-rid')])
        self.assertEqual(self.run_action()['codigo_erro'],'outside_audience');self.assertEqual(F.query.count(),0)

    def test_event_id_collision_is_rejected(self):
        self.base();self.window([self.evt()]);self.run_action();self.window([self.evt('Email Opened')])
        self.assertEqual(self.run_action()['codigo_erro'],'event_identity_conflict');self.assertEqual(F.query.count(),1)

    def test_participation_token_and_api_key_cannot_be_external_identity(self):
        self.base(True)
        for secret,expected in [('synthetic-training-secret','participation_token_forbidden'),(baseline.KEY,'secret_identifier_forbidden')]:
            self.window([self.evt(ident=secret)]);self.assertEqual(self.run_action()['codigo_erro'],expected)
        self.assertEqual(F.query.count(),0)

    def test_binding_name_digest_and_legacy_are_not_trusted(self):
        self.preparar_base();self.aid=A.query.one().id
        e=db.session.get(E,self.eid);r=db.session.get(R,self.rid);self.nome=f'ONE-CAMP-{e.uuid.hex}-{r.digest}';self.sha='0'*64
        c=db.session.get(Campanha,self.cid);c.gophish_campaign_id=101;db.session.commit();self.window([])
        self.assertEqual(self.run_action('vincular')['codigo_erro'],'binding_mismatch');self.assertIsNone(V.query.one().externo_id)

    def test_disabled_and_permissions_and_draft(self):
        self.base();self.bloqueado('forbidden',resultados.executar,self.eid,self.creator,'importar',uuid4())
        self.bloqueado('forbidden',resultados.executar,self.eid,self.inactive,'importar',uuid4())
        from app.integracoes.gophish.service import GophishService
        from app.integracoes.gophish.config import ConfiguracaoGophish
        self.app.extensions['gophish_service']=GophishService(ConfiguracaoGophish())
        self.bloqueado('disabled',resultados.executar,self.eid,self.admin,'importar',uuid4())

    def test_http_results_contract_is_fail_closed(self):
        service=self.enable('http');self.assertEqual(service.ler_campanha_tecnica(101)['status'],'forbidden')
        self.guard.assert_not_called();self.network.assert_not_called()

    def test_missing_external_binding(self):
        self.preparar_base();self.bloqueado('external_binding_required',resultados.executar,self.eid,self.admin,'importar',uuid4())
        self.assertEqual(self.fake.chamadas,[])

    def ponte(self):
        self.base(True);db.session.rollback();self.bid=educacao.criar_ponte(self.aid,self.admin,confirmar=True)
        self.url='/simulacao/educacao/'+str(self.bid);self.login(None)

    def test_landing_headers_no_sensitive_content_or_third_party(self):
        self.ponte();r=self.client.get(self.url);html=r.get_data(as_text=True)
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertEqual(r.headers['Referrer-Policy'],'no-referrer')
        self.assertIn("script-src 'none'",r.headers['Content-Security-Policy'])
        for value in ('synthetic-training-secret','pessoa@example.test','password','<script','type="email"','Microsoft','Google'):self.assertNotIn(value,html)
        self.assertIn('Acessar treinamento',html);self.assertIsNone(db.session.get(P,self.pid).iniciado_em)

    def test_explicit_action_and_session_bound_alias_no_real_token(self):
        self.ponte();self.client.get(self.url)
        self.assertEqual(B.query.one().estado,'ativa');self.assertIsNone(db.session.get(P,self.pid).iniciado_em)
        r=self.client.post(self.url+'/treinamento',headers=self.headers);self.assertEqual(r.status_code,303)
        self.assertIn('one-session-',r.location);self.assertNotIn('synthetic-training-secret',r.location)
        html=self.client.get(r.location).get_data(as_text=True);self.assertNotIn('synthetic-training-secret',html)
        self.assertIsNone(db.session.get(P,self.pid).iniciado_em)
        other=self.app.test_client();self.assertEqual(other.get(r.location).status_code,404)
        with self.client.session_transaction() as ses:self.assertNotIn('synthetic-training-secret',str(dict(ses)))

    def test_csrf_required_and_no_scripts_in_error_page(self):
        self.ponte();r=self.client.post(self.url+'/treinamento');self.assertEqual(r.status_code,400)
        self.assertNotIn('<script',r.get_data(as_text=True));self.assertEqual(B.query.one().estado,'ativa')

    def test_expired_bridge(self):
        self.ponte();b=B.query.one();b.criada_em=agora()-timedelta(hours=2);b.expira_em=agora()-timedelta(hours=1);db.session.commit()
        self.assertEqual(self.client.get(self.url).status_code,404)

    def test_revoked_bridge_and_session(self):
        self.ponte();r=self.client.post(self.url+'/treinamento',headers=self.headers)
        db.session.rollback();educacao.revogar_ponte(self.aid,self.admin,True)
        self.assertEqual(self.client.get(self.url).status_code,404);self.assertEqual(self.client.get(r.location).status_code,404)

    def test_bridge_unknown_and_no_participation(self):
        self.base();db.session.rollback();bid=educacao.criar_ponte(self.aid,self.admin,confirmar=True);self.login(None)
        r=self.client.get('/simulacao/educacao/'+str(bid));self.assertEqual(r.status_code,200);self.assertNotIn('Acessar treinamento',r.get_data(as_text=True))
        self.assertEqual(self.client.get('/simulacao/educacao/'+str(uuid4())).status_code,404)

    def test_bridge_unique_revocable_and_no_token_audit(self):
        self.base(True);db.session.rollback();key=uuid4()
        self.assertEqual(educacao.criar_ponte(self.aid,self.admin,key=key,confirmar=True),key)
        self.assertEqual(educacao.criar_ponte(self.aid,self.admin,key=key,confirmar=True),key)
        self.bloqueado('bridge_exists',educacao.criar_ponte,self.aid,self.admin,None,24,True)
        self.assertNotIn(str(key),str([a.__dict__ for a in Auditoria.query.all()]))

    def test_privacy_logging_identifier_email_and_exception(self):
        from log_privacidade import sanitizar,FiltroPrivacidade
        bid=str(uuid4());token='training-super-secret'
        msg=f'GET /simulacao/educacao/{bid} /aprender/t/{token} pessoa@example.test duplicate {bid}'
        safe=sanitizar(msg)
        for value in (bid,token,'pessoa@example.test'):self.assertNotIn(value,safe)
        safe=sanitizar('/%73imulacao/%65ducacao/'+bid);self.assertNotIn(bid,safe)
        with self.app.test_request_context('/simulacao/educacao/'+bid):
            try:raise ValueError(bid+' pessoa@example.test')
            except ValueError:
                import sys
                record=logging.LogRecord('test',40,'file',1,'error '+bid,(),sys.exc_info())
                FiltroPrivacidade().filter(record);self.assertNotIn(bid,record.exc_text);self.assertNotIn('pessoa@example.test',record.exc_text)

    def test_admin_technical_view_and_no_general_results(self):
        self.base(True);self.window([self.evt('Email Opened'),self.evt(ident='click')]);self.run_action();self.login(self.admin)
        r=self.client.get(f'/simulacoes/campanhas/{self.cid}');html=r.get_data(as_text=True)
        self.assertEqual(r.status_code,200);self.assertIn('não comprovam intenção humana',html);self.assertIn('não iniciado',html)
        self.assertNotIn('synthetic-training-secret',html);self.assertNotIn(baseline.KEY,html)
        self.login(self.creator);self.assertEqual(self.client.get(f'/simulacoes/campanhas/{self.cid}').status_code,403)

    def test_active_lease_not_recoverable_by_age(self):
        self.base();key=uuid4();db.session.rollback();resultados.reservar(self.eid,self.admin,'importar',key)
        self.bloqueado('lease_active',leases.recuperar,key,self.admin,True)
        self.bloqueado('confirmation_required',leases.recuperar,key,self.admin,False)
        leases.TICKET.set(None)

    def expired_operation(self):
        key=uuid4();db.session.rollback();resultados.reservar(self.eid,self.admin,'importar',key);old=leases.TICKET.get()
        with Session(db.engine) as s,s.begin():
            l=s.execute(select(L).join(O,L.operacao_id==O.id).where(O.uuid==key)).scalar_one();l.expira_em=agora()-timedelta(seconds=1)
        leases.TICKET.set(None);return key,old

    def test_fencing_recovery_rejects_old_executor(self):
        self.base();key,old=self.expired_operation();ticket,eid,action=leases.recuperar(key,self.admin,True)
        leases.TICKET.set(old)
        self.bloqueado('fence_lost',resultados.persistir,self.eid,self.admin,key,self.service.ler_campanha_tecnica(101),101)
        out=resultados.executar(eid,self.admin,action,key,recuperada=ticket);self.assertEqual(out['estado'],'concluida')
        self.assertEqual(L.query.filter_by(geracao=2).count(),1);leases.TICKET.set(None)

    def test_cancel_explicit_local_revokes_bridge(self):
        self.base(True);db.session.rollback();educacao.criar_ponte(self.aid,self.admin,confirmar=True)
        key,old=self.expired_operation();leases.cancelar(key,self.admin,True)
        db.session.expire_all();self.assertEqual(db.session.get(E,self.eid).estado,'cancelada');self.assertEqual(B.query.one().estado,'revogada')
        self.bloqueado('invalid_state',resultados.executar,self.eid,self.admin,'importar',uuid4())
        self.assertFalse(any(m!='GET' for m,p in self.fake.chamadas));leases.TICKET.set(None)


    def test_alias_follows_existing_explicit_start_only(self):
        self.ponte();r=self.client.post(self.url+'/treinamento',headers=self.headers);path=r.location
        self.assertIsNone(db.session.get(P,self.pid).iniciado_em)
        r=self.client.post(path+'/iniciar',headers=self.headers);self.assertEqual(r.status_code,302)
        self.assertNotIn('synthetic-training-secret',r.location)
        db.session.expire_all();self.assertIsNotNone(db.session.get(P,self.pid).iniciado_em)
        html=self.client.get(r.location).get_data(as_text=True)
        self.assertIn('one-session-',html);self.assertNotIn('synthetic-training-secret',html)
        self.assertIsNone(db.session.get(P,self.pid).concluido_em)

    def test_alias_expiration_and_consumed_bridge_reuse(self):
        self.ponte();r=self.client.post(self.url+'/treinamento',headers=self.headers)
        self.assertEqual(self.client.get(self.url).status_code,404)
        self.assertEqual(self.client.post(self.url+'/treinamento',headers=self.headers).location,r.location)
        with self.client.session_transaction() as ses:
            grant=ses['simulacao_acesso'];grant['expira']=0;ses['simulacao_acesso']=grant
        self.assertEqual(self.client.get(r.location).status_code,404)

    def test_read_only_reconcile_post_timeout_without_retry(self):
        self.preparar_base();self.fake.configurar_resposta('POST','/api/groups/',status='timeout',simular_criacao=True)
        db.session.rollback();out=executar_operacao(self.eid,self.admin,'preparar',uuid4())
        self.assertEqual(out['estado'],'resultado_indeterminado');before=list(self.fake.chamadas);self.fake.respostas.clear()
        out=executar_operacao(self.eid,self.admin,'reconciliar',uuid4())
        self.assertTrue(all(m=='GET' for m,p in self.fake.chamadas[len(before):]))
        self.assertEqual(out['estado'],'concluida')
        self.assertEqual(len([m for m,p in self.fake.chamadas if m=='POST']),1)

    def test_missing_after_uncertain_post_stays_indeterminate(self):
        self.preparar_base();self.fake.configurar_resposta('POST','/api/groups/',status='timeout')
        db.session.rollback();executar_operacao(self.eid,self.admin,'preparar',uuid4());self.fake.respostas.clear()
        out=executar_operacao(self.eid,self.admin,'reconciliar',uuid4())
        self.assertEqual(out['estado'],'resultado_indeterminado');self.assertEqual(out['codigo_erro'],'reconciliation_required')
        self.assertEqual(len([m for m,p in self.fake.chamadas if m=='POST']),1)

    def test_recover_auxiliary_executor_only_reads(self):
        from app.simulacoes import preparacao
        self.preparar_base();key=uuid4();db.session.rollback();preparacao.reservar(self.eid,self.admin,'preparar',key)
        with Session(db.engine) as session,session.begin():
            l=session.execute(select(L).join(O,L.operacao_id==O.id).where(O.uuid==key)).scalar_one();l.expira_em=agora()-timedelta(seconds=1)
        leases.TICKET.set(None);ticket,eid,action=leases.recuperar(key,self.admin,True)
        out=executar_operacao(eid,self.admin,'reconciliar',key,recuperada=ticket)
        self.assertEqual(out['estado'],'concluida');self.assertTrue(all(m=='GET' for m,p in self.fake.chamadas))
        self.assertEqual(db.session.get(E,eid).estado,'nao_preparada');leases.TICKET.set(None)

    def test_admin_confirmation_bound_to_actor(self):
        self.base();self.login(self.admin)
        r=self.client.post(f'/simulacoes/campanhas/{self.cid}/confirmar',data={'acao':'importar','execucao':self.eid},headers=self.headers)
        self.assertEqual(r.status_code,200);self.assertIn('Confirmo explicitamente',r.get_data(as_text=True))
        before=list(self.fake.chamadas)
        self.assertEqual(self.client.post(f'/simulacoes/campanhas/{self.cid}/executar',data={},headers=self.headers).status_code,400)
        self.assertEqual(before,self.fake.chamadas)


    def test_secret_embedded_in_event_identifier_rejected(self):
        self.base(True);p=db.session.get(P,self.pid);p.token='trainingsecret'+('x'*32);secret=p.token;db.session.commit()
        self.window([self.evt(ident='prefix-'+secret)])
        self.assertEqual(self.run_action()['codigo_erro'],'participation_token_forbidden');self.assertEqual(F.query.count(),0)

    def test_metadata_cannot_hold_arbitrary_fields(self):
        self.base();self.window([self.evt()]);self.run_action()
        f=F.query.one();f.metadados_tecnicos={'password':'DO-NOT-STORE'}
        with self.assertRaises(SnapshotImutavel):db.session.flush()
        db.session.rollback();self.assertEqual(F.query.one().metadados_tecnicos,{'contrato':1})

    def test_recovery_confirmation_ui_and_actor_scope(self):
        self.base();key,old=self.expired_operation();self.login(self.admin)
        path=f'/simulacoes/campanhas/{self.cid}'
        r=self.client.post(path+'/confirmar',data={'acao':'recuperar','execucao':self.eid,'operacao':str(key)},headers=self.headers)
        self.assertEqual(r.status_code,200)
        import re
        signed=re.search(r'name="confirmacao" value="([^"]+)"',r.get_data(as_text=True)).group(1)
        r=self.client.post(path+'/executar',data={'confirmacao':signed,'confirmar':'sim'},headers=self.headers)
        self.assertEqual(r.status_code,302);db.session.expire_all();self.assertEqual(O.query.filter_by(uuid=key).one().estado,'concluida')
        self.assertTrue(all(m=='GET' for m,p in self.fake.chamadas));self.assertIsNone(leases.TICKET.get())


    def test_cancel_old_failure_blocks_while_another_operation_active(self):
        self.base();self.window([],results=[{'rid':'outside','email':'outside@example.test'}]);first=uuid4()
        self.assertEqual(self.run_action(key=first)['estado'],'falha');second=uuid4();db.session.rollback()
        resultados.reservar(self.eid,self.admin,'importar',second)
        self.bloqueado('busy',leases.cancelar,first,self.admin,True)
        self.assertNotEqual(db.session.get(E,self.eid).estado,'cancelada');leases.TICKET.set(None)


def erro(code,kwargs):
    def test(self):
        self.base();self.fake.configurar_resposta('GET','/api/campaigns/101/results',**kwargs)
        out=self.run_action();self.assertEqual(out['codigo_erro'],code);self.assertEqual(F.query.count(),0)
        self.assertEqual(V.query.one().externo_id,101);self.assertEqual(len(self.fake.chamadas),1)
        self.assertTrue(all(m=='GET' for m,p in self.fake.chamadas))
        self.assertNotIn(baseline.KEY,str(out));self.assertNotIn('raw-secret',str(out))
    return test
for label,code,kwargs in [('404','not_found',{'http_status':404}),('401','auth_error',{'http_status':401}),('403','forbidden',{'http_status':403}),('500','server_error',{'http_status':500,'data':'raw-secret'}),('timeout','timeout',{'status':'timeout'}),('tls','tls_error',{'status':'tls_error'}),('json','invalid_response',{'http_status':200,'data':'raw-secret'})]:
    setattr(ResultadosTest,'test_error_'+label,erro(code,kwargs))


class PostgreSQLResultadosTest(ResultadosTest):
    setUp=anterior.PostgreSQLTest.setUp
    cleanup_pg=anterior.PostgreSQLTest.cleanup_pg

    def test_migration_cycle_after_c(self):
        db.session.rollback();command.downgrade(self.config,'f187c7e4a905')
        self.assertNotIn('eventos_simulacao',inspect(db.engine).get_table_names());self.assertIn('alvos_simulacao',inspect(db.engine).get_table_names())
        command.upgrade(self.config,'a187d7e4a906');self.assertIn('eventos_simulacao',inspect(db.engine).get_table_names())

    def test_concurrent_import_no_duplicates_no_transaction_during_transport(self):
        self.base();self.window([self.evt()]);db.session.rollback();entered=Event();release=Event();orig=self.fake.request;key=uuid4()
        def transport(*args,**kwargs):
            with Session(db.engine) as s,s.begin():s.execute(select(E).where(E.id==self.eid).with_for_update(nowait=True)).scalar_one()
            if not entered.is_set():entered.set();release.wait(10)
            return orig(*args,**kwargs)
        def run():
            with self.app.app_context():return resultados.executar(self.eid,self.admin,'importar',key)
        with patch.object(self.fake,'request',side_effect=transport),ThreadPoolExecutor(max_workers=2) as pool:
            future=pool.submit(run);self.assertTrue(entered.wait(10));self.assertEqual(self.run_action(key=key)['estado'],'processando')
            self.bloqueado('busy',resultados.executar,self.eid,self.admin,'importar',uuid4());release.set();self.assertEqual(future.result(timeout=20)['estado'],'concluida')
        self.run_action();self.assertEqual(F.query.count(),1)

    def test_legacy_processing_requires_executor_confirmation(self):
        self.base();key=uuid4();db.session.rollback()
        with Session(db.engine) as session,session.begin():
            o=O(uuid=key,execucao_id=self.eid,acao='reconciliar',responsavel_id=self.admin,estado='processando',tentativa=1,resultado={})
            session.add(o);session.flush();session.add(L(operacao_id=o.id,geracao=0,estado='legado'))
        self.bloqueado('legacy_executor_unconfirmed',leases.recuperar,key,self.admin,True)
        ticket,eid,action=leases.recuperar(key,self.admin,True,True)
        out=executar_operacao(eid,self.admin,'reconciliar',key,recuperada=ticket)
        self.assertEqual(out['estado'],'concluida');leases.TICKET.set(None)

    def test_external_campaign_unique_constraint(self):
        self.base();db.session.rollback()
        with self.assertRaises(SQLAlchemyError):
            with db.engine.begin() as conn:
                conn.execute(text("INSERT INTO vinculos_campanha_simulacao (execucao_id,revisao_id,instancia_id,externo_id,nome_tecnico,digest,estado,criado_em) SELECT execucao_id,revisao_id,instancia_id,externo_id,nome_tecnico,digest,estado,criado_em FROM vinculos_campanha_simulacao"))

    def test_sql_fencing_and_immutable_events(self):
        self.base();self.window([self.evt()]);self.run_action();db.session.rollback()
        with self.assertRaises(SQLAlchemyError):
            with db.engine.begin() as conn:conn.execute(text("UPDATE eventos_simulacao SET tipo='mensagem_aberta'"))
        key,old=self.expired_operation();ticket,_,_=leases.recuperar(key,self.admin,True);db.session.rollback();leases.TICKET.set(None)
        with self.assertRaises(SQLAlchemyError):
            with db.engine.begin() as conn:
                conn.execute(text("SELECT set_config('one.simulacao_fence',:marca,true)"),{'marca':old.marca})
                conn.execute(text("UPDATE operacoes_simulacao SET estado='concluida' WHERE uuid=:key"),{'key':key})
        self.assertEqual(O.query.filter_by(uuid=key).one().estado,'processando')
