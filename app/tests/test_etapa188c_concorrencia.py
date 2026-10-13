"""Idempotência do início: sessões independentes e locks reais no PostgreSQL."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier,Lock
from sqlalchemy import event
from app.extensions import db
# Mesma inicialização das dependências usada pelo factory da aplicação.
import app.simulacoes.preparacao
import test_portal_treinamento_progresso as fixture


class InicioPostgresTest(unittest.TestCase):
    postgres=True
    setUpClass=fixture.ProgressoTest.__dict__['setUpClass']
    tearDownClass=fixture.ProgressoTest.__dict__['tearDownClass']
    setUp=fixture.ProgressoTest.setUp
    state=fixture.ProgressoTest.state

    def start(self):
        client=self.app.test_client()
        headers={'X-CSRFToken':client.get('/_csrf').json['token']}
        return client.post('/aprender/t/principal/iniciar',headers=headers).status_code

    def test_two_starts_write_initial_timestamp_once(self):
        with self.app.app_context():engine=db.engine
        before_read=Barrier(2);after_unlocked_read=Barrier(2);mutex=Lock();writes=[]
        def initial_query(sql):
            return 'FROM participacoes_treinamento' in sql and 'participacoes_treinamento.token =' in sql
        def before(conn,cursor,sql,params,ctx,many):
            if initial_query(sql):before_read.wait(timeout=15)
            if sql.startswith('UPDATE participacoes_treinamento SET iniciado_em'):
                with mutex:writes.append(params['iniciado_em'])
        def after(conn,cursor,sql,params,ctx,many):
            # No código vulnerável, fixa ambas as leituras antigas antes da escrita.
            # FOR UPDATE serializa a segunda leitura no código corrigido.
            if initial_query(sql) and 'FOR UPDATE' not in sql:after_unlocked_read.wait(timeout=15)
        event.listen(engine,'before_cursor_execute',before);event.listen(engine,'after_cursor_execute',after)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                fs=[pool.submit(self.start) for _ in range(2)]
                self.assertEqual([f.result(timeout=20) for f in fs],[302,302])
        finally:
            event.remove(engine,'before_cursor_execute',before);event.remove(engine,'after_cursor_execute',after)
        self.assertEqual(len(writes),1)
        self.assertEqual(self.state()[1],writes[0])

    def test_repeat_start_preserves_date_and_cursor(self):
        self.assertEqual(self.start(),302);before=self.state()
        self.assertEqual(self.start(),302);self.assertEqual(self.state(),before)

    def test_start_and_first_advance_preserve_single_start(self):
        gate=Barrier(2)
        def advance():
            client=self.app.test_client();h={'X-CSRFToken':client.get('/_csrf').json['token']}
            gate.wait(timeout=15)
            return client.post(f'/aprender/t/principal/modulo/{self.ids[0]}/avancar',headers=h).status_code
        def start():gate.wait(timeout=15);return self.start()
        with ThreadPoolExecutor(max_workers=2) as pool:
            fs=[pool.submit(start),pool.submit(advance)]
            self.assertEqual([f.result(timeout=20) for f in fs],[302,302])
        self.assertEqual(self.state()[0],self.ids[0]);self.assertIsNotNone(self.state()[1]);before=self.state()
        self.start();self.assertEqual(self.state(),before)


class HeartbeatEstruturaPostgresTest(unittest.TestCase):
    postgres=True
    setUpClass=fixture.ProgressoTest.__dict__['setUpClass']
    tearDownClass=fixture.ProgressoTest.__dict__['tearDownClass']
    setUp=fixture.ProgressoTest.setUp
    state=fixture.ProgressoTest.state

    def test_reordering_while_heartbeat_waits_does_not_count_future_module(self):
        from threading import Event
        from datetime import timedelta
        from sqlalchemy import select
        from app.models import ParticipacaoTreinamento as P,TempoTreinamento as T,ModuloTreinamento as M
        from app.simulacoes.snapshots import agora
        with self.app.app_context():
            engine=db.engine
            tempo=T.query.filter_by(participacao_id=self.pid,modulo_id=self.ids[0]).one()
            tempo.segundos_ativos=0;tempo.ultima_atividade_em=agora()-timedelta(seconds=30);db.session.commit()
        waiting=Event()
        def observe(conn,cursor,sql,params,ctx,many):
            if 'FROM participacoes_treinamento' in sql and 'FOR UPDATE' in sql:waiting.set()
        def beat():
            c=self.app.test_client();h={'X-CSRFToken':c.get('/_csrf').json['token']}
            return c.post(f'/aprender/t/principal/modulo/{self.ids[0]}/heartbeat',headers=h,json={'segundos':30}).status_code
        event.listen(engine,'before_cursor_execute',observe)
        conn=engine.connect();txn=conn.begin();conn.execute(select(P.id).where(P.id==self.pid).with_for_update())
        waiting.clear()  # Ignora o lock da conexão controladora; espera o worker.
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                f=pool.submit(beat);self.assertTrue(waiting.wait(timeout=15))
                with self.app.app_context():db.session.get(M,self.ids[0]).ordem=50;db.session.commit()
                txn.commit();self.assertEqual(f.result(timeout=20),403)
        finally:
            if txn.is_active:txn.rollback()
            conn.close();event.remove(engine,'before_cursor_execute',observe)
        self.assertEqual(self.state()[3],[(self.ids[0],0)])
