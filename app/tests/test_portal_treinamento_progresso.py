"""Progressão isolada; PostgreSQL opcional via ONE_PROGRESSO_TEST_DATABASE_URL."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4
from pathlib import Path
from flask import Flask, jsonify
from flask_wtf.csrf import generate_csrf
from sqlalchemy import create_engine, event, update
from sqlalchemy.schema import CreateSchema, DropSchema
import app.models
from app.extensions import db, csrf
from app.portal_treinamento.routes import portal_treinamento_bp
from app.models.departamento import Departamento
from app.models.colaborador import Colaborador
from app.models.treinamento import Treinamento
from app.models.modulo_treinamento import ModuloTreinamento
from app.models.participacao_treinamento import ParticipacaoTreinamento
from app.models.tempo_treinamento import TempoTreinamento
from app.models.resultado_quiz import ResultadoQuiz
from app.utils.tempo import formatar_tempo_ativo
from app.utils.progresso_treinamento import calcular_progresso

class ProgressoTest(unittest.TestCase):
    postgres = False
    @classmethod
    def setUpClass(cls):
        config = {}
        url = 'sqlite://'
        if cls.postgres:
            url = os.environ.get('ONE_PROGRESSO_TEST_DATABASE_URL')
            if not url:
                raise unittest.SkipTest('PostgreSQL de testes não configurado')
            cls.admin = create_engine(url)
            cls.schema = 'progresso_test_' + uuid4().hex
            with cls.admin.begin() as conn:
                conn.execute(CreateSchema(cls.schema))
            config = {'connect_args': {'options': '-csearch_path=' + cls.schema}}
        cls.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'app/templates'))
        cls.app.config.update(TESTING=True, SECRET_KEY='isolated', SQLALCHEMY_DATABASE_URI=url,
                              SQLALCHEMY_ENGINE_OPTIONS=config, WTF_CSRF_ENABLED=True)
        db.init_app(cls.app)
        csrf.init_app(cls.app)
        cls.app.register_blueprint(portal_treinamento_bp)
        cls.app.jinja_env.filters["tempo_ativo"] = formatar_tempo_ativo
        @cls.app.get('/_csrf')
        def token():
            return jsonify(token=generate_csrf())
    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        if cls.postgres:
            with cls.admin.begin() as conn:
                conn.execute(DropSchema(cls.schema, cascade=True))
            cls.admin.dispose()
    def setUp(self):
        with self.app.app_context():
            db.drop_all(); db.create_all()
            d = Departamento(nome='Teste')
            db.session.add(d); db.session.flush()
            c = Colaborador(nome='Teste', email='teste@example.invalid', departamento_id=d.id)
            t = Treinamento(titulo='Principal', ativo=True)
            other = Treinamento(titulo='Outro', ativo=True)
            empty = Treinamento(titulo='Vazio', ativo=True)
            db.session.add_all([c,t,other,empty]); db.session.flush()
            mods = [ModuloTreinamento(treinamento_id=t.id, titulo=str(i), conteudo='Texto', ordem=i*10) for i in (1,2,3)]
            foreign = ModuloTreinamento(treinamento_id=other.id, titulo='Único', conteudo='Texto', ordem=1)
            db.session.add_all(mods+[foreign]); db.session.flush()
            p = ParticipacaoTreinamento(token='principal', colaborador_id=c.id, treinamento_id=t.id)
            db.session.add_all([p, ParticipacaoTreinamento(token='unico', colaborador_id=c.id, treinamento_id=other.id), ParticipacaoTreinamento(token='vazio', colaborador_id=c.id, treinamento_id=empty.id)])
            db.session.flush()
            db.session.add(TempoTreinamento(participacao_id=p.id, modulo_id=mods[0].id, segundos_ativos=7))
            db.session.commit()
            self.pid=p.id; self.ids=[m.id for m in mods]; self.foreign=foreign.id
        self.client=self.app.test_client()
        self.headers={'X-CSRFToken':self.client.get('/_csrf').json['token']}
    def request(self, index=0, method='POST', token='principal', mid=None):
        url=f'/aprender/t/{token}/modulo/{mid or self.ids[index]}'
        if method=='POST': url+='/avancar'
        return self.client.open(url, method=method, headers=self.headers)
    def state(self):
        with self.app.app_context():
            p=db.session.get(ParticipacaoTreinamento,self.pid)
            return (p.ultimo_modulo_id,p.iniciado_em,p.concluido_em,[(r.modulo_id,r.segundos_ativos) for r in TempoTreinamento.query.all()], ResultadoQuiz.query.count())
    def test_first_get(self):
        before=self.state(); self.assertEqual(self.request(method='GET').status_code,200); self.assertEqual(self.state(),before)
    def test_future_get(self):
        before=self.state(); self.assertEqual(self.request(1,'GET').status_code,302); self.assertEqual(self.state(),before)
    def test_future_post(self):
        before=self.state(); self.assertEqual(self.request(1).status_code,403); self.assertEqual(self.state(),before)
    def test_first_advance(self):
        self.assertEqual(self.request().status_code,302); self.assertEqual(self.state()[0],self.ids[0]); self.assertIsNotNone(self.state()[1])
    def test_second_advance(self):
        self.request(); started=self.state()[1]; self.request(1); self.assertEqual(self.state()[0],self.ids[1]); self.assertEqual(self.state()[1],started)
    def test_repeat(self):
        self.request(); before=self.state(); self.assertEqual(self.request().status_code,302); self.assertEqual(self.state(),before)
    def test_delayed(self):
        self.request(); self.request(1); before=self.state(); r=self.request(); self.assertEqual(self.state(),before); self.assertTrue(r.location.endswith(f'/modulo/{self.ids[2]}'))
    def test_direct_last(self):
        before=self.state(); self.assertEqual(self.request(2).status_code,403); self.assertEqual(self.state(),before)
    def test_invalid_token(self):
        before=self.state()
        for method in ('GET','POST'): self.assertEqual(self.request(method=method,token='invalido').status_code,404)
        self.assertEqual(self.state(),before)
    def test_foreign_module(self):
        before=self.state()
        for method in ('GET','POST'): self.assertEqual(self.request(method=method,mid=self.foreign).status_code,404)
        self.assertEqual(self.state(),before)
    def test_single_module(self):
        self.assertEqual(self.request(method='GET',token='unico',mid=self.foreign).status_code,200)
        r=self.request(token='unico',mid=self.foreign); self.assertTrue(r.location.endswith('/quiz'))
        with self.app.app_context():
            p=ParticipacaoTreinamento.query.filter_by(token='unico').one(); started=p.iniciado_em
            self.assertEqual(p.ultimo_modulo_id,self.foreign); self.assertIsNone(p.concluido_em)
        self.request(token='unico',mid=self.foreign)
        with self.app.app_context(): self.assertEqual(ParticipacaoTreinamento.query.filter_by(token='unico').one().iniciado_em,started)
    def test_empty(self):
        self.assertEqual(self.request(token='vazio').status_code,404)
        self.assertEqual(self.client.post('/aprender/t/vazio/iniciar',headers=self.headers).status_code,200)
        with self.app.app_context(): self.assertIsNone(ParticipacaoTreinamento.query.filter_by(token='vazio').one().iniciado_em)
        self.assertFalse(calcular_progresso([],None).todos_modulos_concluidos)
    def test_inconsistent_cursor(self):
        with self.app.app_context():
            p=db.session.get(ParticipacaoTreinamento,self.pid); p.ultimo_modulo_id=self.foreign; db.session.commit()
        before=self.state()
        for method in ('GET','POST'): self.assertEqual(self.request(method=method).status_code,409)
        self.assertEqual(self.state(),before)
    def test_duplicate_orders(self):
        with self.app.app_context():
            for mid in self.ids: db.session.get(ModuloTreinamento,mid).ordem=1
            db.session.commit()
            modules=list(reversed(ModuloTreinamento.query.filter(ModuloTreinamento.id.in_(self.ids)).all()))
            self.assertEqual([m.id for m in calcular_progresso(modules,None).modulos],sorted(self.ids))
        for index in range(3): self.assertEqual(self.request(index).status_code,302); self.assertEqual(self.state()[0],self.ids[index])
    def test_review(self):
        self.request(); self.request(1); before=self.state(); self.assertEqual(self.request(method='GET').status_code,200); self.assertEqual(self.state(),before)
    def test_csrf(self):
        before=self.state(); self.assertEqual(self.client.post(f'/aprender/t/principal/modulo/{self.ids[0]}/avancar').status_code,400); self.assertEqual(self.state(),before)
    def test_all_completed_state(self):
        for index in range(3): self.request(index)
        before=self.state(); self.request(); self.assertEqual(self.state(),before)
        with self.app.app_context():
            s=calcular_progresso(ModuloTreinamento.query.filter(ModuloTreinamento.id.in_(self.ids)).all(),self.ids[2])
            self.assertTrue(s.todos_modulos_concluidos); self.assertIsNone(s.proximo_modulo_pendente); self.assertEqual(len(s.modulos_concluidos),3)
        self.assertIsNone(self.state()[2])

class ProgressoPostgresTest(ProgressoTest):
    postgres=True
    def concurrent(self,index):
        barrier=Barrier(6)
        def worker(_):
            client=self.app.test_client(); headers={'X-CSRFToken':client.get('/_csrf').json['token']}
            barrier.wait(timeout=10)
            return client.post(f'/aprender/t/principal/modulo/{self.ids[index]}/avancar',headers=headers).status_code
        with ThreadPoolExecutor(max_workers=6) as pool: return list(pool.map(worker,range(6)))
    def test_concurrent_repeat(self):
        self.assertEqual(self.concurrent(0),[302]*6); self.assertEqual(self.state()[0],self.ids[0])
    def test_concurrent_future(self):
        before=self.state(); self.assertEqual(self.concurrent(2),[403]*6); self.assertEqual(self.state(),before)


    def test_waiting_request_refreshes_stale_cursor(self):
        # A sessão da requisição já conhece um cursor antigo antes do bloqueio.
        # Outra transação avança o cursor enquanto mantém a trava; o POST deve
        # validar a versão atual, sem regredir para o primeiro módulo.
        waiting = Event()
        with self.app.app_context():
            engine = db.engine
        connection = engine.connect()
        transaction = connection.begin()
        connection.execute(db.select(ParticipacaoTreinamento.id).where(
            ParticipacaoTreinamento.id == self.pid).with_for_update())
        def before_execute(conn, cursor, statement, params, context, many):
            if 'FOR UPDATE' in statement and 'participacoes_treinamento.token' in statement:
                waiting.set()
        event.listen(engine, 'before_cursor_execute', before_execute)
        def worker():
            with self.app.app_context():
                stale = db.session.get(ParticipacaoTreinamento, self.pid)
                self.assertIsNone(stale.ultimo_modulo_id)
                client = self.app.test_client()
                headers = {'X-CSRFToken': client.get('/_csrf').json['token']}
                return client.post(f'/aprender/t/principal/modulo/{self.ids[0]}/avancar', headers=headers).status_code
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(worker)
            self.assertTrue(waiting.wait(timeout=10))
            connection.execute(update(ParticipacaoTreinamento).where(
                ParticipacaoTreinamento.id == self.pid).values(ultimo_modulo_id=self.ids[1]))
            transaction.commit()
            self.assertEqual(future.result(timeout=10), 302)
            self.assertEqual(self.state()[0], self.ids[1])
            self.assertIsNone(self.state()[1])
        finally:
            if transaction.is_active:
                transaction.rollback()
            connection.close()
            pool.shutdown(wait=True)
            event.remove(engine, 'before_cursor_execute', before_execute)

if __name__=='__main__': unittest.main()
