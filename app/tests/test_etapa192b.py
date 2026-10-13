"""Congelamento do quiz: ORM, HTTP, integridade e locks PostgreSQL reais."""
import unittest
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from sqlalchemy import event, update, delete
import app.simulacoes.preparacao
from app.extensions import db
from app.models import Questao, Alternativa, ParticipacaoTreinamento, Treinamento
from app.utils.estrutura_treinamento import AvaliacaoTreinamentoBloqueada
import test_treinamento_estabilidade as fixture


class AvaliacaoTest(unittest.TestCase):
    postgres = False
    setUpClass = fixture.EstabilidadeTest.__dict__['setUpClass']
    tearDownClass = fixture.fixture.ProgressoTest.__dict__['tearDownClass']
    request = fixture.EstabilidadeTest.request
    state = fixture.EstabilidadeTest.state
    start = fixture.EstabilidadeTest.start

    def setUp(self):
        fixture.EstabilidadeTest.setUp(self)
        with self.app.app_context():
            q = Questao(treinamento_id=self.tid, enunciado='Original', ordem=1)
            db.session.add(q); db.session.flush()
            opts = [Alternativa(questao_id=q.id, texto=str(i), correta=i == 1) for i in range(1, 5)]
            db.session.add_all(opts); db.session.commit()
            self.qid = q.id; self.aids = [a.id for a in opts]
        self.new_url = f'/treinamentos/{self.tid}/questoes/nova'
        self.edit_quiz_url = f'/treinamentos/questoes/{self.qid}/editar'
        self.data = dict(enunciado='Alterada', ordem='2', correta='2',
                         **{f'alternativa_{i}': f'Texto{i}' for i in range(1, 5)})

    def snapshot(self):
        with self.app.app_context():
            return ([(q.id, q.treinamento_id, q.enunciado, q.ordem) for q in Questao.query.order_by(Questao.id)],
                    [(a.id, a.questao_id, a.texto, a.correta) for a in Alternativa.query.order_by(Alternativa.id)])

    def mutate(self, op):
        q = db.session.get(Questao, self.qid)
        a = db.session.get(Alternativa, self.aids[0])
        if op == 'create': db.session.add(Questao(treinamento_id=self.tid, enunciado='Nova', ordem=3))
        elif op == 'delete': db.session.delete(q)
        elif op == 'statement': q.enunciado = 'Novo'
        elif op == 'order': q.ordem = 99
        elif op == 'id': q.id = 999
        elif op == 'answer': a.correta = False
        elif op == 'text': a.texto = 'Novo'
        elif op == 'add_option': db.session.add(Alternativa(questao_id=q.id, texto='Extra', correta=False))
        elif op == 'delete_option': db.session.delete(a)
        elif op == 'orphan_option': q.alternativas.remove(a)
        elif op == 'orphan_question': q.treinamento.questoes.remove(q)
        elif op == 'move_question': q.treinamento = db.session.get(Treinamento, 2)
        elif op == 'move_option': a.questao = Questao.query.filter(Questao.treinamento_id != self.tid).first()

    def test_created_participation_allows_http_create_and_edit(self):
        self.assertIsNone(self.state()[1])
        for url in (self.new_url, self.edit_quiz_url):
            self.assertEqual(self.client.post(url, data=self.data, headers=self.headers).status_code, 302)
        self.assertEqual(len(self.snapshot()[0]), 2)
        self.assertEqual(self.snapshot()[0][0][2:], ('Alterada', 2))
        self.assertIsNone(self.state()[1])

    def test_before_start_option_changes_and_deletes(self):
        for op in ('answer', 'text', 'order', 'add_option', 'delete_option', 'delete'):
            with self.app.app_context():
                self.mutate(op); db.session.commit()
        self.assertEqual(self.snapshot(), ([], []))

    def test_every_structural_operation_blocked_and_atomic(self):
        self.start(); before = self.snapshot()
        for op in ('create', 'delete', 'statement', 'order', 'id', 'answer', 'text',
                   'add_option', 'delete_option', 'orphan_option', 'orphan_question', 'move_question'):
            with self.subTest(op=op), self.app.app_context():
                self.mutate(op)
                with self.assertRaises(AvaliacaoTreinamentoBloqueada): db.session.commit()
                db.session.rollback()
            self.assertEqual(self.snapshot(), before)

    def test_http_blocked_get_post_with_message(self):
        self.start(); before = self.snapshot()
        for url in (self.new_url, self.edit_quiz_url):
            for method in ('GET', 'POST'):
                self.assertEqual(self.client.open(url, method=method, data=self.data, headers=self.headers).status_code, 302)
                with self.client.session_transaction() as sess:
                    self.assertIn('Sua avaliação não pode mais ser alterada', sess['_flashes'][-1][1])
        self.assertEqual(self.snapshot(), before)

    def test_legacy_cursor_or_conclusion_freezes(self):
        for field, value in (('ultimo_modulo_id', self.ids[0]), ('concluido_em', datetime.now(timezone.utc))):
            with self.app.app_context():
                p = db.session.get(ParticipacaoTreinamento, self.pid)
                p.ultimo_modulo_id = None; p.concluido_em = None
                setattr(p, field, value); db.session.commit()
                self.mutate('answer')
                with self.assertRaises(AvaliacaoTreinamentoBloqueada): db.session.commit()
                db.session.rollback()

    def test_move_into_frozen_training(self):
        with self.app.app_context():
            q = Questao(treinamento_id=2, enunciado='Outra', ordem=1)
            db.session.add(q); db.session.commit(); foreign = q.id
        self.start(); before = self.snapshot()
        with self.app.app_context():
            db.session.get(Questao, foreign).treinamento_id = self.tid
            with self.assertRaises(AvaliacaoTreinamentoBloqueada): db.session.commit()
            db.session.rollback()
        self.assertEqual(self.snapshot(), before)

    def test_bulk_dml_cannot_bypass_guard(self):
        for statement in (update(Questao).values(enunciado='Bulk'), delete(Alternativa)):
            with self.app.app_context():
                with self.assertRaisesRegex(ValueError, 'em lote'): db.session.execute(statement)
                db.session.rollback()

    def test_start_and_edit_in_same_flush_blocked(self):
        before = self.snapshot()
        with self.app.app_context():
            with db.session.no_autoflush:
                db.session.get(ParticipacaoTreinamento, self.pid).iniciado_em = datetime.now(timezone.utc)
                self.mutate('answer')
            with self.assertRaises(AvaliacaoTreinamentoBloqueada): db.session.commit()
            db.session.rollback()
        self.assertEqual(self.snapshot(), before)

    def test_alternative_id_and_move_cannot_change_history(self):
        with self.app.app_context():
            q = Questao(treinamento_id=2, enunciado='Outra', ordem=1)
            db.session.add(q); db.session.commit()
        self.start(); before = self.snapshot()
        for operation in ('move_option', 'option_id'):
            with self.app.app_context():
                if operation == 'option_id': db.session.get(Alternativa, self.aids[0]).id = 999
                else: self.mutate(operation)
                with self.assertRaises(AvaliacaoTreinamentoBloqueada): db.session.commit()
                db.session.rollback()
            self.assertEqual(self.snapshot(), before)

    def test_expired_foreign_keys_preserve_frozen_origin(self):
        with self.app.app_context():
            q = Questao(treinamento_id=2, enunciado='Outra', ordem=1)
            db.session.add(q); db.session.commit(); foreign = q.id
        self.start(); before = self.snapshot()
        for kind in ('question', 'option'):
            with self.app.app_context():
                if kind == 'question':
                    q = db.session.get(Questao, self.qid)
                    db.session.expire(q, ['treinamento_id', 'treinamento'])
                    q.treinamento_id = 2
                else:
                    a = db.session.get(Alternativa, self.aids[0])
                    db.session.expire(a, ['questao_id', 'questao'])
                    a.questao_id = foreign
                with self.assertRaises(AvaliacaoTreinamentoBloqueada): db.session.commit()
                db.session.rollback()
            self.assertEqual(self.snapshot(), before)


