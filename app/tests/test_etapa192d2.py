"""19.2D2: backend interno, constraints, retries e locks PostgreSQL reais."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from datetime import datetime, timezone, timedelta
from uuid import uuid4, uuid1
from unittest.mock import patch
from werkzeug.datastructures import MultiDict
from sqlalchemy import select, text, event, inspect, update
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from alembic import command
from alembic.script import ScriptDirectory
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.extensions import db
from app.models import (SubmissaoQuiz as S, ResultadoQuiz as R, ParticipacaoTreinamento as P,
                        Treinamento as T, Colaborador as C, Questao, Alternativa, ModuloTreinamento)
from app.utils import submissoes_quiz as service
from app.utils.resultados_quiz import primeira_aprovacao, resultado_representativo
from app.utils.feedback_treinamento import registrar_feedback
import test_portal_treinamento_quiz as quiz
import test_etapa192c1 as c1
from test_etapa192c3 import wait_for_database_lock

HEAD = 'f192d2e4a911'
PREVIOUS = 'e192c3e4a910'


class SubmissaoMigrationPostgresTest(unittest.TestCase):
    setUpClass = c1.TentativaMigrationPostgresTest.__dict__['setUpClass']
    tearDownClass = c1.TentativaMigrationPostgresTest.__dict__['tearDownClass']

    def setUp(self):
        c1.TentativaMigrationPostgresTest.setUp(self)
        with self.app.app_context():
            command.upgrade(self.config, PREVIOUS)
            with db.engine.connect() as conn:
                self.before = self.snapshot(conn)
                self.constraints = dict(conn.execute(text("SELECT conname,pg_get_constraintdef(oid) "
                    "FROM pg_constraint WHERE conrelid='resultados_quiz'::regclass")).all())
            command.upgrade(self.config, HEAD)

    def snapshot(self, conn):
        return {name: [dict(r) for r in conn.execute(text('SELECT * FROM '+name+' ORDER BY id')).mappings()]
                for name in ('resultados_quiz', 'participacoes_treinamento', 'treinamentos')}

    def insert(self, conn, pid=5, base=None, result=None, estado='pendente', processed=None):
        return conn.execute(text('INSERT INTO submissoes_quiz '
            '(id,participacao_id,resultado_base_id,resultado_id,estado,criada_em,processada_em) '
            'VALUES (:id,:pid,:base,:result,:estado,:created,:processed) RETURNING id'),
            dict(id=uuid4(), pid=pid, base=base, result=result, estado=estado,
                 created=datetime(2026,1,1,tzinfo=timezone.utc), processed=processed)).scalar_one()

    def rejected(self, **kwargs):
        with self.assertRaises(IntegrityError):
            with db.engine.begin() as conn:
                self.insert(conn, **kwargs)

    def test_upgrade_preserva_legado_constraints_e_metadata(self):
        with self.app.app_context(), db.engine.connect() as conn:
            self.assertEqual(ScriptDirectory.from_config(self.config).get_heads(), [HEAD])
            self.assertEqual(self.snapshot(conn), self.before)
            self.assertEqual(conn.execute(text('SELECT count(*) FROM submissoes_quiz')).scalar_one(), 0)
            now = dict(conn.execute(text("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid='resultados_quiz'::regclass")).all())
            self.assertEqual({k:now[k] for k in self.constraints}, self.constraints)
            self.assertNotIn(c1.OLD_UNIQUE, now)
            self.assertEqual(now['uq_resultados_quiz_participacao_id'], 'UNIQUE (participacao_id, id)')
            self.assertEqual(compare_metadata(MigrationContext.configure(conn,
                opts={'compare_server_default':True}), db.metadata), [])
            self.assertFalse(any('respost' in col['name'] for col in inspect(conn).get_columns('submissoes_quiz')))
            fks=inspect(conn).get_foreign_keys('submissoes_quiz')
            self.assertEqual(len(fks),3)
            self.assertTrue(all(fk['options']['ondelete']=='RESTRICT' for fk in fks))

    def test_upgrade_downgrade_reupgrade_sem_submissoes(self):
        with self.app.app_context():
            command.downgrade(self.config,PREVIOUS)
            with db.engine.connect() as conn:
                self.assertEqual(self.snapshot(conn), self.before)
                self.assertNotIn('submissoes_quiz',inspect(conn).get_table_names())
            command.upgrade(self.config,HEAD)
            with db.engine.connect() as conn:
                self.assertEqual(self.snapshot(conn),self.before)

    def test_downgrade_bloqueia_pendente_e_processada_preserva_head(self):
        with self.app.app_context():
            for processed in (False,True):
                with db.engine.begin() as conn:
                    conn.execute(text('DELETE FROM submissoes_quiz'))
                    self.insert(conn,pid=1,result=1 if processed else None,
                        estado='processada' if processed else 'pendente',
                        processed=datetime(2026,1,2,tzinfo=timezone.utc) if processed else None)
                with db.engine.connect() as conn:
                    before=[tuple(r) for r in conn.execute(text('SELECT * FROM submissoes_quiz'))]
                with self.assertRaisesRegex(RuntimeError,'19.2D2 bloqueado'):
                    command.downgrade(self.config,PREVIOUS)
                with db.engine.connect() as conn:
                    self.assertEqual(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),HEAD)
                    self.assertEqual([tuple(r) for r in conn.execute(text('SELECT * FROM submissoes_quiz'))],before)
                    self.assertEqual(self.snapshot(conn),self.before)

    def test_constraints_inicial_pendente_base_resultado_estado_datas_ownership(self):
        with self.app.app_context():
            with db.engine.begin() as conn:
                self.insert(conn,pid=1,result=1,estado='processada',processed=datetime(2026,1,2,tzinfo=timezone.utc))
            self.rejected(pid=1)  # Inicial única, inclusive depois de processada.
            self.rejected(pid=2,base=1)  # Base de outra participação.
            self.rejected(pid=2,result=1,estado='processada',processed=datetime(2026,1,2,tzinfo=timezone.utc))
            self.rejected(pid=2,result=3,estado='processada',processed=datetime(2026,1,2,tzinfo=timezone.utc))
            self.rejected(pid=999)
            for kwargs in (dict(estado='outra'),dict(estado='processada'),dict(result=2),
                dict(processed=datetime(2026,1,2,tzinfo=timezone.utc)),
                dict(pid=2,result=2,estado='processada',processed=datetime(2025,1,1,tzinfo=timezone.utc)),
                dict(pid=2,base=2,result=2,estado='processada',processed=datetime(2026,1,2,tzinfo=timezone.utc))):
                self.rejected(**kwargs)
            with db.engine.begin() as conn:
                self.insert(conn,pid=2)
            self.rejected(pid=2,base=2)  # Uma pendente por participação.
            with db.engine.begin() as conn:
                conn.execute(text('DELETE FROM submissoes_quiz WHERE participacao_id=2'))
                self.insert(conn,pid=2,base=2)
            self.rejected(pid=2,base=2)
            with self.assertRaises(IntegrityError),db.engine.begin() as conn:
                conn.execute(text('DELETE FROM resultados_quiz WHERE id=2'))
            with self.assertRaises(IntegrityError),db.engine.begin() as conn:
                conn.execute(text('DELETE FROM participacoes_treinamento WHERE id=2'))

    def test_resultado_associado_unico_e_base_unica_apos_processamento(self):
        with self.app.app_context():
            with db.engine.begin() as conn:
                base=c1.TentativaMigrationPostgresTest.insert(self,conn,1,2)
                self.insert(conn,pid=1,result=1,estado='processada',processed=datetime(2026,1,2,tzinfo=timezone.utc))
            # Mesma participação e base diferente: a rejeição deve ser a associação única.
            with self.assertRaises(IntegrityError) as caught,db.engine.begin() as conn:
                self.insert(conn,pid=1,base=base,result=1,estado='processada',processed=datetime(2026,1,2,tzinfo=timezone.utc))
            self.assertEqual(caught.exception.orig.diag.constraint_name,'uq_submissoes_quiz_resultado')
            with db.engine.begin() as conn:
                self.insert(conn,pid=1,base=1,result=base,estado='processada',processed=datetime(2026,1,2,tzinfo=timezone.utc))
            with self.assertRaises(IntegrityError) as caught,db.engine.begin() as conn:
                self.insert(conn,pid=1,base=1)
            self.assertEqual(caught.exception.orig.diag.constraint_name,'uq_submissoes_quiz_base')

    def test_downgrade_espera_escritor_e_nao_descarta_submissao(self):
        with self.app.app_context(): engine=db.engine
        attempted=Event(); backend=[]
        def observe(conn,cursor,sql,params,ctx,many):
            if sql.startswith('LOCK TABLE participacoes_treinamento, resultados_quiz, submissoes_quiz'):
                backend.append(conn.connection.driver_connection.info.backend_pid);attempted.set()
        def down():
            with self.app.app_context():
                try:command.downgrade(self.config,PREVIOUS)
                except RuntimeError:return 'bloqueado'
                return 'removido'
        event.listen(engine,'before_cursor_execute',observe)
        try:
            with engine.connect() as conn:
                tx=conn.begin()
                conn.execute(text('SELECT id FROM participacoes_treinamento WHERE id=5 FOR UPDATE'))
                self.insert(conn)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    f=pool.submit(down)
                    try:
                        self.assertTrue(attempted.wait(10));wait_for_database_lock(self.admin,backend[0])
                    finally:tx.commit()
                    self.assertEqual(f.result(15),'bloqueado')
        finally:event.remove(engine,'before_cursor_execute',observe)


class SubmissaoServicePostgresTest(unittest.TestCase):
    postgres=True
    setUpClass=quiz.QuizTest.__dict__['setUpClass']
    tearDownClass=quiz.QuizTest.__dict__['tearDownClass']
    request=quiz.QuizTest.request
    finish_modules=quiz.QuizTest.finish_modules

    def setUp(self):
        quiz.QuizTest.setUp(self)
        self.finish_modules()
        self.bad={**self.answers,f'questao_{self.qids[1]}':self.wrong}

    def call(self, fn, *args):
        with self.app.app_context():return fn(*args)

    def prepare(self, base=None, token='principal'):
        return self.call(service.preparar_submissao,token,base)

    def grade(self, sub, answers=None, token='principal'):
        return self.call(service.corrigir_submissao,token,sub.submissao_id,
                         self.answers if answers is None else answers)

    def state(self):
        with self.app.app_context(),db.engine.connect() as conn:
            return {table:[dict(r) for r in conn.execute(text('SELECT * FROM '+table+' ORDER BY id')).mappings()]
                    for table in ('participacoes_treinamento','resultados_quiz','submissoes_quiz',
                                  'feedbacks_treinamento','tempos_treinamento')}

    def denied(self, code, fn, *args):
        with self.assertRaises(service.ErroQuiz) as ctx:fn(*args)
        self.assertEqual(ctx.exception.codigo,code)
        return ctx.exception

    def failure(self):
        return self.grade(self.prepare(),self.bad)

    def test_preparacao_idempotente_sem_efeitos_colaterais_uuid4_recuperavel(self):
        before=self.state();s=self.prepare();again=self.prepare()
        self.assertEqual(s.submissao_id.version,4)
        self.assertEqual(s.submissao_id,again.submissao_id)
        self.assertTrue(s.criada);self.assertFalse(again.criada)
        after=self.state()
        for table in before:
            if table!='submissoes_quiz':self.assertEqual(after[table],before[table])
        self.assertNotIn(str(s.submissao_id),repr(s))
        with self.app.app_context():db.engine.dispose()
        self.assertEqual(self.prepare().submissao_id,s.submissao_id)

    def test_inicial_reprovacoes_aprovacao_primeira_datas_snapshots_replay(self):
        s=self.prepare();first=self.grade(s,self.bad);before=self.state()
        self.assertFalse(first.aprovado);self.assertEqual(first.numero_tentativa,1)
        self.assertEqual(self.grade(s,{}),first)
        self.assertEqual(self.grade(s,self.answers),first)
        self.assertEqual(self.state(),before)
        secondsub=self.prepare(first.resultado_id);second=self.grade(secondsub,self.bad)
        thirdsub=self.prepare(second.resultado_id);third=self.grade(thirdsub)
        self.assertTrue(third.aprovado);self.assertEqual(third.numero_tentativa,3)
        after=self.state();p=next(x for x in after['participacoes_treinamento'] if x['id']==self.pid)
        pfirst=next(x for x in before['participacoes_treinamento'] if x['id']==self.pid)
        self.assertEqual(p['concluido_em'],pfirst['concluido_em'])
        self.assertEqual(p['iniciado_em'],pfirst['iniciado_em']);self.assertEqual(p['ultimo_modulo_id'],pfirst['ultimo_modulo_id'])
        self.assertEqual(p['aprovado_em'],after['resultados_quiz'][2]['concluido_em'])
        self.assertEqual(after['resultados_quiz'][0],before['resultados_quiz'][0])
        self.assertEqual(self.prepare(first.resultado_id).submissao_id,secondsub.submissao_id)
        self.assertEqual(self.grade(s,self.answers),first)
        self.denied('aprovada',self.prepare,third.resultado_id)
        with self.app.app_context():
            rs=db.session.execute(select(R).order_by(R.numero_tentativa)).scalars().all()
            self.assertEqual(primeira_aprovacao(rs).id,third.resultado_id)
            self.assertEqual(resultado_representativo(rs).id,third.resultado_id)

    def test_respostas_invalidas_nao_consumem_pendente_cliente_numero_ignorado(self):
        s=self.prepare();before=self.state()
        invalid=[{},dict(self.answers,questao_999='1'),
                 {**self.answers,f'questao_{self.qids[0]}':self.foreign_answer},
                 MultiDict(list(self.answers.items())+[(f'questao_{self.qids[0]}','1')]),
                 {**self.answers,f'questao_{self.qids[0]}':True}]
        for answers in invalid:
            self.denied('respostas_invalidas',self.grade,s,answers)
            self.assertEqual(self.state(),before)
        result=self.grade(s,dict(self.answers,numero_tentativa='500'))
        self.assertEqual(result.numero_tentativa,1)

    def test_desconhecido_ou_outra_participacao_nao_expoe_identidade(self):
        s=self.prepare();before=self.state()
        for token,identity in [('principal',uuid4()),('unico',s.submissao_id),('invalido',s.submissao_id),
                               ('principal','malformed'),('principal',uuid1())]:
            exc=self.denied('indisponivel',self.call,service.corrigir_submissao,token,identity,self.answers)
            self.assertNotIn(str(identity),str(exc))
        self.assertEqual(self.state(),before)

    def test_base_alheia_antiga_invalida_e_aprovacao_impedem_nova(self):
        first=self.failure()
        with self.app.app_context(),db.engine.begin() as conn:
            other=c1.TentativaMigrationPostgresTest.insert(self,conn,2,1)
        self.denied('base_invalida',self.prepare,other)
        for base in ('1',True,0,-1):self.denied('base_invalida',self.prepare,base)
        s=self.prepare(first.resultado_id);self.grade(s,self.bad)
        self.assertEqual(self.prepare(first.resultado_id).submissao_id,s.submissao_id)
        before=self.state();self.denied('base_invalida',self.prepare,other);self.assertEqual(before,self.state())

    def test_legacy_historico_sem_quiz_inconsistente_e_estrutura_invalida(self):
        with self.app.app_context(),db.engine.begin() as conn:
            conn.execute(text('UPDATE participacoes_treinamento SET concluido_em=now() WHERE id=:id'),{'id':self.pid})
            c1.TentativaMigrationPostgresTest.insert(self,conn,self.pid,1)
        self.denied('historica',self.prepare)
        with self.app.app_context(),db.engine.begin() as conn:
            conn.execute(text('UPDATE resultados_quiz SET total_questoes=2,total_acertos=1,percentual=50, '
                'percentual_minimo_aplicado=80,minimo_acertos_aplicado=2,aprovado=false'))
            conn.execute(text('UPDATE participacoes_treinamento SET aprovado_em=now() WHERE id=:id'),{'id':self.pid})
        self.denied('inconsistente',self.prepare)
        self.denied('sem_quiz',self.prepare,None,'vazio')

    def test_legacy_historico_antes_classificada_admite_refacao_null_posterior_bloqueia(self):
        with self.app.app_context(),db.engine.begin() as conn:
            conn.execute(text('UPDATE participacoes_treinamento SET concluido_em=now() WHERE id=:id'),{'id':self.pid})
            c1.TentativaMigrationPostgresTest.insert(self,conn,self.pid,1)
            rid=conn.execute(text("INSERT INTO resultados_quiz(participacao_id,numero_tentativa,total_questoes,total_acertos,percentual,percentual_minimo_aplicado,minimo_acertos_aplicado,aprovado,concluido_em) VALUES (:pid,2,2,1,50,80,2,false,now()) RETURNING id"),{'pid':self.pid}).scalar_one()
        s=self.prepare(rid);self.assertEqual(self.grade(s,self.bad).numero_tentativa,3)
        with self.app.app_context(),db.engine.begin() as conn:
            c1.TentativaMigrationPostgresTest.insert(self,conn,self.pid,4)
        self.denied('inconsistente',self.prepare,self.state()['resultados_quiz'][-1]['id'])

    def test_rollback_flush_commit_e_integridade_sem_parciais(self):
        s=self.prepare();before=self.state()
        original=Session.flush
        def fail(session,*args,**kwargs):
            if any(isinstance(item,S) and item.estado=='processada' for item in session.dirty):
                raise SQLAlchemyError('erro privado')
            return original(session,*args,**kwargs)
        with patch.object(Session,'flush',fail):self.denied('falha_banco',self.grade,s)
        self.assertEqual(self.state(),before)
        def before_commit(session):raise SQLAlchemyError('commit privado')
        event.listen(Session,'before_commit',before_commit)
        try:self.denied('falha_banco',self.grade,s)
        finally:event.remove(Session,'before_commit',before_commit)
        self.assertEqual(self.state(),before)
        def integrity(session,*args,**kwargs):
            if any(isinstance(item,R) for item in session.new):
                raise IntegrityError('INSERT private',{'id':str(s.submissao_id)},Exception('private'))
            return original(session,*args,**kwargs)
        with patch.object(Session,'flush',integrity):
            exc=self.denied('falha_banco',self.grade,s)
            self.assertNotIn(str(s.submissao_id),str(exc))
        self.assertEqual(self.state(),before)
        result=self.grade(s);self.assertEqual(self.grade(s),result)

    def test_max_integer_nao_incrementa_nem_altera_pendente(self):
        first=self.failure()
        with self.app.app_context(),db.engine.begin() as conn:
            conn.execute(text('UPDATE resultados_quiz SET numero_tentativa=2147483647 WHERE id=:id'),{'id':first.resultado_id})
        s=self.prepare(first.resultado_id);before=self.state()
        self.denied('numero_esgotado',self.grade,s);self.assertEqual(self.state(),before)

    def parallel(self,functions):
        barrier=Barrier(len(functions))
        def worker(fn):barrier.wait(10);return fn()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            return [f.result(20) for f in [pool.submit(worker,fn) for fn in functions]]

    def test_duas_abas_mesma_identidade_respostas_diferentes_uma_avaliacao(self):
        s=self.prepare()
        results=self.parallel([lambda:self.grade(s,self.answers),lambda:self.grade(s,self.bad)])
        self.assertEqual(results[0],results[1]);self.assertEqual(len(self.state()['resultados_quiz']),1)

    def test_duas_solicitacoes_mesma_base_e_retry_antigo(self):
        first=self.failure()
        a,b=self.parallel([lambda:self.prepare(first.resultado_id),lambda:self.prepare(first.resultado_id)])
        self.assertEqual(a.submissao_id,b.submissao_id)
        second=self.grade(a,self.bad);nextsub=self.prepare(second.resultado_id)
        self.assertEqual(self.prepare(first.resultado_id).submissao_id,a.submissao_id)
        self.assertNotEqual(a.submissao_id,nextsub.submissao_id)
        self.assertEqual(len(self.state()['resultados_quiz']),2)

    def test_duas_identidades_distintas_nao_criam_segunda_avaliacao(self):
        s=self.prepare()
        def bad():
            try:self.call(service.corrigir_submissao,'principal',uuid4(),self.answers)
            except service.ErroQuiz as e:return e.codigo
        results=self.parallel([lambda:self.grade(s),bad])
        self.assertEqual(results[1],'indisponivel');self.assertEqual(len(self.state()['resultados_quiz']),1)

    def test_solicitacao_concorrente_aprovacao_recupera_identidade_antiga(self):
        first=self.failure();s=self.prepare(first.resultado_id)
        results=self.parallel([lambda:self.grade(s),lambda:self.prepare(first.resultado_id)])
        self.assertTrue(results[0].aprovado);self.assertEqual(results[1].submissao_id,s.submissao_id)
        self.denied('aprovada',self.prepare,results[0].resultado_id)
        self.assertEqual(len(self.state()['resultados_quiz']),2)

    def test_feedback_tempo_metricas_portal_antigo_preservados(self):
        first=self.failure()
        feedback=self.call(registrar_feedback,self.pid,dict(avaliacao_geral=4,avaliacao_conteudo=4,
            avaliacao_clareza=4,avaliacao_utilidade=4,comentario='Preservar'))
        before=self.state();s=self.prepare(first.resultado_id);self.grade(s)
        after=self.state()
        self.assertEqual(before['feedbacks_treinamento'],after['feedbacks_treinamento'])
        self.assertEqual(before['tempos_treinamento'],after['tempos_treinamento'])
        from app.utils.painel_colaboradores import consulta_participacoes_painel
        with self.app.app_context():
            row=next(r for r in db.session.execute(consulta_participacoes_painel([1])).mappings() if r['participacao_id']==self.pid)
            self.assertEqual(row['status'],'aprovado');self.assertEqual(row['total_tentativas'],2)
        self.assertEqual(self.client.get(self.quiz_url).status_code,200)
        self.assertEqual(self.client.post(self.quiz_url,data=self.answers,headers=self.headers).status_code,409)
        self.assertEqual(self.state(),after)

    def test_fluxo_portal_sem_uuid_exige_atualizacao(self):
        before=self.state();self.assertEqual(self.client.get(self.quiz_url).status_code,200)
        self.assertEqual(self.client.post(self.quiz_url,data=self.answers,headers=self.headers).status_code,409)
        self.assertEqual(self.state(),before)

    def test_pendente_inicial_nao_transforma_post_legado_em_refacao(self):
        s=self.prepare();before=self.state()
        self.assertEqual(self.client.post(self.quiz_url,data=self.bad,headers=self.headers).status_code,409)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.grade(s).numero_tentativa,1)

    def test_servico_nao_confirma_alteracoes_da_sessao_do_chamador(self):
        with self.app.app_context():
            t=db.session.get(T,1);t.titulo='Alteração não confirmada'
            receipt=service.preparar_submissao('principal')
            with db.engine.connect() as conn:
                self.assertEqual(conn.execute(text('SELECT titulo FROM treinamentos WHERE id=1')).scalar_one(),'Principal')
            db.session.rollback()
        self.assertIsNone(receipt.resultado_id)

    def test_estrutura_invalida_ou_cursor_incompleto_nao_prepara(self):
        with self.app.app_context(),db.engine.begin() as conn:
            conn.execute(text('UPDATE participacoes_treinamento SET ultimo_modulo_id=NULL WHERE id=:id'),{'id':self.pid})
        self.denied('inconsistente',self.prepare)
        with self.app.app_context(),db.engine.begin() as conn:
            conn.execute(text('UPDATE participacoes_treinamento SET ultimo_modulo_id=:mid WHERE id=:id'),{'mid':self.ids[-1],'id':self.pid})
            conn.execute(text('UPDATE alternativas SET correta=false WHERE questao_id=:qid'),{'qid':self.qids[0]})
        self.denied('inconsistente',self.prepare)

    def test_desativacao_primeiro_colaborador_nowait_treinamento_espera(self):
        for table in ('colaboradores','treinamentos'):
            s=self.prepare();before=self.state()
            with self.app.app_context():engine=db.engine
            attempted=Event();pids=[]
            def observer(conn,cursor,sql,params,ctx,many):
                if 'FROM '+table in sql and 'FOR SHARE' in sql:
                    pids.append(conn.connection.driver_connection.info.backend_pid);attempted.set()
            def grade():
                try:self.grade(s);return 'gravou'
                except service.ErroQuiz as e:return e.codigo
            event.listen(engine,'before_cursor_execute',observer)
            try:
                with engine.connect() as conn:
                    tx=conn.begin();conn.execute(text('UPDATE '+table+' SET ativo=false WHERE id=1'))
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        f=pool.submit(grade)
                        try:
                            self.assertTrue(attempted.wait(10))
                            if table=='treinamentos':wait_for_database_lock(engine,pids[0])
                            else:self.assertEqual(f.result(10),'ocupado')
                        finally:tx.commit()
                        self.assertIn(f.result(15),('ocupado','indisponivel'))
                self.denied('indisponivel',self.grade,s)
                after=self.state();self.assertEqual(after['resultados_quiz'],before['resultados_quiz'])
                with engine.begin() as conn:conn.execute(text('UPDATE '+table+' SET ativo=true WHERE id=1'))
            finally:event.remove(engine,'before_cursor_execute',observer)

    def test_correcao_primeiro_desativacao_espera_commit(self):
        base=None
        for table in ('colaboradores','treinamentos'):
            s=self.prepare(base);engine=None
            with self.app.app_context():engine=db.engine
            locked=Event();release=Event();attempted=Event();pids=[]
            def after(conn,cursor,sql,params,ctx,many):
                if 'FROM colaboradores' in sql and sql.rstrip().endswith('FOR SHARE NOWAIT'):
                    locked.set()
                    if not release.wait(15):raise TimeoutError('teste')
            def before(conn,cursor,sql,params,ctx,many):
                if sql.startswith('UPDATE '+table):
                    pids.append(conn.connection.driver_connection.info.backend_pid);attempted.set()
            def deactivate():
                with engine.begin() as conn:conn.execute(text('UPDATE '+table+' SET ativo=false WHERE id=1'))
            event.listen(engine,'after_cursor_execute',after);event.listen(engine,'before_cursor_execute',before)
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    f=pool.submit(lambda:self.grade(s,self.bad))
                    try:
                        self.assertTrue(locked.wait(10));g=pool.submit(deactivate)
                        self.assertTrue(attempted.wait(10));wait_for_database_lock(engine,pids[0]);self.assertFalse(g.done())
                    finally:release.set()
                    result=f.result(15);g.result(15);self.assertFalse(result.aprovado)
            finally:
                release.set();event.remove(engine,'after_cursor_execute',after);event.remove(engine,'before_cursor_execute',before)
            with engine.begin() as conn:conn.execute(text('UPDATE '+table+' SET ativo=true WHERE id=1'))
            base=result.resultado_id

    def test_ordem_inversa_convites_nao_causa_deadlock(self):
        s=self.prepare()
        with self.app.app_context():engine=db.engine
        # Outro escritor segura colaborador antes de tentar treinamento, como convites.
        with engine.connect() as conn:
            tx=conn.begin();conn.execute(text('SELECT id FROM colaboradores WHERE id=1 FOR UPDATE'))
            self.denied('ocupado',self.grade,s)
            conn.execute(text('SELECT id FROM treinamentos WHERE id=1 FOR UPDATE'))
            tx.commit()
        self.assertEqual(self.grade(s).numero_tentativa,1)

    def test_criterio_atual_no_envio_e_snapshot_preservado(self):
        s=self.prepare()
        with self.app.app_context(),db.engine.begin() as conn:
            conn.execute(text('UPDATE treinamentos SET nota_minima_percentual=50 WHERE id=1'))
        result=self.grade(s,self.bad);self.assertTrue(result.aprovado)
        before=self.state()
        with self.app.app_context(),db.engine.begin() as conn:
            conn.execute(text('UPDATE treinamentos SET nota_minima_percentual=100 WHERE id=1'))
        self.assertEqual(self.grade(s),result)
        self.assertEqual(self.state()['resultados_quiz'],before['resultados_quiz'])
        self.assertEqual(before['resultados_quiz'][0]['percentual_minimo_aplicado'],50)

    def test_alteracao_criterio_primeiro_correcao_espera_e_aplica_novo(self):
        s=self.prepare()
        with self.app.app_context():engine=db.engine
        attempted=Event();pids=[]
        def before(conn,cursor,sql,params,ctx,many):
            if 'FROM treinamentos' in sql and sql.rstrip().endswith('FOR SHARE'):
                pids.append(conn.connection.driver_connection.info.backend_pid);attempted.set()
        event.listen(engine,'before_cursor_execute',before)
        try:
            with engine.connect() as conn:
                tx=conn.begin();conn.execute(text('UPDATE treinamentos SET nota_minima_percentual=50 WHERE id=1'))
                with ThreadPoolExecutor(max_workers=1) as pool:
                    f=pool.submit(lambda:self.grade(s,self.bad))
                    try:self.assertTrue(attempted.wait(10));wait_for_database_lock(engine,pids[0])
                    finally:tx.commit()
                    self.assertTrue(f.result(15).aprovado)
        finally:event.remove(engine,'before_cursor_execute',before)

    def test_classificacao_pura_processada_nao_autorizada_aprovada_inconsistente(self):
        s=self.prepare();result=self.grade(s)
        with self.app.app_context():
            p=db.session.get(P,self.pid);t=db.session.get(T,p.treinamento_id);c=db.session.get(C,p.colaborador_id)
            rs=db.session.execute(select(R).where(R.participacao_id==p.id)).scalars().all()
            sub=db.session.get(S,s.submissao_id)
            mods=db.session.execute(select(ModuloTreinamento).where(ModuloTreinamento.treinamento_id==t.id)).scalars().all()
            qs=db.session.execute(select(Questao).where(Questao.treinamento_id==t.id)
                .options(selectinload(Questao.alternativas))).scalars().all()
            progress=service.calcular_progresso(mods,p.ultimo_modulo_id)
            def evaluate(**kwargs):return service.elegibilidade_quiz(p,t,c,rs,progress,qs,**kwargs)
            self.assertEqual(evaluate().tipo,'nao_autorizada')
            self.assertEqual(evaluate(autorizada=True).tipo,'aprovada')
            self.assertEqual(evaluate(autorizada=True,submissao=sub).tipo,'processada')
            p.aprovado_em=None
            self.assertEqual(evaluate(autorizada=True).tipo,'inconsistente')
            db.session.rollback()

    def test_query_count_preparacao_constante_com_uma_e_cinco_tentativas(self):
        counts=[]
        first=self.failure()
        with self.app.app_context():engine=db.engine
        for target in (1,5):
            while len(self.state()['resultados_quiz'])<target:
                latest=self.state()['resultados_quiz'][-1]['id']
                self.grade(self.prepare(latest),self.bad)
            queries=[]
            def observer(conn,cursor,sql,params,ctx,many):queries.append(sql)
            event.listen(engine,'before_cursor_execute',observer)
            try:s=self.prepare(self.state()['resultados_quiz'][-1]['id'])
            finally:event.remove(engine,'before_cursor_execute',observer)
            # state() acima usa cinco consultas constantes, incluídas nas duas medições.
            counts.append(len(queries))
        self.assertEqual(counts[0],counts[1])


class AliasSubmissaoPostgresTest(unittest.TestCase):
    """Grant de sessão real, revisão aprovada e ponte; nenhuma rede real."""
    def setUp(self):
        import test_simulacao_preparacao as anterior
        from app.simulacoes import educacao
        from flask import session as browser_session
        from app.models.simulacao import AlvoSimulacao
        anterior.PostgreSQLTest.setUp(self)
        q=db.session.execute(select(Questao).where(Questao.treinamento_id==1)).scalar_one()
        right=Alternativa(questao_id=q.id,texto='Correta',correta=True)
        db.session.add(right)
        p=P(token='alias-common-token',colaborador_id=1,treinamento_id=1,campanha_id=self.campaign.id)
        db.session.add(p);db.session.commit();self.pid=p.id;self.answers={f'questao_{q.id}':str(right.id)}
        self.preparar_base();self.aid=db.session.execute(select(AlvoSimulacao.id)).scalar_one()
        db.session.rollback();self.bid=educacao.criar_ponte(self.aid,self.admin,confirmar=True)
        with self.app.test_request_context():
            self.alias=educacao.acessar(self.bid)
            self.grant=dict(browser_session['simulacao_acesso'])
        db.session.rollback()
        with db.engine.begin() as conn:
            conn.execute(text('UPDATE participacoes_treinamento SET iniciado_em=now(), '
                'ultimo_modulo_id=(SELECT id FROM modulos_treinamento WHERE treinamento_id=1 LIMIT 1) WHERE id=:pid'),{'pid':self.pid})
        self.sub=self.call(service.preparar_submissao,self.alias)

    # Fixture anterior mantém guardas HTTP/SMTP e schema exclusivo.
    import test_simulacao_preparacao as _anterior
    cleanup_pg=_anterior.PostgreSQLTest.cleanup_pg
    preparar_base=_anterior.PreparacaoTest.preparar_base
    login=_anterior.PreparacaoTest.login
    post=_anterior.PreparacaoTest.post
    enable=_anterior.PreparacaoTest.enable

    def call(self,fn,*args,grant=True):
        from flask import session as browser_session
        with self.app.test_request_context():
            if grant:browser_session['simulacao_acesso']=dict(self.grant)
            return fn(*args)

    def grade(self):return self.call(service.corrigir_submissao,self.alias,self.sub.submissao_id,self.answers)

    def revoke(self):
        from app.simulacoes import educacao
        with self.app.app_context():educacao.revogar_ponte(self.aid,self.admin,True)

    def result_count(self):
        with self.app.app_context(),db.engine.connect() as conn:
            return conn.execute(text('SELECT count(*) FROM resultados_quiz')).scalar_one()

    def test_alias_valido_mesma_transacao_e_replay_token_comum(self):
        result=self.grade();self.assertEqual(result.numero_tentativa,1)
        with self.app.app_context():
            replay=service.corrigir_submissao('alias-common-token',self.sub.submissao_id,{})
        self.assertEqual(replay,result);self.assertEqual(self.result_count(),1)
        self.network.assert_not_called();self.guard.assert_not_called()

    def test_alias_ausente_expirado_revogado_inclusive_replay(self):
        with self.assertRaises(service.ErroQuiz):
            self.call(service.corrigir_submissao,self.alias,self.sub.submissao_id,self.answers,grant=False)
        old=dict(self.grant);self.grant['expira']=0
        with self.assertRaises(service.ErroQuiz):self.grade()
        self.grant=old;self.grade();self.revoke()
        with self.assertRaises(service.ErroQuiz):self.grade()
        self.assertEqual(self.result_count(),1)

    def test_revogacao_antes_correcao_nao_grava(self):
        self.revoke()
        with self.assertRaises(service.ErroQuiz):self.grade()
        self.assertEqual(self.result_count(),0)

    def test_correcao_primeiro_revogacao_espera_ate_commit(self):
        engine=db.engine;locked=Event();release=Event();attempted=Event();pids=[]
        def after(conn,cursor,sql,params,ctx,many):
            if 'FROM pontes_educativas_simulacao' in sql and sql.rstrip().endswith('FOR UPDATE'):
                if not locked.is_set():
                    locked.set()
                    if not release.wait(15):raise TimeoutError('teste')
        def before(conn,cursor,sql,params,ctx,many):
            if 'FROM execucoes_simulacao' in sql and sql.rstrip().endswith('FOR UPDATE') and locked.is_set():
                pids.append(conn.connection.driver_connection.info.backend_pid);attempted.set()
        event.listen(engine,'after_cursor_execute',after);event.listen(engine,'before_cursor_execute',before)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                f=pool.submit(self.grade)
                try:
                    self.assertTrue(locked.wait(10));g=pool.submit(self.revoke)
                    self.assertTrue(attempted.wait(10));wait_for_database_lock(engine,pids[0])
                    self.assertFalse(g.done())
                finally:release.set()
                self.assertTrue(f.result(15).aprovado);g.result(15)
        finally:
            release.set();event.remove(engine,'after_cursor_execute',after);event.remove(engine,'before_cursor_execute',before)
        with self.assertRaises(service.ErroQuiz):self.grade()
        self.assertEqual(self.result_count(),1)

    def test_revogacao_adquire_lock_primeiro_correcao_espera_e_recusa(self):
        engine=db.engine;locked=Event();release=Event();attempted=Event();pids=[]
        def after(conn,cursor,sql,params,ctx,many):
            if 'FROM execucoes_simulacao' in sql and sql.rstrip().endswith('FOR UPDATE') and not locked.is_set():
                locked.set()
                if not release.wait(15):raise TimeoutError('teste')
        def before(conn,cursor,sql,params,ctx,many):
            if 'FROM execucoes_simulacao' in sql and sql.rstrip().endswith('FOR UPDATE') and locked.is_set():
                pids.append(conn.connection.driver_connection.info.backend_pid);attempted.set()
        event.listen(engine,'after_cursor_execute',after);event.listen(engine,'before_cursor_execute',before)
        def grade():
            try:self.grade();return 'gravou'
            except service.ErroQuiz as e:return e.codigo
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                f=pool.submit(self.revoke)
                try:
                    self.assertTrue(locked.wait(10));g=pool.submit(grade)
                    self.assertTrue(attempted.wait(10));wait_for_database_lock(engine,pids[0])
                finally:release.set()
                f.result(15);self.assertEqual(g.result(15),'indisponivel')
        finally:
            release.set();event.remove(engine,'after_cursor_execute',after);event.remove(engine,'before_cursor_execute',before)
        self.assertEqual(self.result_count(),0)
