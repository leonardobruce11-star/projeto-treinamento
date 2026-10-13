"""HTTP D3; fixtures sintéticos e PostgreSQL descartável exclusivamente."""
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import text
from app.extensions import db
from app.models import SubmissaoQuiz as S, ResultadoQuiz as R, ParticipacaoTreinamento as P
from app.utils.submissoes_quiz import ErroQuiz
import app.simulacoes.preparacao
import test_portal_treinamento_quiz as fixture

class QuizHTTPTest(unittest.TestCase):
    postgres=False
    setUpClass=fixture.QuizTest.__dict__['setUpClass']
    tearDownClass=fixture.QuizTest.__dict__['tearDownClass']
    request=fixture.QuizTest.request
    finish_modules=fixture.QuizTest.finish_modules

    def setUp(self):
        fixture.QuizTest.setUp(self)
        self.finish_modules()
        self.prepare_url=self.quiz_url+'/preparar'
        self.bad={**self.answers,f'questao_{self.qids[1]}':self.wrong}

    def snapshot(self):
        with self.app.app_context(),db.engine.connect() as c:
            return {name:[tuple(r) for r in c.execute(text('SELECT * FROM '+name+' ORDER BY id'))]
                    for name in ('participacoes_treinamento','resultados_quiz','submissoes_quiz',
                                 'feedbacks_treinamento','tempos_treinamento')}

    def prepare(self,base=None):
        r=self.client.post(self.prepare_url,data={} if base is None else {'resultado_base_id':str(base)},headers=self.headers)
        self.assertEqual(r.status_code,303)
        return r

    def identity(self):
        html=self.client.get(self.quiz_url).get_data(as_text=True)
        return re.search(r'name="submissao_id" value="([^"]+)"',html).group(1)

    def grade(self,identity,answers=None):
        return self.client.post(self.quiz_url,data={**(self.answers if answers is None else answers),
                                                  'submissao_id':identity},headers=self.headers)

    def result_id(self,response):
        self.assertEqual(response.status_code,303)
        return int(response.location.rsplit('/',1)[1])

    def test_get_no_writes_initial_pending_and_result(self):
        before=self.snapshot();r=self.client.get(self.quiz_url)
        self.assertIn('Iniciar avaliação',r.get_data(as_text=True));self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.client.get(self.prepare_url).status_code,405)
        self.prepare();before=self.snapshot();identity=self.identity()
        self.assertEqual(self.snapshot(),before)
        result=self.grade(identity);before=self.snapshot()
        for url in (self.quiz_url,result.location):self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.snapshot(),before)

    def test_failure_retry_approval_history_and_dates(self):
        before=self.snapshot()
        with self.app.app_context():
            p=db.session.get(P,self.pid);progress=(p.ultimo_modulo_id,p.iniciado_em)
        self.prepare();identity=self.identity()
        self.assertEqual(len(self.snapshot()['resultados_quiz']),0)
        first=self.grade(identity,self.bad);base=self.result_id(first)
        html=self.client.get(first.location).get_data(as_text=True)
        for value in ('Avaliação reprovada','50.0%','80','Refazer avaliação'):self.assertIn(value,html)
        failed=self.snapshot();self.prepare(base);second=self.identity()
        self.assertNotEqual(identity,second)
        self.assertEqual(failed['participacoes_treinamento'],self.snapshot()['participacoes_treinamento'])
        approved=self.grade(second);self.result_id(approved)
        html=self.client.get(approved.location).get_data(as_text=True)
        for value in ('Treinamento aprovado em','Avaliação aprovada','Histórico das avaliações'):self.assertIn(value,html)
        self.assertNotIn('Refazer avaliação',html)
        with self.app.app_context():
            p=db.session.get(P,self.pid);results=R.query.order_by(R.numero_tentativa).all()
            self.assertEqual([r.numero_tentativa for r in results],[1,2]);self.assertEqual(p.aprovado_em,results[1].concluido_em)
            self.assertEqual((p.ultimo_modulo_id,p.iniciado_em,p.concluido_em),
                             (*progress,results[0].concluido_em))
        snapshot=self.snapshot()
        self.assertEqual(self.grade(identity).location,first.location)
        self.assertEqual(self.grade(second,self.bad).location,approved.location)
        self.assertEqual(self.prepare(base).location,approved.location)
        self.assertEqual(self.snapshot(),snapshot)
        self.assertEqual(before['tempos_treinamento'],snapshot['tempos_treinamento'])

    def test_double_prepare_pending_resume_and_old_base(self):
        self.prepare();identity=self.identity();self.prepare();self.assertEqual(self.identity(),identity)
        first=self.grade(identity,self.bad);base=self.result_id(first)
        self.prepare(base);second=self.identity()
        pending_html=self.client.get(self.quiz_url).get_data(as_text=True)
        self.assertIn('Histórico das avaliações',pending_html)
        self.assertIn(f'/quiz/resultados/{base}',pending_html)
        again=self.grade(second,self.bad);newbase=self.result_id(again)
        before=self.snapshot();self.assertEqual(self.prepare(base).location,again.location)
        self.assertEqual(self.snapshot(),before)
        self.prepare(newbase);self.assertNotEqual(self.identity(),second)
        # An old correction returns its own receipt even while another submission is pending.
        self.assertEqual(self.grade(identity).location,first.location)

    def test_incomplete_and_recoverable_keep_original_identity(self):
        self.prepare();identity=self.identity();before=self.snapshot()
        r=self.grade(identity,{})
        self.assertEqual(r.status_code,400);self.assertIn(identity,r.get_data(as_text=True))
        self.assertEqual(self.snapshot(),before)
        for code in ('ocupado','falha_banco'):
            with patch('app.portal_treinamento.routes.corrigir_submissao',side_effect=ErroQuiz(code)):
                r=self.grade(identity)
            self.assertEqual(r.status_code,503);self.assertIn(identity,r.get_data(as_text=True))
            self.assertEqual(self.snapshot(),before)
        from sqlalchemy.exc import SQLAlchemyError
        with patch('app.portal_treinamento.routes.corrigir_submissao',side_effect=ErroQuiz('falha_banco')):
            with patch('app.portal_treinamento.routes._contexto_quiz',side_effect=SQLAlchemyError('private-recovery-diagnostic')):
                response=self.grade(identity)
        self.assertEqual(response.status_code,503)
        self.assertIn('Atualizar ou retomar avaliação',response.get_data(as_text=True))
        self.assertNotIn(identity,response.get_data(as_text=True))
        self.assertNotIn('private-recovery-diagnostic',response.get_data(as_text=True))
        self.assertEqual(self.snapshot(),before)
        self.result_id(self.grade(identity))

    def test_prepare_retry_keeps_requested_base(self):
        self.prepare();base=self.result_id(self.grade(self.identity(),self.bad));before=self.snapshot()
        for code in ('ocupado','falha_banco'):
            with patch('app.portal_treinamento.routes.preparar_submissao',side_effect=ErroQuiz(code)):
                r=self.client.post(self.prepare_url,data={'resultado_base_id':base},headers=self.headers)
            self.assertEqual(r.status_code,503)
            self.assertIn(f'name="resultado_base_id" value="{base}"',r.get_data(as_text=True))
            self.assertEqual(self.snapshot(),before)

    def test_old_forms_uuid_invalid_duplicate_and_csrf(self):
        before=self.snapshot()
        r=self.client.post(self.quiz_url,data=self.answers,headers=self.headers)
        self.assertEqual(r.status_code,409);self.assertIn('atualizado',r.get_data(as_text=True));self.assertEqual(self.snapshot(),before)
        for url in (self.quiz_url,self.prepare_url):
            self.assertEqual(self.client.post(url,data=self.answers).status_code,400)
        self.prepare();identity=self.identity();before=self.snapshot()
        for bad in ('',str(uuid4()),'invalido'):
            self.assertEqual(self.grade(bad).status_code,404)
        from werkzeug.datastructures import MultiDict
        self.assertEqual(self.client.post(self.quiz_url,data=MultiDict([('submissao_id',identity),('submissao_id',identity)]),headers=self.headers).status_code,409)
        self.assertEqual(self.snapshot(),before)

    def test_ownership_get_post_and_unavailable_credentials(self):
        self.prepare();identity=self.identity();result=self.grade(identity);rid=self.result_id(result)
        before=self.snapshot()
        for token in ('unico','desconhecido'):
            url=f'/aprender/t/{token}/quiz'
            self.assertEqual(self.client.get(url+f'/resultados/{rid}').status_code,404)
            self.assertEqual(self.client.post(url,data={'submissao_id':identity},headers=self.headers).status_code,404)
            self.assertEqual(self.client.post(url+'/preparar',data={'resultado_base_id':rid},headers=self.headers).status_code,404 if token=='desconhecido' else 409)
        self.assertEqual(self.snapshot(),before)
        with self.app.app_context():
            p=db.session.get(P,self.pid);p.colaborador.ativo=False;db.session.commit()
        before=self.snapshot()
        for url in (self.quiz_url,result.location):self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.grade(identity).status_code,404);self.assertEqual(self.snapshot(),before)

    def test_legacy_null_is_not_failed(self):
        with self.app.app_context():
            p=db.session.get(P,self.pid)
            from datetime import datetime,timezone
            p.concluido_em=datetime.now(timezone.utc)
            r=R(participacao_id=self.pid,total_questoes=2,total_acertos=0,percentual=0,aprovado=None)
            db.session.add(r);db.session.commit();rid=r.id
        before=self.snapshot();html=self.client.get(self.quiz_url).get_data(as_text=True)
        self.assertIn('Histórico não classificado',html);self.assertNotIn('Refazer avaliação',html)
        self.assertEqual(self.client.post(self.prepare_url,data={'resultado_base_id':rid},headers=self.headers).status_code,409)
        self.assertEqual(self.snapshot(),before)

    def test_safe_error_messages(self):
        for code in ('base_invalida','pendente_obsoleta','historica','inconsistente','aprovada','numero_esgotado','indisponivel'):
            before=self.snapshot()
            with patch('app.portal_treinamento.routes.preparar_submissao',side_effect=ErroQuiz(code)):
                r=self.client.post(self.prepare_url,headers=self.headers)
            self.assertEqual(r.status_code,404 if code=='indisponivel' else 409)
            self.assertNotIn('SQL',r.get_data(as_text=True));self.assertEqual(self.snapshot(),before)

    def test_real_commit_failure_rolls_back_and_retains_identity(self):
        from sqlalchemy import event
        from sqlalchemy.orm import Session
        from sqlalchemy.exc import SQLAlchemyError
        self.prepare();identity=self.identity();before=self.snapshot()
        def fail(session):
            session.flush()
            raise SQLAlchemyError('synthetic-private-diagnostic')
        event.listen(Session,'before_commit',fail)
        try:response=self.grade(identity)
        finally:event.remove(Session,'before_commit',fail)
        self.assertEqual(response.status_code,503)
        html=response.get_data(as_text=True)
        self.assertIn(identity,html);self.assertNotIn('synthetic-private-diagnostic',html)
        self.assertEqual(self.snapshot(),before)
        self.result_id(self.grade(identity))

    def test_csrf_recovery_and_privacy_headers(self):
        self.prepare();identity=self.identity();before=self.snapshot()
        response=self.client.post(self.quiz_url,data={'submissao_id':identity})
        self.assertEqual(response.status_code,400)
        self.assertIn('Atualizar ou retomar avaliação',response.get_data(as_text=True))
        self.assertNotIn(identity,response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(response.headers['Referrer-Policy'],'no-referrer')
        self.assertEqual(self.identity(),identity);self.assertEqual(self.snapshot(),before)

    def test_prepare_without_quiz_and_invalid_base_do_not_write(self):
        from werkzeug.datastructures import MultiDict
        before=self.snapshot()
        response=self.client.post('/aprender/t/vazio/quiz/preparar',headers=self.headers)
        self.assertEqual(response.status_code,409)
        self.assertIn('não possui avaliação',response.get_data(as_text=True))
        for data in ({'resultado_base_id':'-1'}, {'resultado_base_id':'9'*5000},
                     MultiDict([('resultado_base_id','1'),('resultado_base_id','2')])):
            self.assertEqual(self.client.post(self.prepare_url,data=data,headers=self.headers).status_code,409)
        self.assertEqual(self.snapshot(),before)

    def test_approved_participation_rejects_obsolete_pending_form(self):
        self.prepare();approved=self.grade(self.identity());base=self.result_id(approved)
        # Simulate an obsolete persisted form from an unsupported historical writer.
        with self.app.app_context():
            sub=S(participacao_id=self.pid,resultado_base_id=base)
            db.session.add(sub);db.session.commit();identity=str(sub.id)
        before=self.snapshot()
        html=self.client.get(self.quiz_url).get_data(as_text=True)
        self.assertNotIn('Refazer avaliação',html)
        self.assertNotIn('name="submissao_id"',html)
        self.assertEqual(self.grade(identity).status_code,409)
        self.assertEqual(self.client.post(self.prepare_url,data={'resultado_base_id':base},headers=self.headers).status_code,303)
        self.assertEqual(self.snapshot(),before)

class QuizHTTPPostgresTest(QuizHTTPTest):
    postgres=True

    def parallel(self,url,payloads):
        barrier=Barrier(len(payloads))
        def worker(payload):
            client=self.app.test_client();headers={'X-CSRFToken':client.get('/_csrf').json['token']}
            barrier.wait(10)
            r=client.post(url,data=payload,headers=headers)
            return r.status_code,r.location
        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:return list(pool.map(worker,payloads))

    def test_parallel_preparation_and_different_answers_two_tabs(self):
        responses=self.parallel(self.prepare_url,[{}]*4)
        self.assertEqual([r[0] for r in responses],[303]*4)
        self.assertEqual(len(self.snapshot()['submissoes_quiz']),1)
        identity=self.identity()
        responses=self.parallel(self.quiz_url,[{**answers,'submissao_id':identity} for answers in (self.answers,self.bad,self.answers,self.bad)])
        self.assertEqual([r[0] for r in responses],[303]*4)
        self.assertEqual(len(set(r[1] for r in responses)),1)
        self.assertEqual(len(self.snapshot()['resultados_quiz']),1)

import test_etapa192d2 as d2

class AliasHTTPPostgresTest(unittest.TestCase):
    setUp=d2.AliasSubmissaoPostgresTest.setUp
    cleanup_pg=d2.AliasSubmissaoPostgresTest.cleanup_pg
    preparar_base=d2.AliasSubmissaoPostgresTest.preparar_base
    login=d2.AliasSubmissaoPostgresTest.login
    post=d2.AliasSubmissaoPostgresTest.post
    enable=d2.AliasSubmissaoPostgresTest.enable
    call=d2.AliasSubmissaoPostgresTest.call
    revoke=d2.AliasSubmissaoPostgresTest.revoke
    result_count=d2.AliasSubmissaoPostgresTest.result_count

    def client_access(self):
        from flask import g
        g.pop('csrf_token',None)
        client=self.app.test_client()
        with client.session_transaction() as session:session['simulacao_acesso']=dict(self.grant)
        url=f'/aprender/t/{self.alias}/quiz'
        r=client.get(url)
        self.assertEqual(r.status_code,200)
        csrf=re.search(r'name="csrf_token"\s+value="([^"]+)"',r.get_data(as_text=True)).group(1)
        return client,url,{'X-CSRFToken':csrf}

    def test_alias_http_prepare_grade_replay_and_result_ownership(self):
        client,url,headers=self.client_access()
        before=self.result_count()
        self.assertEqual(client.post(url+'/preparar',headers=headers).status_code,303)
        self.assertEqual(self.result_count(),before)
        result=client.post(url,data={**self.answers,'submissao_id':str(self.sub.submissao_id)},headers=headers)
        self.assertEqual(result.status_code,303)
        self.assertIn(f'/t/{self.alias}/',result.location)
        self.assertNotIn(str(self.sub.submissao_id),result.location)
        self.assertEqual(client.get(result.location).status_code,200)
        self.assertEqual(client.post(url,data={'submissao_id':str(self.sub.submissao_id)},headers=headers).location,result.location)
        with client.session_transaction() as session:session.pop('simulacao_acesso')
        for path in (url,result.location):self.assertEqual(client.get(path).status_code,404)
        self.assertEqual(client.post(url,data={'submissao_id':str(self.sub.submissao_id)},headers=headers).status_code,404)
        self.assertEqual(self.result_count(),1)
        self.network.assert_not_called();self.guard.assert_not_called()

    def test_expired_and_revoked_alias_get_post_no_disclosure(self):
        client,url,headers=self.client_access()
        with client.session_transaction() as session:
            grant=dict(self.grant);grant['expira']=0;session['simulacao_acesso']=grant
        for path in (url,url+'/resultados/9999'):self.assertEqual(client.get(path).status_code,404)
        for path in (url,url+'/preparar'):
            self.assertEqual(client.post(path,data={'submissao_id':str(self.sub.submissao_id)},headers=headers).status_code,404)
        with client.session_transaction() as session:session['simulacao_acesso']=dict(self.grant)
        self.revoke()
        for path in (url,url+'/resultados/9999'):self.assertEqual(client.get(path).status_code,404)
        for path in (url,url+'/preparar'):
            self.assertEqual(client.post(path,data={'submissao_id':str(self.sub.submissao_id)},headers=headers).status_code,404)
        self.assertEqual(self.result_count(),0)