class AvaliacaoPostgresTest(AvaliacaoTest):
    postgres = True

    def race(self, activity, operation, admin_first=False, http=False):
        identity = None
        if activity == 'quiz':
            response=self.client.post('/aprender/t/principal/quiz/preparar',headers=self.headers)
            self.assertEqual(response.status_code,303)
            from app.models import SubmissaoQuiz
            with self.app.app_context():identity=str(SubmissaoQuiz.query.one().id)
        locked, contender, release = Event(), Event(), Event()
        with self.app.app_context(): engine = db.engine
        portal_lock = 'FOR SHARE' if activity == 'quiz' else 'FOR KEY SHARE'
        target = 'FOR UPDATE' if admin_first else portal_lock
        other = portal_lock if admin_first else 'FOR UPDATE'
        def after(conn, cursor, sql, params, context, many):
            if 'FROM treinamentos' in sql and target in sql and not locked.is_set():
                locked.set()
                if not release.wait(15): raise TimeoutError('release')
        def before(conn, cursor, sql, params, context, many):
            if 'FROM treinamentos' in sql and other in sql: contender.set()
        def admin():
            if http:
                c = self.app.test_client()
                h = {'X-CSRFToken': c.get('/_csrf').json['token']}
                url = self.new_url if operation == 'create' else self.edit_quiz_url
                response = c.post(url, headers=h, data=self.data)
                self.assertEqual(response.status_code, 302)
                with c.session_transaction() as sess:
                    return 'blocked' if 'não pode mais ser alterada' in sess['_flashes'][-1][1] else 'saved'
            with self.app.app_context():
                try:
                    self.mutate(operation); db.session.commit(); return 'saved'
                except AvaliacaoTreinamentoBloqueada:
                    db.session.rollback(); return 'blocked'
        def portal():
            c = self.app.test_client(); h = {'X-CSRFToken': c.get('/_csrf').json['token']}
            url = {'start': '/aprender/t/principal/iniciar',
                   'advance': f'/aprender/t/principal/modulo/{self.ids[0]}/avancar',
                   'heartbeat': f'/aprender/t/principal/modulo/{self.ids[0]}/heartbeat',
                   'quiz': '/aprender/t/principal/quiz'}[activity]
            data={f'questao_{self.qid}': str(self.aids[0])}
            if identity:data['submissao_id']=identity
            return c.post(url, headers=h, data=data,
                          json=None).status_code
        event.listen(engine, 'before_cursor_execute', before)
        event.listen(engine, 'after_cursor_execute', after)
        pool = ThreadPoolExecutor(max_workers=2)
        try:
            f = pool.submit(admin if admin_first else portal)
            self.assertTrue(locked.wait(15))
            g = pool.submit(portal if admin_first else admin)
            self.assertTrue(contender.wait(15))
            self.assertFalse(g.done())
            release.set()
            first, second = f.result(20), g.result(20)
            return (first, second) if admin_first else (second, first)
        finally:
            release.set(); pool.shutdown(wait=True)
            event.remove(engine, 'before_cursor_execute', before)
            event.remove(engine, 'after_cursor_execute', after)

    def test_start_versus_create_statement_and_answer(self):
        for op in ('create', 'statement', 'answer'):
            self.setUp(); before = self.snapshot()
            self.assertEqual(self.race('start', op), ('blocked', 302))
            self.assertEqual(self.snapshot(), before)

    def test_first_advance_versus_edit(self):
        for activity in ('advance',):
            self.setUp(); before = self.snapshot()
            result = self.race(activity, 'statement')
            self.assertEqual(result[0], 'blocked')
            self.assertEqual(self.snapshot(), before)

    def test_legal_admin_finishes_before_start(self):
        self.assertEqual(self.race('start', 'answer', admin_first=True), ('saved', 302))
        self.assertFalse(self.snapshot()[1][0][3])
        before = self.snapshot()
        with self.app.app_context():
            self.mutate('statement')
            with self.assertRaises(AvaliacaoTreinamentoBloqueada): db.session.commit()
            db.session.rollback()
        self.assertEqual(self.snapshot(), before)

    def test_quiz_submission_versus_answer(self):
        for i in range(3): self.request(i)
        before = self.snapshot()
        self.assertEqual(self.race('quiz', 'answer'), ('blocked', 303))
        self.assertEqual(self.snapshot(), before)
        from app.models import ResultadoQuiz
        with self.app.app_context():
            self.assertEqual(ResultadoQuiz.query.one().total_acertos, 1)

    def test_admin_lock_before_quiz_is_rejected_then_quiz_scores_snapshot(self):
        for i in range(3): self.request(i)
        before = self.snapshot()
        self.assertEqual(self.race('quiz', 'answer', admin_first=True), ('blocked', 303))
        self.assertEqual(self.snapshot(), before)

    def test_http_race_rejection_from_flush_is_controlled(self):
        for op in ('create', 'statement'):
            self.setUp(); before = self.snapshot()
            self.assertEqual(self.race('start', op, http=True), ('blocked', 302))
            self.assertEqual(self.snapshot(), before)

    def test_concurrent_reassociation_cannot_hide_new_frozen_origin(self):
        from threading import current_thread
        from unittest.mock import patch
        import app.utils.estrutura_treinamento as guard
        ready, release = Event(), Event()
        original = guard._avaliacoes_alteradas
        paused = False
        def inspect_quiz(session):
            nonlocal paused
            ids = original(session)
            if current_thread().name.startswith('stalequiz') and not paused:
                paused = True; ready.set()
                if not release.wait(15): raise TimeoutError('release')
            return ids
        def stale_editor():
            with self.app.app_context():
                db.session.get(Questao, self.qid).treinamento_id = 2
                try:
                    db.session.commit(); return 'saved'
                except AvaliacaoTreinamentoBloqueada:
                    db.session.rollback(); return 'blocked'
        with patch.object(guard, '_avaliacoes_alteradas', inspect_quiz):
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix='stalequiz') as pool:
                worker = pool.submit(stale_editor)
                try:
                    self.assertTrue(ready.wait(15))
                    with self.app.app_context():
                        db.session.get(Questao, self.qid).treinamento_id = 3
                        db.session.commit()
                        ParticipacaoTreinamento.query.filter_by(token='vazio').one().iniciado_em = datetime.now(timezone.utc)
                        db.session.commit()
                    release.set(); self.assertEqual(worker.result(20), 'blocked')
                finally:
                    release.set()
        self.assertEqual(self.snapshot()[0][0][1], 3)
