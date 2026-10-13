"""C3: migration e locks reais em PostgreSQL descartável, sem configuração DEV."""
import time
import unittest
from metadata_revisoes_quiz import metadata_c3
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from alembic import command
from alembic.script import ScriptDirectory
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import configure_mappers, selectinload
from app.extensions import db
from app.models import ParticipacaoTreinamento as P, ResultadoQuiz as R
from app.utils.resultados_quiz import resultado_representativo, primeira_aprovacao
from app.utils.painel_colaboradores import consulta_participacoes_painel, status_participacao
import test_etapa192c1 as c1
import test_portal_treinamento_quiz as quiz

HEAD = 'e192c3e4a910'
PREVIOUS = 'd192c1e4a909'
OLD_UNIQUE = c1.OLD_UNIQUE


def wait_for_database_lock(admin, pid):
    """Confirma bloqueio no servidor, sem inferir apenas do estado da thread."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with admin.connect() as conn:
            wait = conn.execute(text('SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid'), {'pid':pid}).scalar_one_or_none()
        if wait == 'Lock':
            return
        time.sleep(.01)
    raise AssertionError('A conexão concorrente não bloqueou em lock PostgreSQL')


class CardinalidadeMigrationPostgresTest(unittest.TestCase):
    setUpClass = c1.TentativaMigrationPostgresTest.__dict__['setUpClass']
    tearDownClass = c1.TentativaMigrationPostgresTest.__dict__['tearDownClass']
    insert = c1.TentativaMigrationPostgresTest.insert

    def setUp(self):
        # As migrations não instanciam objetos ORM. Configure o backref antes
        # de usar P.resultados_quiz, inclusive executando apenas este arquivo.
        configure_mappers()
        c1.TentativaMigrationPostgresTest.setUp(self)
        with self.app.app_context():
            command.upgrade(self.config, PREVIOUS)
            with db.engine.connect() as conn:
                self.before = self.snapshot(conn)
                self.constraints = self.catalog(conn)
                self.columns = inspect(conn).get_columns('resultados_quiz')

    def snapshot(self, conn):
        return {table:[dict(r) for r in conn.execute(text('SELECT * FROM '+table+' ORDER BY id')).mappings()]
                for table in ('resultados_quiz','participacoes_treinamento','treinamentos')}

    def catalog(self, conn):
        return {r.conname:tuple(r) for r in conn.execute(text(
            "SELECT conname,oid,conindid,pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid='resultados_quiz'::regclass ORDER BY conname"))}

    def upgrade(self):
        self.assertEqual(ScriptDirectory.from_config(self.config).get_heads(), ['f192d2e4a911'])
        command.upgrade(self.config, HEAD)

    def test_upgrade_remove_somente_unique_e_preserva_colunas_historico(self):
        with self.app.app_context():
            self.upgrade()
            with db.engine.connect() as conn:
                self.assertEqual(self.snapshot(conn), self.before)
                self.assertEqual(self.catalog(conn), {k:v for k,v in self.constraints.items() if k!=OLD_UNIQUE})
                columns=inspect(conn).get_columns('resultados_quiz')
                self.assertEqual([(c['name'],str(c['type']),c['nullable'],c['default']) for c in columns],
                                 [(c['name'],str(c['type']),c['nullable'],c['default']) for c in self.columns])
                self.assertEqual(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(), HEAD)

    def test_metadados_alembic_equivalentes_com_default(self):
        with self.app.app_context():
            self.upgrade()
            with db.engine.connect() as conn:
                ctx=MigrationContext.configure(conn,opts={'compare_server_default':True})
                self.assertEqual(compare_metadata(ctx,metadata_c3()), [])
            self.assertTrue(inspect(P).relationships.resultados_quiz.uselist)
            self.assertEqual(inspect(P).relationships.resultados_quiz.lazy, 'raise')
            self.assertNotIn('resultado_quiz',inspect(P).relationships)

    def test_persistencia_uma_duas_e_cinco_tentativas_historicas(self):
        with self.app.app_context():
            self.upgrade()
            with db.engine.begin() as conn:
                self.insert(conn,1,2)
                for n in range(2,6):
                    self.insert(conn,2,n)
                self.insert(conn,5)  # Default 1 em SQL direto.
            with db.engine.connect() as conn:
                counts=dict(conn.execute(text('SELECT participacao_id,count(*) FROM resultados_quiz GROUP BY participacao_id')).all())
                self.assertEqual((counts[1],counts[2],counts[3],counts[5]),(2,5,1,1))
                self.assertEqual(conn.execute(text('SELECT numero_tentativa FROM resultados_quiz WHERE participacao_id=5')).scalar_one(),1)
                self.assertEqual(conn.execute(text('SELECT count(*) FROM resultados_quiz WHERE aprovado IS NULL')).scalar_one(),8)

    def test_rejeita_duplicado_zero_negativo_null_e_fk_invalida(self):
        with self.app.app_context():
            self.upgrade()
            for pid,number,constraint in ((1,1,c1.NEW_UNIQUE),(5,0,c1.NEW_CHECK),(5,-1,c1.NEW_CHECK),(999,1,'resultados_quiz_participacao_id_fkey')):
                with self.subTest(pid=pid,number=number), self.assertRaises(IntegrityError) as caught:
                    with db.engine.begin() as conn:
                        self.insert(conn,pid,number)
                self.assertEqual(caught.exception.orig.diag.constraint_name,constraint)
            with self.assertRaises(IntegrityError):
                with db.engine.begin() as conn:
                    self.insert(conn,5,None)
            with db.engine.connect() as conn:
                self.assertEqual(self.snapshot(conn),self.before)

    def test_upgrade_downgrade_reupgrade_sem_perda(self):
        with self.app.app_context():
            self.upgrade()
            command.downgrade(self.config, PREVIOUS)
            with db.engine.connect() as conn:
                self.assertEqual(self.snapshot(conn), self.before)
                unique={u['name']:u['column_names'] for u in inspect(conn).get_unique_constraints('resultados_quiz')}
                self.assertEqual(unique[OLD_UNIQUE],['participacao_id'])
                self.assertEqual(unique[c1.NEW_UNIQUE],['participacao_id','numero_tentativa'])
                self.assertEqual(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),PREVIOUS)
            self.upgrade()
            with db.engine.connect() as conn:
                self.assertEqual(self.snapshot(conn),self.before)
                self.assertNotIn(OLD_UNIQUE,self.catalog(conn))

    def test_downgrade_permite_linha_unica_com_numero_diferente_de_um(self):
        with self.app.app_context():
            self.upgrade()
            with db.engine.begin() as conn:
                conn.execute(text('UPDATE resultados_quiz SET numero_tentativa=7 WHERE participacao_id=1'))
            with db.engine.connect() as conn:
                before=self.snapshot(conn)
            command.downgrade(self.config,PREVIOUS)
            with db.engine.connect() as conn:
                self.assertEqual(self.snapshot(conn),before)
                self.assertIn(OLD_UNIQUE,self.catalog(conn))
            # A proteção independente da C1 continua impedindo perda da numeração.
            with self.assertRaisesRegex(RuntimeError,'Downgrade bloqueado'):
                command.downgrade(self.config,c1.OLD_HEAD)
            self.upgrade()
            with db.engine.connect() as conn:
                self.assertEqual(self.snapshot(conn),before)

    def test_downgrade_bloqueado_preserva_linhas_constraints_e_head(self):
        with self.app.app_context():
            self.upgrade()
            with db.engine.begin() as conn:
                self.insert(conn,1,2)
                self.insert(conn,1,3)
            with db.engine.connect() as conn:
                before=self.snapshot(conn); constraints=self.catalog(conn)
            with self.assertRaisesRegex(RuntimeError,'19.2C3 bloqueado.*mais de um'):
                command.downgrade(self.config,PREVIOUS)
            with db.engine.connect() as conn:
                self.assertEqual(self.snapshot(conn),before)
                self.assertEqual(self.catalog(conn),constraints)
                self.assertEqual(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),HEAD)

    def test_escritor_antes_downgrade_espera_e_detecta_multiplicidade(self):
        with self.app.app_context():
            self.upgrade(); engine=db.engine
        attempt=Event(); backend=[]
        def observer(conn,cursor,sql,params,ctx,many):
            if sql.startswith('LOCK TABLE participacoes_treinamento, resultados_quiz'):
                backend.append(conn.connection.driver_connection.info.backend_pid); attempt.set()
        def down():
            with self.app.app_context():
                try:
                    command.downgrade(self.config,PREVIOUS); return 'restaurada'
                except RuntimeError as exc:
                    return str(exc)
        event.listen(engine,'before_cursor_execute',observer)
        conn=engine.connect(); tx=conn.begin()
        try:
            conn.execute(text('SELECT id FROM participacoes_treinamento WHERE id=1 FOR UPDATE'))
            self.insert(conn,1,2)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(down)
                try:
                    self.assertTrue(attempt.wait(15))
                    wait_for_database_lock(self.admin,backend[0])
                    self.assertFalse(future.done())
                finally:
                    tx.commit()
                self.assertIn('19.2C3 bloqueado',future.result(20))
        finally:
            if tx.is_active: tx.rollback()
            conn.close(); event.remove(engine,'before_cursor_execute',observer)
        with engine.connect() as conn:
            self.assertEqual(conn.execute(text('SELECT count(*) FROM resultados_quiz WHERE participacao_id=1')).scalar_one(),2)
            self.assertNotIn(OLD_UNIQUE,self.catalog(conn))

    def test_downgrade_antes_escritor_mantem_lock_ate_restaurar_unique(self):
        with self.app.app_context():
            self.upgrade(); engine=db.engine
        locked=Event(); release=Event(); attempted=Event(); backend=[]
        def after(conn,cursor,sql,params,ctx,many):
            if sql.startswith('LOCK TABLE participacoes_treinamento, resultados_quiz'):
                locked.set()
                if not release.wait(15): raise TimeoutError('Liberação do downgrade')
        def before(conn,cursor,sql,params,ctx,many):
            if sql.startswith('SELECT id FROM participacoes_treinamento WHERE id=1 FOR UPDATE'):
                backend.append(conn.connection.driver_connection.info.backend_pid); attempted.set()
        def down():
            with self.app.app_context():
                command.downgrade(self.config,PREVIOUS)
        def writer():
            try:
                with engine.begin() as conn:
                    conn.execute(text('SELECT id FROM participacoes_treinamento WHERE id=1 FOR UPDATE'))
                    self.insert(conn,1,2)
                return 'inserida'
            except IntegrityError as exc:
                return exc.orig.diag.constraint_name
        event.listen(engine,'after_cursor_execute',after)
        event.listen(engine,'before_cursor_execute',before)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                f=pool.submit(down)
                try:
                    self.assertTrue(locked.wait(15))
                    g=pool.submit(writer)
                    self.assertTrue(attempted.wait(15))
                    wait_for_database_lock(self.admin,backend[0])
                    self.assertFalse(g.done())
                finally:
                    release.set()
                f.result(20)
                self.assertEqual(g.result(20),OLD_UNIQUE)
        finally:
            release.set()
            event.remove(engine,'after_cursor_execute',after)
            event.remove(engine,'before_cursor_execute',before)
        with engine.connect() as conn:
            self.assertEqual(self.snapshot(conn),self.before)
            self.assertIn(OLD_UNIQUE,self.catalog(conn))
            self.assertEqual(conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one(),PREVIOUS)

    def test_consumidores_python_sql_em_schema_migrado(self):
        with self.app.app_context():
            self.upgrade()
            with db.engine.begin() as conn:
                # Histórico + reprovação + aprovação + aprovação maior + histórico.
                conn.execute(text("INSERT INTO resultados_quiz(participacao_id,numero_tentativa,total_questoes,total_acertos,percentual,percentual_minimo_aplicado,minimo_acertos_aplicado,aprovado,concluido_em) VALUES (1,2,6,4,66.67,80,5,false,'2021-01-01+00'),(1,3,6,5,83.33,80,5,true,'2021-01-02+00'),(1,4,6,6,100,80,5,true,'2021-01-03+00'),(1,5,6,6,100,NULL,NULL,NULL,'2021-01-04+00')"))
            ps=db.session.execute(select(P).options(selectinload(P.resultados_quiz)).order_by(P.id)).scalars().all()
            rows={r['participacao_id']:r for r in db.session.execute(consulta_participacoes_painel([1])).mappings()}
            self.assertEqual(len(rows),20)
            for p in ps:
                self.assertEqual(status_participacao(p,p.resultados_quiz),rows[p.id]['status'])
            self.assertEqual(resultado_representativo(ps[0].resultados_quiz).numero_tentativa,3)
            self.assertEqual(primeira_aprovacao(ps[0].resultados_quiz).numero_tentativa,3)
            self.assertEqual(rows[1]['total_tentativas'],5)


class PortalCardinalidadePostgresTest(unittest.TestCase):
    postgres=True
    setUpClass=quiz.QuizTest.__dict__['setUpClass']
    tearDownClass=quiz.QuizTest.__dict__['tearDownClass']
    setUp=quiz.QuizTest.setUp
    request=quiz.QuizTest.request
    def finish_modules(self):
        quiz.QuizTest.finish_modules(self)
        self.client.post(self.quiz_url+'/preparar',headers=self.headers)
        from app.models import SubmissaoQuiz
        with self.app.app_context():self.identity=str(SubmissaoQuiz.query.one().id)
    concurrent_posts=quiz.QuizPostgresTest.concurrent_posts

    def snapshot(self):
        with self.app.app_context():
            return ([dict(r) for r in db.session.execute(select(*R.__table__.c)).mappings()],
                    [dict(r) for r in db.session.execute(select(*P.__table__.c)).mappings()])

    def test_dois_posts_simultaneos_uma_tentativa_um_sem_unique_antiga(self):
        self.finish_modules()
        with self.app.app_context():
            unique=inspect(db.engine).get_unique_constraints('resultados_quiz')
            self.assertEqual({u['name'] for u in unique},{c1.NEW_UNIQUE,'uq_resultados_quiz_participacao_id'})
        self.assertEqual(sorted(self.concurrent_posts([dict(self.answers,numero_tentativa='2'),dict(self.answers,numero_tentativa='999')])),[303,303])
        before=self.snapshot()
        self.assertEqual(len(before[0]),1)
        self.assertEqual(before[0][0]['numero_tentativa'],1)
        participation=next(p for p in before[1] if p['id']==self.pid)
        self.assertIsNotNone(participation['concluido_em'])
        self.assertIsNotNone(participation['aprovado_em'])
        posted=self.client.post(self.quiz_url,data=dict(self.answers,numero_tentativa='3',submissao_id=self.identity),headers=self.headers)
        self.assertEqual(posted.status_code,303)
        for response in (self.client.get(self.quiz_url),self.client.get(posted.location)):
            self.assertEqual(response.status_code,200)
            self.assertNotIn('Refazer quiz',response.get_data(as_text=True))
        self.assertEqual(self.snapshot(),before)

    def test_get_e_numero_cliente_nao_criam_tentativa(self):
        self.finish_modules(); before=self.snapshot()
        self.assertEqual(self.client.get(self.quiz_url+'?numero_tentativa=2').status_code,200)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.client.post(self.quiz_url,data=dict(self.answers,numero_tentativa='88',submissao_id=self.identity),headers=self.headers).status_code,303)
        self.assertEqual(self.snapshot()[0][0]['numero_tentativa'],1)

    def test_segundo_post_espera_lock_e_rele_conclusao_com_share_treinamento(self):
        self.finish_modules()
        with self.app.app_context():
            engine=db.engine
        locked=Event(); release=Event(); attempted=Event(); backend=[]; training_locks=[]
        def participation_lock(sql):
            return 'FROM participacoes_treinamento' in sql and sql.rstrip().endswith('FOR UPDATE')
        def before(conn,cursor,sql,params,ctx,many):
            if participation_lock(sql) and locked.is_set():
                backend.append(conn.connection.driver_connection.info.backend_pid); attempted.set()
        def after(conn,cursor,sql,params,ctx,many):
            if participation_lock(sql) and not locked.is_set():
                locked.set()
                if not release.wait(15): raise TimeoutError('Liberação do primeiro POST')
            if 'FROM treinamentos' in sql and sql.rstrip().endswith('FOR SHARE'):
                training_locks.append(conn.connection.driver_connection.info.backend_pid)
        def post():
            client=self.app.test_client()
            headers={'X-CSRFToken':client.get('/_csrf').json['token']}
            return client.post(self.quiz_url,data=dict(self.answers,numero_tentativa='2',submissao_id=self.identity),headers=headers).status_code
        event.listen(engine,'before_cursor_execute',before)
        event.listen(engine,'after_cursor_execute',after)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(post)
                try:
                    self.assertTrue(locked.wait(15))
                    second=pool.submit(post)
                    self.assertTrue(attempted.wait(15))
                    # Mesmo banco temporário, conexão administrativa independente.
                    admin=create_engine(self.app.config['SQLALCHEMY_DATABASE_URI'])
                    try: wait_for_database_lock(admin,backend[0])
                    finally: admin.dispose()
                    self.assertFalse(second.done())
                finally:
                    release.set()
                self.assertEqual((first.result(20),second.result(20)),(303,303))
        finally:
            release.set()
            event.remove(engine,'before_cursor_execute',before)
            event.remove(engine,'after_cursor_execute',after)
        self.assertEqual(len(set(training_locks)),2)
        self.assertEqual(len(self.snapshot()[0]),1)
        self.assertEqual(self.snapshot()[0][0]['numero_tentativa'],1)
