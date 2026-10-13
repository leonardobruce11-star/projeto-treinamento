"""19.2C1: persistência incremental, legado e migration PostgreSQL real."""
from sqlalchemy.orm import selectinload

import unittest
from metadata_revisoes_quiz import metadata_c3
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from sqlalchemy import Integer, event, inspect, text
from sqlalchemy.exc import IntegrityError, DataError
from alembic import command
from alembic.script import ScriptDirectory
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import app.simulacoes.preparacao
from app.extensions import db
from app.models import ResultadoQuiz, ParticipacaoTreinamento
import test_portal_treinamento_quiz as quiz_fixture
import test_aprovacao_migration as migration_fixture

HEAD = 'd192c1e4a909'
OLD_HEAD = 'c199b1e4a908'
OLD_UNIQUE = 'resultados_quiz_participacao_id_key'
NEW_UNIQUE = 'uq_resultados_quiz_participacao_tentativa'
NEW_CHECK = 'ck_resultados_quiz_numero_tentativa'
MISSING = object()


class PersistenciaTentativaTest(unittest.TestCase):
    postgres = False
    setUpClass = quiz_fixture.QuizTest.__dict__['setUpClass']
    tearDownClass = quiz_fixture.QuizTest.__dict__['tearDownClass']
    setUp = quiz_fixture.QuizTest.setUp
    request = quiz_fixture.QuizTest.request
    finish_modules = quiz_fixture.QuizTest.finish_modules

    def test_portal_ignora_numero_cliente_e_replay_preserva_tentativa_um(self):
        self.finish_modules()
        self.client.post(self.quiz_url+'/preparar', headers=self.headers)
        from app.models import SubmissaoQuiz
        with self.app.app_context(): identity=str(SubmissaoQuiz.query.one().id)
        data = {**self.answers, 'numero_tentativa': '2', 'submissao_id':identity}
        self.assertEqual(self.client.post(self.quiz_url, data=data, headers=self.headers).status_code, 303)
        with self.app.app_context():
            result = ResultadoQuiz.query.one()
            before = (result.id, result.numero_tentativa, result.percentual, result.aprovado,
                      result.concluido_em, result.participacao.aprovado_em)
            self.assertEqual(result.numero_tentativa, 1)
            self.assertEqual(db.session.execute(db.select(ParticipacaoTreinamento).options(selectinload(ParticipacaoTreinamento.resultados_quiz)).where(ParticipacaoTreinamento.id == self.pid)).scalar_one().resultados_quiz, [result])
        data['numero_tentativa'] = '999'
        self.assertEqual(self.client.post(self.quiz_url, data=data, headers=self.headers).status_code, 303)
        with self.app.app_context():
            result = ResultadoQuiz.query.one()
            self.assertEqual((result.id, result.numero_tentativa, result.percentual, result.aprovado,
                              result.concluido_em, result.participacao.aprovado_em), before)

    def test_default_orm_preservado_e_relacao_colecao(self):
        with self.app.app_context():
            result = ResultadoQuiz(participacao_id=self.pid, total_questoes=1,
                                   total_acertos=1, percentual=100)
            db.session.add(result); db.session.commit()
            self.assertEqual(result.numero_tentativa, 1)
            self.assertEqual(db.session.execute(db.select(ParticipacaoTreinamento).options(selectinload(ParticipacaoTreinamento.resultados_quiz)).where(ParticipacaoTreinamento.id == self.pid)).scalar_one().resultados_quiz, [result])
            self.assertTrue(inspect(ParticipacaoTreinamento).relationships.resultados_quiz.uselist)

    def test_unique_composta_impede_repetir_numero_no_modelo_atual(self):
        with self.app.app_context():
            db.session.add(ResultadoQuiz(participacao_id=self.pid, total_questoes=1,
                           total_acertos=0, percentual=0))
            db.session.commit()
            db.session.add(ResultadoQuiz(participacao_id=self.pid, numero_tentativa=1,
                           total_questoes=1, total_acertos=1, percentual=100))
            with self.assertRaises(IntegrityError): db.session.commit()
            db.session.rollback()
            self.assertEqual(ResultadoQuiz.query.one().numero_tentativa, 1)

    def test_conclusao_sem_quiz_nao_cria_resultado(self):
        quiz_fixture.QuizTest.without_quiz(self)
        self.finish_modules()
        self.assertEqual(self.client.post(self.quiz_url, headers=self.headers).status_code, 302)
        with self.app.app_context():
            self.assertEqual(ResultadoQuiz.query.count(), 0)
            self.assertEqual(db.session.execute(db.select(ParticipacaoTreinamento).options(selectinload(ParticipacaoTreinamento.resultados_quiz)).where(ParticipacaoTreinamento.id == self.pid)).scalar_one().resultados_quiz, [])


