"""19.1C: integração e concorrência em bancos exclusivos de testes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from threading import Barrier, Event
import unittest
from unittest.mock import patch
from sqlalchemy import event, select, update
from sqlalchemy.exc import SQLAlchemyError
from app.extensions import db
from app.models import ParticipacaoTreinamento as P, ResultadoQuiz as R, Treinamento as T
from app.models.questao import Questao
from app.models.alternativa import Alternativa
import app.simulacoes.preparacao
import test_portal_treinamento_quiz as fixture


class IntegracaoAprovacaoTest(unittest.TestCase):
    postgres = False
    setUpClass = fixture.QuizTest.__dict__['setUpClass']
    tearDownClass = fixture.QuizTest.__dict__['tearDownClass']
    setUp = fixture.QuizTest.setUp
    request = fixture.QuizTest.request
    state = fixture.QuizTest.state
    finish_modules = fixture.QuizTest.finish_modules
    without_quiz = fixture.QuizTest.without_quiz
    post_quiz = fixture.QuizTest.post_quiz

    def configurar(self, total, acertos, nota=None):
        self.without_quiz()
        with self.app.app_context():
            p = db.session.get(P, self.pid)
            self.tid = p.treinamento_id
            p.treinamento.nota_minima_percentual = nota
            dados = {}
            for i in range(total):
                q = Questao(treinamento_id=self.tid, enunciado=f'Q{i}', ordem=i)
                db.session.add(q); db.session.flush()
                a = Alternativa(questao_id=q.id, texto='C', correta=True)
                b = Alternativa(questao_id=q.id, texto='E', correta=False)
                db.session.add_all([a,b]); db.session.flush()
                dados[f'questao_{q.id}'] = str(a.id if i < acertos else b.id)
            db.session.commit()
        return dados

    def snap(self):
        with self.app.app_context():
            p = db.session.get(P, self.pid)
            rs = [tuple(getattr(r,k) for k in ('id','total_questoes','total_acertos','percentual',
                'percentual_minimo_aplicado','minimo_acertos_aplicado','aprovado','concluido_em'))
                for r in R.query.filter_by(participacao_id=self.pid).all()]
            return p.concluido_em, p.aprovado_em, p.ultimo_modulo_id, rs

    def conferir(self, total, acertos, minimo, aprovado, nota=None):
        dados = self.configurar(total, acertos, nota); self.finish_modules()
        self.assertEqual(self.post_quiz(dados).status_code,303)
        with self.app.app_context():
            r = R.query.filter_by(participacao_id=self.pid).one(); p=r.participacao
            self.assertEqual(r.aprovado,aprovado)
            self.assertEqual(r.minimo_acertos_aplicado,minimo)
            self.assertEqual(r.percentual_minimo_aplicado, Decimal(nota or (80 if total<10 else 70)))
            self.assertEqual(p.concluido_em,r.concluido_em)
            if aprovado:self.assertEqual(p.aprovado_em,p.concluido_em)
            else:self.assertIsNone(p.aprovado_em)
        return dados

    def test_5_de_6_aprovado(self): self.conferir(6,5,5,True)
    def test_4_de_6_reprovado(self):
        self.conferir(6,4,5,False)
        with self.app.app_context():self.assertEqual(R.query.one().percentual,66.67)
    def test_7_de_10_aprovado(self): self.conferir(10,7,7,True)
    def test_6_de_10_reprovado(self): self.conferir(10,6,7,False)
    def test_9_questoes(self): self.conferir(9,8,8,True)
    def test_manual(self): self.conferir(6,3,3,True,'50.00')
    def test_manual_7001(self): self.conferir(10,7,8,False,'70.01')
    def test_manual_7001_aprovado(self): self.conferir(10,8,8,True,'70.01')

    def test_snapshot_e_reenvio_nao_reavaliam(self):
        dados=self.conferir(6,4,5,False); antes=self.snap()
        with self.app.app_context():db.session.get(T,self.tid).nota_minima_percentual=Decimal(50);db.session.commit()
        for method in ('GET','POST'):
            r=self.client.get(self.quiz_url) if method=='GET' else self.client.get(self.post_quiz(dados).location)
            self.assertEqual(r.status_code,200);self.assertEqual(self.snap(),antes)
            self.assertIn('Avaliação reprovada',r.get_data(as_text=True))
        self.assertIn('80.00%',r.get_data(as_text=True))

    def test_primeira_aprovacao_nao_substituida(self):
        dados=self.configurar(6,5);self.finish_modules()
        with self.app.app_context():
            p=db.session.get(P,self.pid);p.aprovado_em=datetime(2025,1,1,tzinfo=timezone.utc);db.session.commit()
        antes=self.snap()[1];self.post_quiz(dados);self.assertEqual(self.snap()[1],antes)

    def test_sem_quiz_get_e_post_idempotentes(self):
        self.without_quiz();self.finish_modules();antes=self.snap()
        self.assertEqual(self.client.get(self.quiz_url).status_code,200)
        self.assertEqual(self.snap(),antes)
        self.assertEqual(self.post_quiz().status_code,302)
        depois=self.snap();self.assertEqual(depois[0],depois[1]);self.assertIsNotNone(depois[0]);self.assertEqual(depois[3],[])
        self.assertEqual(self.post_quiz().status_code,302);self.assertEqual(self.snap(),depois)

    def test_sem_quiz_incompleto_nao_aprova(self):
        self.without_quiz();antes=self.snap();self.assertEqual(self.post_quiz().status_code,403);self.assertEqual(self.snap(),antes)

    def test_falha_apos_flush_rollback_integral(self):
        dados=self.configurar(6,5);self.finish_modules();antes=self.snap()
        def falhar(session):session.flush();raise SQLAlchemyError('falha injetada')
        with patch('app.portal_treinamento.routes.corrigir_submissao',side_effect=__import__('app.utils.submissoes_quiz',fromlist=['ErroQuiz']).ErroQuiz('falha_banco')):self.assertEqual(self.post_quiz(dados).status_code,503)
        self.assertEqual(self.snap(),antes)
        self.assertEqual(self.post_quiz(dados).status_code,303)

    def test_sem_quiz_falha_apos_flush(self):
        self.without_quiz();self.finish_modules();antes=self.snap()
        def falhar(session):session.flush();raise SQLAlchemyError('falha injetada')
        with patch('sqlalchemy.orm.Session.commit',falhar):self.assertEqual(self.post_quiz().status_code,503)
        self.assertEqual(self.snap(),antes)

    def test_historico_null_preservado(self):
        self.finish_modules()
        with self.app.app_context():
            p=db.session.get(P,self.pid);p.concluido_em=datetime(2025,1,1,tzinfo=timezone.utc)
            db.session.add(R(participacao_id=self.pid,total_questoes=2,total_acertos=2,percentual=100));db.session.commit()
        antes=self.snap()
        self.assertEqual(self.post_quiz(self.answers).status_code,409)
        response=self.client.get(self.quiz_url);self.assertIn('não foi classificado',response.get_data(as_text=True));self.assertEqual(self.snap(),antes)

    def test_sem_quiz_historico_nao_recebe_backfill(self):
        self.without_quiz();self.finish_modules()
        with self.app.app_context():db.session.get(P,self.pid).concluido_em=datetime(2025,1,1,tzinfo=timezone.utc);db.session.commit()
        antes=self.snap();self.post_quiz();self.assertEqual(self.snap(),antes);self.assertIsNone(antes[1])

    def test_feedback_disponivel_para_reprovado(self):
        self.conferir(6,4,5,False)
        self.assertEqual(self.client.get('/aprender/t/principal/feedback').status_code,200)
        dados={k:'4' for k in ('avaliacao_geral','avaliacao_conteudo','avaliacao_clareza','avaliacao_utilidade')}
        self.assertEqual(self.client.post('/aprender/t/principal/feedback',data=dados,headers=self.headers).status_code,303)

    def test_interface_aprovado(self):
        self.conferir(6,5,5,True)
        response=self.client.get(self.quiz_url)
        self.assertIn('<h2>Avaliação aprovada</h2>',response.get_data(as_text=True))
        self.assertEqual(response.headers['Cache-Control'],'no-store');self.assertEqual(response.headers['Referrer-Policy'],'no-referrer')


class IntegracaoAprovacaoPostgresTest(IntegracaoAprovacaoTest):
    postgres = True

    def paralelo(self, a, b):
        with self.app.app_context():engine=db.engine
        gate=Barrier(2)
        def observar(conn,cursor,sql,params,ctx,many):
            if 'FROM participacoes_treinamento' in sql and 'FOR UPDATE' in sql:gate.wait(timeout=15)
        event.listen(engine,'before_cursor_execute',observar)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fs=[pool.submit(a),pool.submit(b)];return [f.result(timeout=25) for f in fs]
        finally:event.remove(engine,'before_cursor_execute',observar)

    def prepare_form(self):
        self.client.post(self.quiz_url+'/preparar',headers=self.headers)
        from app.models import SubmissaoQuiz
        with self.app.app_context():self.form_identity=str(SubmissaoQuiz.query.one().id)

    def worker(self, path, dados=None, heartbeat=False):
        c=self.app.test_client();h={'X-CSRFToken':c.get('/_csrf').json['token']}
        if path == self.quiz_url and dados and getattr(self,'form_identity',None):
            dados=dict(dados,submissao_id=self.form_identity)
        return c.post(path,headers=h,**({'json':{'segundos':30}} if heartbeat else {'data':dados or {}})).status_code

    def test_dois_quizzes_simultaneos(self):
        dados=self.configurar(6,4);self.finish_modules();self.prepare_form()
        self.assertEqual(sorted(self.paralelo(lambda:self.worker(self.quiz_url,dados),lambda:self.worker(self.quiz_url,dados))),[303,303])
        s=self.snap();self.assertEqual(len(s[3]),1);self.assertFalse(s[3][0][6]);self.assertIsNone(s[1]);self.assertEqual(s[0],s[3][0][7])
        self.post_quiz(dados);self.assertEqual(self.snap(),s)

    def test_duas_conclusoes_sem_quiz(self):
        self.without_quiz();self.finish_modules()
        self.assertEqual(self.paralelo(lambda:self.worker(self.quiz_url),lambda:self.worker(self.quiz_url)),[302,302])
        s=self.snap();self.assertEqual(s[0],s[1]);self.assertIsNotNone(s[1]);self.assertEqual(s[3],[])
        self.post_quiz();self.assertEqual(self.snap(),s)

    def test_formulario_legado_x_avanco(self):
        dados=self.configurar(6,5);self.request(0);self.request(1)
        # The legacy form performs no write and acquires no participation lock.
        # Synchronize HTTP starts, rather than expecting two writing transactions.
        gate=Barrier(2)
        def send(path,data=None):
            gate.wait(10)
            return self.worker(path,data)
        with ThreadPoolExecutor(max_workers=2) as pool:
            stale=pool.submit(send,self.quiz_url,dados)
            advance=pool.submit(send,f'/aprender/t/principal/modulo/{self.ids[2]}/avancar')
            codes=[stale.result(20),advance.result(20)]
        self.assertIn(codes[0],[409,403]);self.assertEqual(codes[1],302)
        self.assertEqual(self.snap()[3],[]);self.assertIsNone(self.snap()[1]);self.post_quiz(dados)
        s=self.snap();self.assertEqual(len(s[3]),1);self.assertEqual(s[0],s[1]);self.assertEqual(s[2],self.ids[2])

    def preparar_x_avanco(self, preparacao_primeiro):
        from app.models import SubmissaoQuiz
        from app.utils import submissoes_quiz as service
        from test_etapa192c3 import wait_for_database_lock
        self.configurar(6,5); self.request(0); self.request(1)
        with self.app.app_context():
            engine=db.engine
        locked=Event(); release=Event(); attempted=Event()
        first_pid=[]; second_pid=[]; locks={}; observed=[]
        def before(conn,cursor,sql,params,ctx,many):
            if not any(lock in sql for lock in ('FOR UPDATE','FOR SHARE','FOR KEY SHARE')):
                return
            pid=conn.connection.driver_connection.info.backend_pid
            table=next((t for t in ('participacoes_treinamento','treinamentos','colaboradores')
                        if 'FROM '+t in sql), None)
            if table:
                locks.setdefault(pid,[]).append((table,sql))
            if table=='participacoes_treinamento' and locked.is_set():
                second_pid.append(pid); attempted.set()
        def after(conn,cursor,sql,params,ctx,many):
            if 'FROM participacoes_treinamento' in sql and 'FOR UPDATE' in sql and not locked.is_set():
                first_pid.append(conn.connection.driver_connection.info.backend_pid)
                locked.set()
                if not release.wait(20):
                    raise AssertionError('Timeout liberando primeiro lock')
        original=service._dados
        def dados(session,p,t,c):
            result=original(session,p,t,c)
            observed.append((p.ultimo_modulo_id,result[0].permitido))
            return result
        prepare=lambda:self.worker(self.quiz_url+'/preparar')
        advance=lambda:self.worker(f'/aprender/t/principal/modulo/{self.ids[2]}/avancar')
        first,second=(prepare,advance) if preparacao_primeiro else (advance,prepare)
        event.listen(engine,'before_cursor_execute',before)
        event.listen(engine,'after_cursor_execute',after)
        try:
            with patch.object(service,'_dados',side_effect=dados), ThreadPoolExecutor(max_workers=2) as pool:
                a=pool.submit(first)
                try:
                    self.assertTrue(locked.wait(15))
                    b=pool.submit(second)
                    self.assertTrue(attempted.wait(15))
                    self.assertNotEqual(first_pid[0],second_pid[0])
                    wait_for_database_lock(engine,second_pid[0])
                    self.assertFalse(a.done()); self.assertFalse(b.done())
                finally:
                    release.set()
                codes=[a.result(25),b.result(25)]
        finally:
            release.set()
            event.remove(engine,'before_cursor_execute',before)
            event.remove(engine,'after_cursor_execute',after)
        self.assertEqual(codes,[409,302] if preparacao_primeiro else [302,303])
        self.assertEqual(observed,[(self.ids[1],False)] if preparacao_primeiro else [(self.ids[2],True)])
        for pid in (first_pid[0],second_pid[0]):
            tables=[t for t,sql in locks[pid]]
            self.assertEqual(tables[:2],['participacoes_treinamento','treinamentos'])
            if 'colaboradores' in tables:
                self.assertEqual(tables[:3],['participacoes_treinamento','treinamentos','colaboradores'])
                self.assertIn('NOWAIT',locks[pid][2][1])
        with self.app.app_context():
            self.assertEqual(SubmissaoQuiz.query.count(),0 if preparacao_primeiro else 1)
            self.assertEqual(R.query.count(),0)
            p=db.session.get(P,self.pid)
            self.assertEqual(p.ultimo_modulo_id,self.ids[2])
            self.assertIsNone(p.concluido_em); self.assertIsNone(p.aprovado_em)
        # An explicit subsequent preparation is authorized after the advance commits.
        self.assertEqual(prepare(),303)
        with self.app.app_context():
            self.assertEqual(SubmissaoQuiz.query.count(),1)
            self.assertEqual(R.query.count(),0)
            self.assertEqual(db.session.get(P,self.pid).ultimo_modulo_id,self.ids[2])

    def test_quiz_x_avanco_preparacao_verifica_primeiro(self):
        self.preparar_x_avanco(True)

    def test_quiz_x_avanco_avanco_confirma_primeiro(self):
        self.preparar_x_avanco(False)

    def test_quiz_x_heartbeat(self):
        dados=self.configurar(6,5);self.finish_modules();self.prepare_form()
        self.assertEqual(self.paralelo(lambda:self.worker(self.quiz_url,dados),lambda:self.worker(f'/aprender/t/principal/modulo/{self.ids[2]}/heartbeat',heartbeat=True)),[303,200])
        s=self.snap();self.assertEqual(len(s[3]),1);self.assertEqual(s[0],s[1])

    def test_conclusao_x_avanco(self):
        self.without_quiz();self.request(0);self.request(1)
        codes=self.paralelo(lambda:self.worker(self.quiz_url),lambda:self.worker(f'/aprender/t/principal/modulo/{self.ids[2]}/avancar'))
        self.assertIn(codes[0],[302,403]);self.assertEqual(codes[1],302)
        if codes[0]==403:self.assertIsNone(self.snap()[1]);self.post_quiz()
        s=self.snap();self.assertEqual(s[0],s[1]);self.assertEqual(s[3],[])

    def test_configuracao_atualizada_antes_do_lock(self):
        dados=self.configurar(10,7,'70.00');self.finish_modules();self.prepare_form()
        with self.app.app_context():engine=db.engine
        waiting=Event()
        def observar(conn,cursor,sql,params,ctx,many):
            if 'FROM treinamentos' in sql and 'FOR SHARE' in sql:waiting.set()
        conn=engine.connect();txn=conn.begin()
        conn.execute(update(T).where(T.id==self.tid).values(nota_minima_percentual=Decimal('70.01')))
        event.listen(engine,'before_cursor_execute',observar)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                f=pool.submit(self.worker,self.quiz_url,dados);self.assertTrue(waiting.wait(15));self.assertFalse(f.done())
                txn.commit();self.assertEqual(f.result(25),303)
        finally:
            if txn.is_active:txn.rollback()
            conn.close();event.remove(engine,'before_cursor_execute',observar)
        s=self.snap();self.assertEqual(s[3][0][4:7],(Decimal('70.01'),8,False));self.assertIsNone(s[1])

    def test_configuracao_aguarda_snapshot_e_commit(self):
        dados=self.configurar(10,7,'70.00');self.finish_modules();self.prepare_form()
        with self.app.app_context():engine=db.engine
        locked=Event();release=Event();update_waiting=Event()
        def depois(conn,cursor,sql,params,ctx,many):
            if 'FROM treinamentos' in sql and 'FOR SHARE' in sql:locked.set();self.assertTrue(release.wait(15))
        def antes(conn,cursor,sql,params,ctx,many):
            if sql.startswith('UPDATE treinamentos'):update_waiting.set()
        def alterar():
            with engine.begin() as c:c.execute(update(T).where(T.id==self.tid).values(nota_minima_percentual=Decimal('70.01')))
        event.listen(engine,'after_cursor_execute',depois);event.listen(engine,'before_cursor_execute',antes)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                quiz=pool.submit(self.worker,self.quiz_url,dados);self.assertTrue(locked.wait(15))
                change=pool.submit(alterar);self.assertTrue(update_waiting.wait(15));self.assertFalse(change.done())
                release.set();self.assertEqual(quiz.result(25),303);change.result(25)
        finally:
            release.set();event.remove(engine,'after_cursor_execute',depois);event.remove(engine,'before_cursor_execute',antes)
        s=self.snap();self.assertEqual(s[3][0][4:7],(Decimal('70.00'),7,True));self.assertEqual(s[0],s[1])
        self.post_quiz(dados);self.assertEqual(self.snap(),s)