class PersistenciaTentativaPostgresTest(PersistenciaTentativaTest):
    postgres = True


class TentativaMigrationPostgresTest(unittest.TestCase):
    setUpClass = migration_fixture.AprovacaoMigrationPostgresTest.__dict__['setUpClass']
    tearDownClass = migration_fixture.AprovacaoMigrationPostgresTest.__dict__['tearDownClass']

    def setUp(self):
        with self.app.app_context(): db.session.remove()
        with self.admin.begin() as c:
            c.execute(text('DROP SCHEMA '+self.schema+' CASCADE'))
            c.execute(text('CREATE SCHEMA '+self.schema))
        with self.app.app_context():
            command.upgrade(self.config, OLD_HEAD)
            with db.engine.begin() as c:
                c.execute(text("INSERT INTO departamentos(id,nome,ativo) VALUES (1,'Sintético',true)"))
                c.execute(text("INSERT INTO colaboradores(id,nome,email,departamento_id,ativo,criado_em) VALUES (1,'Sintético','c1@example.invalid',1,true,'2020-01-01T01:02:03+00')"))
                c.execute(text("INSERT INTO treinamentos(id,titulo,ativo,criado_em,nota_minima_percentual) VALUES (1,'Sintético',true,'2020-01-01T01:02:03+00',70.01)"))
                for pid in range(1, 21):
                    c.execute(text("INSERT INTO participacoes_treinamento(id,token,colaborador_id,treinamento_id,iniciado_em,concluido_em,aprovado_em,criado_em) VALUES (:id,:token,1,1,:inicio,:fim,:aprovado,'2020-01-01T01:02:03+00')"),
                              dict(id=pid,token=f'c1-sintetico-{pid}',inicio='2020-03-04T05:06:07+00' if pid < 5 else None,
                                   fim='2020-03-05T06:07:08+00' if pid < 5 else None,
                                   aprovado='2020-03-05T06:07:08+00' if pid == 3 else None))
                # Legado não classificado inclusive fora das faixas atuais: preservar.
                c.execute(text("INSERT INTO resultados_quiz(participacao_id,total_questoes,total_acertos,percentual,concluido_em,percentual_minimo_aplicado,minimo_acertos_aplicado,aprovado) VALUES (1,0,-1,500,'2020-03-05T06:07:08+00',NULL,NULL,NULL),(2,6,4,66.66666666666667,'2020-03-05T06:07:09+00',NULL,NULL,NULL),(3,6,5,83.33333333333333,'2020-03-05T06:07:10+00',80,5,true),(4,6,4,66.67,'2020-03-05T06:07:11+00',80,5,false)"))
                self.original = [dict(r) for r in c.execute(text('SELECT * FROM resultados_quiz ORDER BY id')).mappings()]
                self.parents = [dict(r) for r in c.execute(text('SELECT * FROM participacoes_treinamento ORDER BY id')).mappings()]
                self.old_unique_identity = c.execute(text("SELECT oid,conindid FROM pg_constraint WHERE conrelid='resultados_quiz'::regclass AND conname=:name"), {'name':OLD_UNIQUE}).one()

    def upgrade(self):
        self.assertEqual(ScriptDirectory.from_config(self.config).get_heads(), ['f192d2e4a911'])
        command.upgrade(self.config, HEAD)

    def insert(self, connection, pid, number=MISSING):
        columns = 'participacao_id,total_questoes,total_acertos,percentual,concluido_em'
        values = ':pid,1,1,100,\'2021-01-02T03:04:05+00\''
        params = {'pid':pid}
        if number is not MISSING:
            columns += ',numero_tentativa'; values += ',:number'; params['number'] = number
        return connection.execute(text(f'INSERT INTO resultados_quiz({columns}) VALUES ({values}) RETURNING id'), params).scalar_one()

    def assert_history(self):
        with db.engine.connect() as c:
            names = ','.join(self.original[0])
            actual = [dict(r) for r in c.execute(text(f'SELECT {names} FROM resultados_quiz WHERE participacao_id <= 4 ORDER BY id')).mappings()]
            self.assertEqual(actual, self.original)
            self.assertEqual([dict(r) for r in c.execute(text('SELECT * FROM participacoes_treinamento ORDER BY id')).mappings()], self.parents)
            identity = c.execute(text("SELECT oid,conindid FROM pg_constraint WHERE conrelid='resultados_quiz'::regclass AND conname=:name"),{'name':OLD_UNIQUE}).one()
            self.assertEqual(identity, self.old_unique_identity)

    def test_upgrade_downgrade_reupgrade_preserva_historico_e_default_sql(self):
        with self.app.app_context():
            self.upgrade(); self.assert_history()
            with db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT numero_tentativa FROM resultados_quiz ORDER BY id')).scalars().all(), [1]*4)
                column = next(x for x in inspect(c).get_columns('resultados_quiz') if x['name']=='numero_tentativa')
                self.assertIsInstance(column['type'], Integer)
                self.assertFalse(column['nullable']); self.assertEqual(column['default'], '1')
                self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), HEAD)
            with db.engine.begin() as c: rid = self.insert(c, 5)
            with db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT numero_tentativa FROM resultados_quiz WHERE id=:id'),{'id':rid}).scalar_one(), 1)
            command.downgrade(self.config, OLD_HEAD); self.assert_history()
            with db.engine.connect() as c:
                self.assertNotIn('numero_tentativa', [x['name'] for x in inspect(c).get_columns('resultados_quiz')])
                self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), OLD_HEAD)
                self.assertNotIn(NEW_UNIQUE,[x['name'] for x in inspect(c).get_unique_constraints('resultados_quiz')])
                self.assertNotIn(NEW_CHECK,[x['name'] for x in inspect(c).get_check_constraints('resultados_quiz')])
            with db.engine.begin() as c: self.insert(c, 6)
            self.upgrade(); self.assert_history()
            with db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT numero_tentativa FROM resultados_quiz ORDER BY id')).scalars().all(), [1]*6)

    def test_check_not_null_e_limites_integer(self):
        with self.app.app_context():
            self.upgrade()
            for pid,number in enumerate((0,-1,None,2147483648),5):
                with self.subTest(number=number), self.assertRaises((IntegrityError,DataError)):
                    with db.engine.begin() as c:self.insert(c,pid,number)
            with db.engine.begin() as c:self.insert(c,10,2147483647)
            with db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT numero_tentativa FROM resultados_quiz WHERE participacao_id=10')).scalar_one(),2147483647)
            self.assert_history()

    def test_unique_composta_operacional_e_unique_antiga_mantida(self):
        with self.app.app_context():
            self.upgrade()
            with db.engine.connect() as c:
                unique={x['name']:x['column_names'] for x in inspect(c).get_unique_constraints('resultados_quiz')}
                self.assertEqual(unique[OLD_UNIQUE], ['participacao_id'])
                self.assertEqual(unique[NEW_UNIQUE], ['participacao_id','numero_tentativa'])
                self.assertIn(NEW_CHECK,[x['name'] for x in inspect(c).get_check_constraints('resultados_quiz')])
            with db.engine.begin() as c:
                row=c.execute(text("INSERT INTO resultados_quiz(participacao_id,numero_tentativa,total_questoes,total_acertos,percentual,concluido_em) VALUES (1,1,1,1,100,CURRENT_TIMESTAMP) ON CONFLICT ON CONSTRAINT uq_resultados_quiz_participacao_tentativa DO NOTHING RETURNING id")).first()
                self.assertIsNone(row)
            for number in (1,2):
                with self.subTest(number=number):
                    with self.assertRaises(IntegrityError) as caught:
                        with db.engine.begin() as c:self.insert(c,1,number)
                    if number == 2:
                        self.assertEqual(caught.exception.orig.diag.constraint_name,OLD_UNIQUE)
                    else:
                        self.assertIn(caught.exception.orig.diag.constraint_name,(OLD_UNIQUE,NEW_UNIQUE))
            self.assert_history()

    def test_metadados_equivalentes_incluindo_server_default(self):
        with self.app.app_context():
            self.upgrade()
            with db.engine.connect() as c:
                context=MigrationContext.configure(c,opts={'compare_server_default':True,
                    'include_object':lambda obj,name,kind,reflected,compare_to:
                        name=='resultados_quiz' if kind=='table' else True})
                # C1 continua tendo a UNIQUE individual; compare à projeção C3, sem adições D2.
                diff = compare_metadata(context,metadata_c3())
                self.assertEqual(len(diff),1)
                self.assertEqual(diff[0][0],'remove_constraint')
                self.assertEqual(diff[0][1].name,OLD_UNIQUE)
            self.assert_history()

    def test_downgrade_bloqueia_numero_diferente_de_um_sem_perda(self):
        with self.app.app_context():
            self.upgrade()
            with db.engine.begin() as c:c.execute(text('UPDATE resultados_quiz SET numero_tentativa=2 WHERE participacao_id=1'))
            with db.engine.connect() as c:before=[dict(r) for r in c.execute(text('SELECT * FROM resultados_quiz ORDER BY id')).mappings()]
            with self.assertRaisesRegex(RuntimeError,'Downgrade bloqueado'):command.downgrade(self.config,OLD_HEAD)
            with db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),HEAD)
                self.assertEqual([dict(r) for r in c.execute(text('SELECT * FROM resultados_quiz ORDER BY id')).mappings()],before)
                self.assertIn(NEW_UNIQUE,[x['name'] for x in inspect(c).get_unique_constraints('resultados_quiz')])
                self.assertIn(NEW_CHECK,[x['name'] for x in inspect(c).get_check_constraints('resultados_quiz')])
            self.assert_history()

    def test_escrita_concorrente_nao_escapa_da_verificacao_do_downgrade(self):
        with self.app.app_context():self.upgrade();engine=db.engine
        attempted=Event()
        def observe(conn,cursor,sql,params,context,many):
            if sql.startswith('LOCK TABLE participacoes_treinamento, resultados_quiz'):attempted.set()
        def down():
            with self.app.app_context():
                try:command.downgrade(self.config,OLD_HEAD);return 'removed'
                except RuntimeError as error:return str(error)
        event.listen(engine,'before_cursor_execute',observe)
        conn=engine.connect();txn=conn.begin()
        conn.execute(text('UPDATE resultados_quiz SET numero_tentativa=2 WHERE participacao_id=1'))
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(down)
                self.assertTrue(attempted.wait(15));self.assertFalse(future.done())
                txn.commit();self.assertIn('Downgrade bloqueado',future.result(20))
        finally:
            if txn.is_active:txn.rollback()
            conn.close();event.remove(engine,'before_cursor_execute',observe)
        with self.app.app_context():
            self.assert_history()
            with db.engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT numero_tentativa FROM resultados_quiz WHERE participacao_id=1')).scalar_one(),2)
                self.assertEqual(c.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),HEAD)
